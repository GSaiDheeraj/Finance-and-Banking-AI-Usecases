"""
Deterministic alert engine.

NO LLM. Turns the deterministic facts (drift breaches, position/sector concentration,
risk-limit breaches, drawdown, cash-flow variances, emerging-risk findings) into a single
prioritised list of `Alert`s — each with a fixed severity and a `recommended_action`. Because
the rules and thresholds are constants, the same inputs always raise the same alerts (NFR-1).

The emerging-risk findings come from the (LLM-classified) news scan, but their fold-in here
— which findings become alerts and at what severity — is decided by Python, applying a
credibility discount so public news never outranks a hard limit breach.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .logconf import get_logger
from .reference import risk_limits
from .schemas import (
    Alert,
    AlertType,
    AllocationBreakdown,
    CashFlowReport,
    CountryResearchProfile,
    DriftReport,
    DriftStatus,
    MarketRiskScan,
    RebalancePlan,
    RiskLimitCheck,
    RiskMetrics,
    Severity,
)

_log = get_logger()

_SEV_RANK = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
             Severity.HIGH: 3, Severity.CRITICAL: 4}
_LIMIT_SEVERITY = {"breach": Severity.HIGH, "warn": Severity.MEDIUM,
                   "ok": Severity.INFO, "not_available": Severity.INFO}
_NEWS_SEVERITY = {"low": Severity.LOW, "medium": Severity.MEDIUM, "high": Severity.HIGH}


def build_alerts(
    drift: DriftReport,
    rebalance: RebalancePlan,
    allocation: AllocationBreakdown,
    limit_checks: List[RiskLimitCheck],
    cashflow: CashFlowReport,
    risk_metrics: RiskMetrics,
    risk_scan: Optional[MarketRiskScan] = None,
    country_research: Optional[Dict[str, CountryResearchProfile]] = None,
) -> List[Alert]:
    """Assemble the full prioritised alert list."""
    alerts: List[Alert] = []
    limits = risk_limits()

    # 1. rebalancing alerts (one per breached sleeve, with its trade)
    trade_by_bucket = {t.bucket: t for t in rebalance.trades}
    for line in drift.lines:
        if line.status in (DriftStatus.BREACH_OVER, DriftStatus.BREACH_UNDER):
            t = trade_by_bucket.get(line.bucket)
            action = (f"{t.action.title()} {abs(t.trade_value):,.0f} of {line.bucket} "
                      f"to return to the {line.target_weight:.0%} target." if t
                      else "Rebalance toward target.")
            alerts.append(Alert(
                type=AlertType.REBALANCE, severity=Severity.HIGH,
                title=f"Allocation breach: {line.bucket} {line.actual_weight:.0%} "
                      f"(target {line.target_weight:.0%})",
                detail=line.rationale,
                evidence=[f"actual={line.actual_weight:.1%}", f"target={line.target_weight:.1%}",
                          f"band={line.lower_band:.1%}–{line.upper_band:.1%}"],
                recommended_action=action, symbols=[],
            ))

    # 2. concentration: single-name cap
    name_cap = limits.get("single_name_max_weight", 0.10)
    for b in allocation.by_position:
        if b.weight > name_cap:
            alerts.append(Alert(
                type=AlertType.CONCENTRATION,
                severity=Severity.HIGH if b.weight > 1.5 * name_cap else Severity.MEDIUM,
                title=f"Single-name concentration: {b.name} at {b.weight:.0%}",
                detail=f"{b.name} is {b.weight:.1%} of the book, above the {name_cap:.0%} "
                       f"single-name limit.",
                evidence=[f"weight={b.weight:.1%}", f"limit={name_cap:.0%}",
                          f"value={b.market_value:,.0f}"],
                recommended_action=f"Trim {b.name} to ≤{name_cap:.0%} to reduce idiosyncratic risk.",
                symbols=[b.name],
            ))

    # 3. concentration: sector cap
    sec_cap = limits.get("sector_max_weight", 0.30)
    for b in allocation.by_sector:
        if b.weight > sec_cap and b.name not in ("Unclassified",):
            alerts.append(Alert(
                type=AlertType.CONCENTRATION,
                severity=Severity.MEDIUM,
                title=f"Sector concentration: {b.name} at {b.weight:.0%}",
                detail=f"Sector '{b.name}' is {b.weight:.1%} of the book, above the "
                       f"{sec_cap:.0%} sector limit.",
                evidence=[f"weight={b.weight:.1%}", f"limit={sec_cap:.0%}"],
                recommended_action=f"Diversify away from '{b.name}' toward the {sec_cap:.0%} cap.",
                symbols=[],
            ))

    # 4. risk-limit checks (vol / VaR / TE / drawdown / cash)
    for c in limit_checks:
        if c.status in ("breach", "warn"):
            sev = _LIMIT_SEVERITY.get(c.status, Severity.INFO)
            atype = AlertType.DRAWDOWN if c.name == "max_drawdown_alert" else AlertType.RISK_LIMIT
            alerts.append(Alert(
                type=atype, severity=sev,
                title=f"Risk limit {c.status}: {c.name.replace('_', ' ')}",
                detail=c.detail,
                evidence=[f"observed={c.observed}", f"limit={c.limit}"],
                recommended_action=_limit_action(c),
                symbols=[],
            ))

    # 5. cash-flow variance flags
    for v in cashflow.variances:
        if v.status != "on_track" and _SEV_RANK.get(v.severity, 0) >= _SEV_RANK[Severity.MEDIUM]:
            alerts.append(Alert(
                type=AlertType.CASHFLOW_VARIANCE, severity=v.severity,
                title=f"Cash-flow variance ({v.status}): {v.symbol or v.type.value}",
                detail=v.note,
                evidence=[f"expected={v.expected:,.0f}", f"actual={v.actual:,.0f}",
                          f"variance={v.variance:,.0f}"],
                recommended_action=_cashflow_action(v),
                symbols=[v.symbol] if v.symbol else [],
            ))

    # 6. data-quality alert when pricing/coverage is poor
    if allocation.unpriced_symbols:
        alerts.append(Alert(
            type=AlertType.DATA_QUALITY, severity=Severity.LOW,
            title=f"{len(allocation.unpriced_symbols)} holding(s) could not be priced",
            detail="These positions are excluded from allocation/risk until priced.",
            evidence=allocation.unpriced_symbols,
            recommended_action="Supply a price or a resolvable ticker for the listed symbols.",
            symbols=allocation.unpriced_symbols,
        ))

    # 7. emerging-risk findings (LLM-classified, Python-discounted)
    if risk_scan:
        for f in risk_scan.findings:
            sev = _NEWS_SEVERITY.get((f.severity or "medium").lower(), Severity.MEDIUM)
            # credibility discount: a single public-news item caps at medium
            if sev == Severity.HIGH:
                sev = Severity.MEDIUM
            alerts.append(Alert(
                type=AlertType.EMERGING_RISK, severity=sev,
                title=f"Emerging risk ({f.category.value}): {f.subject}",
                detail=f.summary,
                evidence=[f.url] if f.url else [],
                recommended_action="Review exposure to the named risk; corroborate before acting.",
                symbols=[f.subject] if f.scope == "position" else [],
            ))

    # 8. country research (geopolitical/sectoral/bond/market — LLM-classified, Python-
    # discounted, same credibility clamp as block 7). Reuses AlertType.EMERGING_RISK rather
    # than a new type — this is the same "outside risk signal" concern, just about a held
    # country instead of a held security or a portfolio-wide macro read.
    for country, profile in (country_research or {}).items():
        for f in profile.findings:
            sev = _NEWS_SEVERITY.get((f.severity or "medium").lower(), Severity.MEDIUM)
            if sev == Severity.HIGH:
                sev = Severity.MEDIUM
            alerts.append(Alert(
                type=AlertType.EMERGING_RISK, severity=sev,
                title=f"Country risk ({f.category.value}): {country}",
                detail=f.summary,
                evidence=[f.url] if f.url else [],
                recommended_action="Review exposure to this country; corroborate before acting.",
                symbols=[],
            ))

    alerts.sort(key=lambda a: _SEV_RANK.get(a.severity, 0), reverse=True)
    _log.info("alerts: raised %d (high+=%d)", len(alerts),
              sum(1 for a in alerts if _SEV_RANK.get(a.severity, 0) >= 3))
    return alerts


def _limit_action(c: RiskLimitCheck) -> str:
    name = c.name
    if name == "max_volatility_annual":
        return "Reduce risk assets / add duration or cash to bring volatility under the ceiling."
    if name == "var_95_1d_limit_pct":
        return "De-risk the largest contributors to lower 1-day VaR under the limit."
    if name == "max_drawdown_alert":
        return "Review the drawdown drivers; consider hedges or a defensive tilt."
    if name == "max_tracking_error":
        return "Move active positions toward benchmark weights to cut tracking error."
    if name == "min_cash_weight":
        return "Raise cash to meet the minimum liquidity buffer."
    return "Bring the metric back within its limit."


def _cashflow_action(v) -> str:
    if v.status == "missing":
        return f"Confirm whether a distribution for {v.symbol} is due or delayed."
    if v.status == "shortfall":
        return f"Investigate the income shortfall on {v.symbol} (cut, timing, or data gap)."
    if v.status == "unexpected":
        return "Verify this flow is authorised and correctly booked."
    return "Reconcile against the custodian statement."

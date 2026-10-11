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

_SEV_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}
_LIMIT_SEVERITY = {
    "breach": Severity.HIGH,
    "warn": Severity.MEDIUM,
    "ok": Severity.INFO,
    "not_available": Severity.INFO,
}
_NEWS_SEVERITY = {"low": Severity.LOW, "medium": Severity.MEDIUM, "high": Severity.HIGH}


def build_alerts(
    drift: DriftReport,
    rebalance: RebalancePlan,
    allocation: AllocationBreakdown,
    limit_checks: list[RiskLimitCheck],
    cashflow: CashFlowReport,
    risk_metrics: RiskMetrics,
    risk_scan: MarketRiskScan | None = None,
    country_research: dict[str, CountryResearchProfile] | None = None,
) -> list[Alert]:
    """Assemble the full prioritised alert list."""
    alerts: list[Alert] = []
    limits = risk_limits()

    # 1. rebalancing alerts (one per breached sleeve, with its trade)
    trade_by_bucket = {t.bucket: t for t in rebalance.trades}
    for line in drift.lines:
        if line.status in (DriftStatus.BREACH_OVER, DriftStatus.BREACH_UNDER):
            trade = trade_by_bucket.get(line.bucket)
            action = (
                f"{trade.action.title()} {abs(trade.trade_value):,.0f} of {line.bucket} "
                f"to return to the {line.target_weight:.0%} target."
                if trade
                else "Rebalance toward target."
            )
            alerts.append(
                Alert(
                    type=AlertType.REBALANCE,
                    severity=Severity.HIGH,
                    title=f"Allocation breach: {line.bucket} {line.actual_weight:.0%} "
                    f"(target {line.target_weight:.0%})",
                    detail=line.rationale,
                    evidence=[
                        f"actual={line.actual_weight:.1%}",
                        f"target={line.target_weight:.1%}",
                        f"band={line.lower_band:.1%}–{line.upper_band:.1%}",
                    ],
                    recommended_action=action,
                    symbols=[],
                )
            )

    # 2. concentration: single-name cap
    name_cap = limits.get("single_name_max_weight", 0.10)
    for position in allocation.by_position:
        if position.weight > name_cap:
            alerts.append(
                Alert(
                    type=AlertType.CONCENTRATION,
                    severity=(
                        Severity.HIGH if position.weight > 1.5 * name_cap else Severity.MEDIUM
                    ),
                    title=(f"Single-name concentration: {position.name} at {position.weight:.0%}"),
                    detail=(
                        f"{position.name} is {position.weight:.1%} of the book, "
                        f"above the {name_cap:.0%} single-name limit."
                    ),
                    evidence=[
                        f"weight={position.weight:.1%}",
                        f"limit={name_cap:.0%}",
                        f"value={position.market_value:,.0f}",
                    ],
                    recommended_action=(
                        f"Trim {position.name} to ≤{name_cap:.0%} to reduce idiosyncratic risk."
                    ),
                    symbols=[position.name],
                )
            )

    # 3. concentration: sector cap
    sec_cap = limits.get("sector_max_weight", 0.30)
    for sector in allocation.by_sector:
        if sector.weight > sec_cap and sector.name not in ("Unclassified",):
            alerts.append(
                Alert(
                    type=AlertType.CONCENTRATION,
                    severity=Severity.MEDIUM,
                    title=f"Sector concentration: {sector.name} at {sector.weight:.0%}",
                    detail=f"Sector '{sector.name}' is {sector.weight:.1%} of the book, above the "
                    f"{sec_cap:.0%} sector limit.",
                    evidence=[f"weight={sector.weight:.1%}", f"limit={sec_cap:.0%}"],
                    recommended_action=(
                        f"Diversify away from '{sector.name}' toward the {sec_cap:.0%} cap."
                    ),
                    symbols=[],
                )
            )

    # 4. risk-limit checks (vol / VaR / TE / drawdown / cash)
    for check in limit_checks:
        if check.status in ("breach", "warn"):
            sev = _LIMIT_SEVERITY.get(check.status, Severity.INFO)
            atype = (
                AlertType.DRAWDOWN if check.name == "max_drawdown_alert" else AlertType.RISK_LIMIT
            )
            alerts.append(
                Alert(
                    type=atype,
                    severity=sev,
                    title=f"Risk limit {check.status}: {check.name.replace('_', ' ')}",
                    detail=check.detail,
                    evidence=[f"observed={check.observed}", f"limit={check.limit}"],
                    recommended_action=_limit_action(check),
                    symbols=[],
                )
            )

    # 5. cash-flow variance flags
    for variance in cashflow.variances:
        if (
            variance.status != "on_track"
            and _SEV_RANK.get(variance.severity, 0) >= _SEV_RANK[Severity.MEDIUM]
        ):
            alerts.append(
                Alert(
                    type=AlertType.CASHFLOW_VARIANCE,
                    severity=variance.severity,
                    title=f"Cash-flow variance ({variance.status}): "
                    f"{variance.symbol or variance.type.value}",
                    detail=variance.note,
                    evidence=[
                        f"expected={variance.expected:,.0f}",
                        f"actual={variance.actual:,.0f}",
                        f"variance={variance.variance:,.0f}",
                    ],
                    recommended_action=_cashflow_action(variance),
                    symbols=[variance.symbol] if variance.symbol else [],
                )
            )

    # 6. data-quality alert when pricing/coverage is poor
    if allocation.unpriced_symbols:
        alerts.append(
            Alert(
                type=AlertType.DATA_QUALITY,
                severity=Severity.LOW,
                title=f"{len(allocation.unpriced_symbols)} holding(s) could not be priced",
                detail="These positions are excluded from allocation/risk until priced.",
                evidence=allocation.unpriced_symbols,
                recommended_action="Supply a price or a resolvable ticker for the listed symbols.",
                symbols=allocation.unpriced_symbols,
            )
        )

    # 7. emerging-risk findings (LLM-classified, Python-discounted)
    if risk_scan:
        for finding in risk_scan.findings:
            sev = _NEWS_SEVERITY.get((finding.severity or "medium").lower(), Severity.MEDIUM)
            # credibility discount: a single public-news item caps at medium
            if sev == Severity.HIGH:
                sev = Severity.MEDIUM
            alerts.append(
                Alert(
                    type=AlertType.EMERGING_RISK,
                    severity=sev,
                    title=f"Emerging risk ({finding.category.value}): {finding.subject}",
                    detail=finding.summary,
                    evidence=[finding.url] if finding.url else [],
                    recommended_action=(
                        "Review exposure to the named risk; corroborate before acting."
                    ),
                    symbols=[finding.subject] if finding.scope == "position" else [],
                )
            )

    # 8. country research (geopolitical/sectoral/bond/market — LLM-classified, Python-
    # discounted, same credibility clamp as block 7). Reuses AlertType.EMERGING_RISK rather
    # than a new type — this is the same "outside risk signal" concern, just about a held
    # country instead of a held security or a portfolio-wide macro read.
    for country, profile in (country_research or {}).items():
        for finding in profile.findings:
            sev = _NEWS_SEVERITY.get((finding.severity or "medium").lower(), Severity.MEDIUM)
            if sev == Severity.HIGH:
                sev = Severity.MEDIUM
            alerts.append(
                Alert(
                    type=AlertType.EMERGING_RISK,
                    severity=sev,
                    title=f"Country risk ({finding.category.value}): {country}",
                    detail=finding.summary,
                    evidence=[finding.url] if finding.url else [],
                    recommended_action=(
                        "Review exposure to this country; corroborate before acting."
                    ),
                    symbols=[],
                )
            )

    alerts.sort(key=lambda alert: _SEV_RANK.get(alert.severity, 0), reverse=True)
    _log.info(
        "alerts: raised %d (high+=%d)",
        len(alerts),
        sum(1 for alert in alerts if _SEV_RANK.get(alert.severity, 0) >= 3),
    )
    return alerts


def _limit_action(check: RiskLimitCheck) -> str:
    name = check.name
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


def _cashflow_action(variance) -> str:
    if variance.status == "missing":
        return f"Confirm whether a distribution for {variance.symbol} is due or delayed."
    if variance.status == "shortfall":
        return f"Investigate the income shortfall on {variance.symbol} (cut, timing, or data gap)."
    if variance.status == "unexpected":
        return "Verify this flow is authorised and correctly booked."
    return "Reconcile against the custodian statement."

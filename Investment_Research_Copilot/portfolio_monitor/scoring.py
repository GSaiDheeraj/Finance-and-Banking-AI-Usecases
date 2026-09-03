"""
Deterministic portfolio risk score + health band.

NO LLM. Rolls the deterministic facts into a fixed-weight scorecard and a 0-100 risk score
(higher = riskier / less healthy), then maps it to a health band (healthy / watch / elevated
/ critical). Because the weights, severities and band cut-offs are constants, the same inputs
always yield the same score and band — the reproducibility guarantee (NFR-1).

This mirrors the credit-scorecard pattern used elsewhere in the book: each factor exposes its
weight, severity and contribution so the score is fully auditable.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .logconf import get_logger
from .reference import (
    FACTOR_WEIGHTS,
    SEVERITY_MULT,
    health_band_for_score,
    vol_ceiling,
    risk_limits,
)
from .schemas import (
    CashFlowReport,
    CountryResearchProfile,
    DriftReport,
    MarketRiskScan,
    PortfolioRiskScore,
    RiskFactorResult,
    RiskFactorType,
    RiskLimitCheck,
    RiskMetrics,
    Severity,
)

_log = get_logger()

_SEV_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_RANK_SEV = {0: "low", 1: "low", 2: "medium", 3: "high", 4: "critical"}


def _factor(ftype: RiskFactorType, severity: str, evidence: List[str]) -> RiskFactorResult:
    present = severity != "low"
    weight = FACTOR_WEIGHTS[ftype]
    mult = SEVERITY_MULT.get(severity, 0.0)
    return RiskFactorResult(
        factor=ftype, present=present, severity=severity, weight=weight,
        contribution=round(weight * mult, 2), evidence=evidence,
    )


def score_portfolio(
    drift: DriftReport,
    risk_metrics: RiskMetrics,
    limit_checks: List[RiskLimitCheck],
    cashflow: CashFlowReport,
    mandate: str,
    risk_scan: Optional[MarketRiskScan] = None,
    country_research: Optional[Dict[str, CountryResearchProfile]] = None,
) -> PortfolioRiskScore:
    """Build the portfolio risk scorecard, 0-100 score and health band deterministically."""
    factors: List[RiskFactorResult] = []

    # --- allocation drift ---
    if drift.n_breaches >= 2:
        sev = "high"
    elif drift.n_breaches == 1:
        sev = "medium"
    elif drift.active_share > 0.10:
        sev = "medium"
    else:
        sev = "low"
    factors.append(_factor(
        RiskFactorType.ALLOCATION_DRIFT, sev,
        [f"{drift.n_breaches} sleeve breach(es)", f"active share {drift.active_share:.1%}"]))

    # --- concentration (HHI + top1) ---
    hhi = risk_metrics.concentration_hhi
    top1 = risk_metrics.top1_weight or 0.0
    name_cap = risk_limits().get("single_name_max_weight", 0.10)
    if (hhi is not None and hhi >= 0.20) or top1 > 1.5 * name_cap:
        conc_sev = "high"
    elif (hhi is not None and hhi >= 0.12) or top1 > name_cap:
        conc_sev = "medium"
    else:
        conc_sev = "low"
    factors.append(_factor(
        RiskFactorType.CONCENTRATION, conc_sev,
        [f"HHI={hhi}", f"top1={top1:.1%}", f"effective_n={risk_metrics.effective_n}"]))

    # --- volatility vs mandate ceiling ---
    vol = risk_metrics.volatility_annual
    ceiling = vol_ceiling(mandate)
    if vol is None:
        vol_sev = "low"
    elif vol > 1.25 * ceiling:
        vol_sev = "high"
    elif vol > ceiling:
        vol_sev = "medium"
    else:
        vol_sev = "low"
    factors.append(_factor(
        RiskFactorType.VOLATILITY, vol_sev,
        [f"vol={vol:.1%}" if vol is not None else "vol n/a", f"ceiling={ceiling:.1%}"]))

    # --- drawdown ---
    dd = abs(risk_metrics.max_drawdown) if risk_metrics.max_drawdown is not None else None
    dd_limit = risk_limits().get("max_drawdown_alert", 0.20)
    if dd is None:
        dd_sev = "low"
    elif dd > 1.5 * dd_limit:
        dd_sev = "high"
    elif dd > dd_limit:
        dd_sev = "medium"
    else:
        dd_sev = "low"
    factors.append(_factor(
        RiskFactorType.DRAWDOWN, dd_sev,
        [f"max_drawdown={risk_metrics.max_drawdown:.1%}" if dd is not None else "drawdown n/a"]))

    # --- cash-flow variance ---
    cf_flags = [v for v in cashflow.variances if v.status != "on_track"]
    cf_high = sum(1 for v in cf_flags if v.severity in (Severity.HIGH, Severity.CRITICAL))
    if cf_high >= 1:
        cf_sev = "medium"
    elif len(cf_flags) >= 3:
        cf_sev = "medium"
    elif cf_flags:
        cf_sev = "low" if len(cf_flags) == 1 else "medium"
    else:
        cf_sev = "low"
    factors.append(_factor(
        RiskFactorType.CASHFLOW_VARIANCE, cf_sev,
        [f"{len(cf_flags)} variance flag(s)", f"{cf_high} high-severity"]))

    # --- risk-limit breaches (the hardest signal) ---
    breaches = [c for c in limit_checks if c.status == "breach"]
    warns = [c for c in limit_checks if c.status == "warn"]
    if len(breaches) >= 2:
        lim_sev = "critical"
    elif breaches:
        lim_sev = "high"
    elif warns:
        lim_sev = "medium"
    else:
        lim_sev = "low"
    factors.append(_factor(
        RiskFactorType.RISK_LIMIT_BREACH, lim_sev,
        [f"{len(breaches)} breach(es)", f"{len(warns)} warning(s)"]
        + [c.name for c in breaches]))

    # --- emerging risk (news, discounted) ---
    em_sev = "low"
    if risk_scan:
        tone = (risk_scan.overall_risk_tone or "normal").lower()
        n_high = sum(1 for f in risk_scan.findings if (f.severity or "").lower() == "high")
        if tone == "stressed" or n_high >= 2:
            em_sev = "medium"        # capped at medium — public news never dominates
        elif tone == "elevated" or risk_scan.findings:
            em_sev = "low" if not risk_scan.findings else "medium"
    factors.append(_factor(
        RiskFactorType.EMERGING_RISK, em_sev,
        [f"tone={risk_scan.overall_risk_tone}" if risk_scan else "no scan",
         f"{len(risk_scan.findings) if risk_scan else 0} finding(s)"]))

    # --- country research (geopolitical/sectoral/bond/market — discounted, same as above) ---
    cr_sev = "low"
    cr_findings = [f for p in (country_research or {}).values() for f in p.findings]
    if country_research:
        n_high = sum(1 for f in cr_findings if (f.severity or "").lower() == "high")
        tones = {(p.overall_risk_tone or "normal").lower() for p in country_research.values()}
        if "stressed" in tones or n_high >= 2:
            cr_sev = "medium"           # capped at medium — same credibility discount
        elif "elevated" in tones or cr_findings:
            cr_sev = "medium"
    factors.append(_factor(
        RiskFactorType.COUNTRY_RISK, cr_sev,
        [f"{len(country_research)} country(ies) researched" if country_research else "no research",
         f"{len(cr_findings)} finding(s)"]))

    raw = round(sum(f.contribution for f in factors), 2)
    normalized = round(min(100.0, raw), 2)
    band = health_band_for_score(normalized)

    summary = _summary(band, drift, breaches, risk_metrics)
    _log.info("score: raw=%.1f normalized=%.1f band=%s", raw, normalized, band.value)
    return PortfolioRiskScore(
        factors=factors, raw_score=raw, normalized_score=normalized,
        health_band=band, summary=summary,
    )


def _summary(band, drift, breaches, metrics) -> str:
    bits = [f"Portfolio health: {band.value.upper()}."]
    if drift.n_breaches:
        bits.append(f"{drift.n_breaches} allocation sleeve(s) outside tolerance.")
    if breaches:
        bits.append(f"{len(breaches)} risk limit(s) breached.")
    if metrics.volatility_annual is not None:
        bits.append(f"Annualised volatility {metrics.volatility_annual:.1%}.")
    if not drift.n_breaches and not breaches:
        bits.append("No allocation or risk-limit breaches.")
    return " ".join(bits)

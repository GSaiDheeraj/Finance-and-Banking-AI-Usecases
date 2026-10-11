"""
Deterministic portfolio risk score and health band.

No LLM. Rolls the deterministic facts into a fixed-weight scorecard and a
0-100 risk score (higher means riskier / less healthy), then maps it to a
health band. Weights, severities, and band cut-offs are constants, so the
same inputs always yield the same score and band.
"""

from __future__ import annotations

from .logconf import get_logger
from .reference import (
    FACTOR_WEIGHTS,
    SEVERITY_MULT,
    health_band_for_score,
    risk_limits,
    vol_ceiling,
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

HIGH_CONCENTRATION_HHI = 0.20
MEDIUM_CONCENTRATION_HHI = 0.12
ACTIVE_SHARE_MEDIUM_THRESHOLD = 0.10
VOL_HIGH_MULTIPLE = 1.25
DRAWDOWN_HIGH_MULTIPLE = 1.5
MAX_SCORE = 100.0
HIGH_SEVERITY_CASHFLOW_FLAGS = 3


def _build_factor(
    factor_type: RiskFactorType, severity: Severity, evidence: list[str]
) -> RiskFactorResult:
    weight = FACTOR_WEIGHTS[factor_type]
    multiplier = SEVERITY_MULT.get(severity.value, 0.0)
    return RiskFactorResult(
        factor=factor_type,
        present=severity != Severity.LOW,
        severity=severity.value,
        weight=weight,
        contribution=round(weight * multiplier, 2),
        evidence=evidence,
    )


def _drift_severity(drift: DriftReport) -> Severity:
    if drift.n_breaches >= 2:
        return Severity.HIGH
    if drift.n_breaches == 1 or drift.active_share > ACTIVE_SHARE_MEDIUM_THRESHOLD:
        return Severity.MEDIUM
    return Severity.LOW


def _drift_factor(drift: DriftReport) -> RiskFactorResult:
    return _build_factor(
        RiskFactorType.ALLOCATION_DRIFT,
        _drift_severity(drift),
        [
            f"{drift.n_breaches} sleeve breach(es)",
            f"active share {drift.active_share:.1%}",
        ],
    )


def _concentration_severity(
    concentration_hhi: float | None, top1_weight: float | None, single_name_cap: float
) -> Severity:
    if (concentration_hhi is not None and concentration_hhi >= HIGH_CONCENTRATION_HHI) or (
        top1_weight is not None and top1_weight > DRAWDOWN_HIGH_MULTIPLE * single_name_cap
    ):
        return Severity.HIGH
    if (concentration_hhi is not None and concentration_hhi >= MEDIUM_CONCENTRATION_HHI) or (
        top1_weight is not None and top1_weight > single_name_cap
    ):
        return Severity.MEDIUM
    return Severity.LOW


def _concentration_factor(metrics: RiskMetrics) -> RiskFactorResult:
    single_name_cap = risk_limits().get("single_name_max_weight", 0.10)
    top1_weight = metrics.top1_weight or 0.0
    severity = _concentration_severity(
        metrics.concentration_hhi, metrics.top1_weight, single_name_cap
    )
    return _build_factor(
        RiskFactorType.CONCENTRATION,
        severity,
        [
            f"HHI={metrics.concentration_hhi}",
            f"top1={top1_weight:.1%}",
            f"effective_n={metrics.effective_n}",
        ],
    )


def _metric_severity(value: float | None, limit: float, high_multiple: float) -> Severity:
    """Shared None→LOW / >multiple×limit→HIGH / >limit→MEDIUM / else LOW ladder."""
    if value is None:
        return Severity.LOW
    if value > high_multiple * limit:
        return Severity.HIGH
    if value > limit:
        return Severity.MEDIUM
    return Severity.LOW


def _volatility_factor(metrics: RiskMetrics, mandate: str) -> RiskFactorResult:
    volatility = metrics.volatility_annual
    ceiling = vol_ceiling(mandate)
    severity = _metric_severity(volatility, ceiling, VOL_HIGH_MULTIPLE)
    volatility_evidence = f"vol={volatility:.1%}" if volatility is not None else "vol n/a"
    return _build_factor(
        RiskFactorType.VOLATILITY,
        severity,
        [volatility_evidence, f"ceiling={ceiling:.1%}"],
    )


def _drawdown_factor(metrics: RiskMetrics) -> RiskFactorResult:
    drawdown = abs(metrics.max_drawdown) if metrics.max_drawdown is not None else None
    drawdown_limit = risk_limits().get("max_drawdown_alert", 0.20)
    severity = _metric_severity(drawdown, drawdown_limit, DRAWDOWN_HIGH_MULTIPLE)
    drawdown_evidence = (
        f"max_drawdown={metrics.max_drawdown:.1%}" if drawdown is not None else "drawdown n/a"
    )
    return _build_factor(RiskFactorType.DRAWDOWN, severity, [drawdown_evidence])


def _off_track_variances(cashflow: CashFlowReport) -> list:
    """Cash-flow variances that are not on track."""
    return [variance for variance in cashflow.variances if variance.status != "on_track"]


def _n_high_severity(variances: list) -> int:
    """Count of high/critical severity variances."""
    return sum(
        1 for variance in variances if variance.severity in (Severity.HIGH, Severity.CRITICAL)
    )


def _cashflow_severity(cashflow: CashFlowReport) -> Severity:
    off_track = _off_track_variances(cashflow)
    high_severity = _n_high_severity(off_track)
    if high_severity >= 1 or len(off_track) >= HIGH_SEVERITY_CASHFLOW_FLAGS:
        return Severity.MEDIUM
    if len(off_track) == 1:
        return Severity.LOW
    if off_track:
        return Severity.MEDIUM
    return Severity.LOW


def _cashflow_factor(cashflow: CashFlowReport) -> RiskFactorResult:
    off_track = _off_track_variances(cashflow)
    high_severity = _n_high_severity(off_track)
    return _build_factor(
        RiskFactorType.CASHFLOW_VARIANCE,
        _cashflow_severity(cashflow),
        [f"{len(off_track)} variance flag(s)", f"{high_severity} high-severity"],
    )


def _limit_breach_factor(limit_checks: list[RiskLimitCheck]) -> RiskFactorResult:
    breaches = [check for check in limit_checks if check.status == "breach"]
    warnings = [check for check in limit_checks if check.status == "warn"]
    if len(breaches) >= 2:
        severity = Severity.CRITICAL
    elif breaches:
        severity = Severity.HIGH
    elif warnings:
        severity = Severity.MEDIUM
    else:
        severity = Severity.LOW
    return _build_factor(
        RiskFactorType.RISK_LIMIT_BREACH,
        severity,
        [f"{len(breaches)} breach(es)", f"{len(warnings)} warning(s)"]
        + [check.name for check in breaches],
    )


def _discounted_news_severity(is_stressed: bool, is_suspect: bool) -> Severity:
    """Credibility-discounted news severity: public news caps at medium, never dominates.

    `is_suspect` is True when there is any concrete reason for concern (elevated tone,
    high-severity findings, or any findings at all); without it the tone alone stays low.
    """
    if is_stressed or is_suspect:
        return Severity.MEDIUM
    return Severity.LOW


def _emerging_risk_factor(risk_scan: MarketRiskScan | None) -> RiskFactorResult:
    if risk_scan is None:
        return _build_factor(
            RiskFactorType.EMERGING_RISK, Severity.LOW, ["no scan", "0 finding(s)"]
        )
    tone = (risk_scan.overall_risk_tone or "normal").lower()
    n_high = sum(1 for finding in risk_scan.findings if (finding.severity or "").lower() == "high")
    # An "elevated" tone alone without concrete findings stays low.
    severity = _discounted_news_severity(
        tone == "stressed", n_high >= 2 or bool(risk_scan.findings)
    )
    return _build_factor(
        RiskFactorType.EMERGING_RISK,
        severity,
        [f"tone={risk_scan.overall_risk_tone}", f"{len(risk_scan.findings)} finding(s)"],
    )


def _country_risk_factor(
    country_research: dict[str, CountryResearchProfile] | None,
) -> RiskFactorResult:
    if not country_research:
        return _build_factor(
            RiskFactorType.COUNTRY_RISK, Severity.LOW, ["no research", "0 finding(s)"]
        )
    findings = [finding for profile in country_research.values() for finding in profile.findings]
    n_high = sum(1 for finding in findings if (finding.severity or "").lower() == "high")
    tones = {
        (profile.overall_risk_tone or "normal").lower() for profile in country_research.values()
    }
    # Same credibility discount as news: capped at medium.
    severity = _discounted_news_severity(
        "stressed" in tones, n_high >= 2 or "elevated" in tones or bool(findings)
    )
    return _build_factor(
        RiskFactorType.COUNTRY_RISK,
        severity,
        [
            f"{len(country_research)} country(ies) researched",
            f"{len(findings)} finding(s)",
        ],
    )


def score_portfolio(
    drift: DriftReport,
    risk_metrics: RiskMetrics,
    limit_checks: list[RiskLimitCheck],
    cashflow: CashFlowReport,
    mandate: str,
    risk_scan: MarketRiskScan | None = None,
    country_research: dict[str, CountryResearchProfile] | None = None,
) -> PortfolioRiskScore:
    """Build the portfolio risk scorecard, 0-100 score, and health band deterministically."""
    breaches = [check for check in limit_checks if check.status == "breach"]
    factors = [
        _drift_factor(drift),
        _concentration_factor(risk_metrics),
        _volatility_factor(risk_metrics, mandate),
        _drawdown_factor(risk_metrics),
        _cashflow_factor(cashflow),
        _limit_breach_factor(limit_checks),
        _emerging_risk_factor(risk_scan),
        _country_risk_factor(country_research),
    ]

    raw_score = round(sum(factor.contribution for factor in factors), 2)
    normalized_score = round(min(MAX_SCORE, raw_score), 2)
    band = health_band_for_score(normalized_score)

    _log.info("score: raw=%.1f normalized=%.1f band=%s", raw_score, normalized_score, band.value)
    return PortfolioRiskScore(
        factors=factors,
        raw_score=raw_score,
        normalized_score=normalized_score,
        health_band=band,
        summary=_summarize(band.value, drift, breaches, risk_metrics),
    )


def _summarize(
    band_value: str,
    drift: DriftReport,
    breaches: list[RiskLimitCheck],
    metrics: RiskMetrics,
) -> str:
    parts = [f"Portfolio health: {band_value.upper()}."]
    if drift.n_breaches:
        parts.append(f"{drift.n_breaches} allocation sleeve(s) outside tolerance.")
    if breaches:
        parts.append(f"{len(breaches)} risk limit(s) breached.")
    if metrics.volatility_annual is not None:
        parts.append(f"Annualised volatility {metrics.volatility_annual:.1%}.")
    if not drift.n_breaches and not breaches:
        parts.append("No allocation or risk-limit breaches.")
    return " ".join(parts)

"""
Deterministic credit scorecard + internal rating + lending decision.

NO LLM calls. This module turns the deterministic facts (computed ratios, sector
benchmark, multi-period trend, audit opinion, off-balance-sheet notes) into a
fixed-weight scorecard, a 0-100 risk score (higher = riskier), an internal rating grade
(AAA..D), a risk band, a PD/LGD/expected-loss triple, and a lending decision. Because
weights, severities and band cut-offs are constants, **the same case always yields the
same grade and decision** — the reproducibility guarantee in NFR-1.

One hard stop forces grade = D / decline: a default-level event (disclaimer or adverse
audit opinion, or a going-concern note). Everything else flows through the additive
scorecard.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .reference import (
    CASH_CONVERSION_THRESHOLDS,
    CURRENT_RATIO_THRESHOLDS,
    DSCR_THRESHOLDS,
    EBITDA_MARGIN_THRESHOLDS,
    GRADE_TO_BAND,
    INTEREST_COVER_THRESHOLDS,
    LEVERAGE_THRESHOLDS,
    SCORE_TO_GRADE,
    SIZE_MICRO_REVENUE,
    SIZE_SMALL_REVENUE,
    lgd_for_industry,
    pd_table,
)
from .schemas import (
    AuditOpinion,
    CompanyMetadata,
    CreditScorecard,
    FinancialRatios,
    LendingDecision,
    NoteItem,
    RatingFactorResult,
    RatingFactorType,
    RatingGrade,
    RiskBand,
    SectorBenchmarkResult,
    TrendAnalysis,
)

# Base weights per factor (points). Tuned so that a clean, well-covered investment-grade
# borrower scores < 20 (A/AAA) and a highly-levered, cash-poor borrower with a negative
# trend reaches 60+ (B/CCC).
FACTOR_WEIGHTS: Dict[RatingFactorType, float] = {
    RatingFactorType.LEVERAGE: 22.0,
    RatingFactorType.DEBT_SERVICE_COVERAGE: 22.0,
    RatingFactorType.LIQUIDITY: 16.0,
    RatingFactorType.PROFITABILITY: 16.0,
    RatingFactorType.CASH_FLOW_QUALITY: 14.0,
    RatingFactorType.DETERIORATING_TREND: 16.0,
    RatingFactorType.SECTOR_UNDERPERFORMANCE: 10.0,
    RatingFactorType.SIZE_SCALE: 8.0,
    RatingFactorType.JURISDICTION: 6.0,
    RatingFactorType.AUDIT_QUALITY: 20.0,
    RatingFactorType.OFF_BALANCE_SHEET: 10.0,
    # Web-sentiment factors (softer, corroborating signals — deliberately lower weight
    # than the audited financials so news never dominates a fundamentals-driven rating).
    RatingFactorType.ADVERSE_MEDIA: 14.0,
    RatingFactorType.MARKET_SENTIMENT: 8.0,
}

# severity -> multiplier. 'critical' is reserved for near-default readings.
_SEVERITY_MULT = {"low": 0.0, "medium": 0.6, "high": 1.0, "critical": 1.6}


def _latest(ratios_by_period: List[FinancialRatios]) -> Optional[FinancialRatios]:
    return ratios_by_period[-1] if ratios_by_period else None


def _ratio_value(fr: Optional[FinancialRatios], name: str) -> Optional[float]:
    if not fr:
        return None
    rv = fr.ratios.get(name)
    return rv.value if rv else None


def _factor(
    ftype: RatingFactorType, severity: str, evidence: List[str], pages: List[int],
) -> RatingFactorResult:
    """Build a factor result. severity 'low' => not present / zero contribution."""
    present = severity != "low"
    weight = FACTOR_WEIGHTS[ftype]
    mult = _SEVERITY_MULT.get(severity, 0.0)
    return RatingFactorResult(
        factor=ftype, present=present, severity=severity, weight=weight,
        contribution=round(weight * mult, 2),
        evidence=evidence, pages=sorted({p for p in pages if p is not None}),
    )


def _sev_higher_worse(value: Optional[float], t: Dict[str, float]) -> str:
    """Severity when a HIGHER value is worse (e.g. leverage). t has medium/high/critical."""
    if value is None:
        return "low"
    if value >= t["critical"]:
        return "critical"
    if value >= t["high"]:
        return "high"
    if value >= t["medium"]:
        return "medium"
    return "low"


def _sev_lower_worse(value: Optional[float], t: Dict[str, float]) -> str:
    """Severity when a LOWER value is worse (e.g. coverage, margin, liquidity)."""
    if value is None:
        return "low"
    if value <= t["critical"]:
        return "critical"
    if value <= t["high"]:
        return "high"
    if value <= t["medium"]:
        return "medium"
    return "low"


_SEV_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_RANK_SEV = {1: "low", 2: "medium", 3: "high", 4: "critical"}


def _adverse_media_severity(web_sentiment) -> tuple:
    """Deterministic severity for web-found adverse findings, with a credibility discount.

    Web findings are weaker evidence than audited accounts (a name appearing near
    'lawsuit' in a search is often a false positive, a same-name company, or the firm
    merely commenting on someone else). So each finding is discounted one severity notch,
    a single finding cannot exceed 'medium' on its own, and reaching 'high' requires at
    least two corroborating high-severity findings. A low disambiguation confidence caps
    the whole factor at 'medium'. Returns (severity, evidence_lines).
    """
    if web_sentiment is None or not web_sentiment.adverse_findings:
        return "low", []
    ranks: List[int] = []
    web_high = 0
    for f in web_sentiment.adverse_findings:
        rank = _SEV_RANK.get((f.severity or "medium").lower(), 2)
        if (f.severity or "").lower() == "high":
            web_high += 1
        ranks.append(max(1, rank - 1))          # credibility discount
    top = max(ranks) if ranks else 1
    top = min(top, 2)                           # a single tier of web evidence caps at medium
    if web_high >= 2:                           # corroboration restores high
        top = max(top, 3)
    if (web_sentiment.disambiguation_confidence or "low").lower() == "low":
        top = min(top, 2)                       # unsure it's even the right company
    severity = _RANK_SEV.get(top, "low")
    evidence = [
        f"{f.category}: {f.summary[:90]} [{f.severity}]"
        for f in web_sentiment.adverse_findings
    ]
    return severity, evidence


def _market_sentiment_severity(web_sentiment) -> tuple:
    """Deterministic severity for overall public/news sentiment. Returns (severity, evidence)."""
    if web_sentiment is None:
        return "low", []
    overall = (web_sentiment.overall_sentiment or "neutral").lower()
    conf = (web_sentiment.disambiguation_confidence or "low").lower()
    if overall == "negative":
        severity = "high" if conf == "high" else "medium"
    elif overall == "mixed":
        severity = "medium" if conf in ("medium", "high") else "low"
    else:                                       # positive / neutral
        severity = "low"
    evidence = [f"Overall web sentiment: {overall} (disambiguation: {conf})"]
    if web_sentiment.sentiment_summary:
        evidence.append(web_sentiment.sentiment_summary[:160])
    return severity, evidence


def _grade_for_score(score: float) -> RatingGrade:
    for max_score, grade in SCORE_TO_GRADE:
        if score <= max_score:
            return RatingGrade(grade)
    return RatingGrade.D


def _pricing_for_band(band: RiskBand) -> Optional[str]:
    """Illustrative indicative pricing band by risk band (over a reference rate)."""
    return {
        RiskBand.INVESTMENT_GRADE_STRONG: "Reference + 80-130 bps",
        RiskBand.INVESTMENT_GRADE: "Reference + 130-220 bps",
        RiskBand.SUB_INVESTMENT_GRADE: "Reference + 250-400 bps",
        RiskBand.SPECULATIVE: "Reference + 450-700 bps",
        RiskBand.DISTRESSED: "Bespoke / secured only",
        RiskBand.DEFAULT: "n/a",
    }.get(band)


def _decide(band: RiskBand, material_gaps: int, hard_stop: bool) -> LendingDecision:
    if hard_stop or band == RiskBand.DEFAULT:
        return LendingDecision.DECLINE
    if band == RiskBand.DISTRESSED:
        return LendingDecision.DECLINE
    if band == RiskBand.SPECULATIVE:
        return LendingDecision.REFER_CREDIT_COMMITTEE
    if band == RiskBand.SUB_INVESTMENT_GRADE or material_gaps >= 1:
        return LendingDecision.APPROVE_WITH_CONDITIONS
    return LendingDecision.APPROVE


def _conditions(band: RiskBand, factors: List[RatingFactorResult]) -> List[str]:
    """Suggest standard conditions/covenants driven by which factors fired."""
    conds: List[str] = []
    fired = {f.factor for f in factors if f.present}
    if RatingFactorType.LEVERAGE in fired:
        conds.append("Maximum Net Debt / EBITDA covenant with quarterly testing.")
    if RatingFactorType.DEBT_SERVICE_COVERAGE in fired:
        conds.append("Minimum DSCR / interest-cover covenant.")
    if RatingFactorType.LIQUIDITY in fired:
        conds.append("Minimum liquidity / current-ratio maintenance covenant.")
    if RatingFactorType.CASH_FLOW_QUALITY in fired:
        conds.append("Cash-sweep on excess free cash flow; dividend restriction.")
    if RatingFactorType.OFF_BALANCE_SHEET in fired:
        conds.append("Full disclosure & cap on guarantees / contingent liabilities.")
    if band in (RiskBand.SUB_INVESTMENT_GRADE, RiskBand.SPECULATIVE):
        conds.append("Security / collateral package and personal guarantee where applicable.")
        conds.append("Enhanced monitoring: quarterly management accounts.")
    return conds


def score_case(
    ratios_by_period: List[FinancialRatios],
    benchmark: SectorBenchmarkResult,
    trend: TrendAnalysis,
    company: CompanyMetadata,
    notes: List[NoteItem],
    latest_revenue: Optional[float] = None,
    web_sentiment=None,
) -> CreditScorecard:
    """Build the full scorecard, grade, band, PD/LGD and lending decision deterministically."""
    factors: List[RatingFactorResult] = []
    hard_stop_reason: Optional[str] = None
    latest = _latest(ratios_by_period)

    def pages_of(name: str) -> List[int]:
        return latest.ratios.get(name).pages if (latest and latest.ratios.get(name)) else []

    # --- Leverage (net debt / EBITDA) ---
    lev = _ratio_value(latest, "net_debt_to_ebitda")
    lev_sev = _sev_higher_worse(lev, LEVERAGE_THRESHOLDS)
    factors.append(_factor(
        RatingFactorType.LEVERAGE, lev_sev,
        [f"Net Debt/EBITDA = {lev}" if lev is not None else "Net Debt/EBITDA not computable"],
        pages_of("net_debt_to_ebitda")))

    # --- Debt-service coverage (worst of interest cover and DSCR) ---
    ic = _ratio_value(latest, "interest_coverage")
    dscr = _ratio_value(latest, "dscr")
    ic_sev = _sev_lower_worse(ic, INTEREST_COVER_THRESHOLDS)
    dscr_sev = _sev_lower_worse(dscr, DSCR_THRESHOLDS)
    cover_sev = max([ic_sev, dscr_sev], key=lambda s: _SEVERITY_MULT.get(s, 0.0))
    factors.append(_factor(
        RatingFactorType.DEBT_SERVICE_COVERAGE, cover_sev,
        [f"Interest coverage = {ic}", f"DSCR = {dscr}"],
        pages_of("interest_coverage") + pages_of("dscr")))

    # --- Liquidity (current ratio) ---
    cr = _ratio_value(latest, "current_ratio")
    factors.append(_factor(
        RatingFactorType.LIQUIDITY, _sev_lower_worse(cr, CURRENT_RATIO_THRESHOLDS),
        [f"Current ratio = {cr}" if cr is not None else "Current ratio not computable"],
        pages_of("current_ratio")))

    # --- Profitability (EBITDA margin) ---
    em = _ratio_value(latest, "ebitda_margin")
    factors.append(_factor(
        RatingFactorType.PROFITABILITY, _sev_lower_worse(em, EBITDA_MARGIN_THRESHOLDS),
        [f"EBITDA margin = {em}" if em is not None else "EBITDA margin not computable"],
        pages_of("ebitda_margin")))

    # --- Cash-flow quality (OCF / net income) ---
    cc = _ratio_value(latest, "cash_conversion")
    factors.append(_factor(
        RatingFactorType.CASH_FLOW_QUALITY, _sev_lower_worse(cc, CASH_CONVERSION_THRESHOLDS),
        [f"Cash conversion (OCF/NI) = {cc}" if cc is not None else "Cash conversion not computable"],
        pages_of("cash_conversion")))

    # --- Deteriorating trend ---
    traj = trend.overall_trajectory
    trend_sev = "low"
    if traj == "deteriorating":
        # significant deterioration on a core ratio escalates to high
        core_sig = any(
            t.ratio_name in ("net_debt_to_ebitda", "interest_coverage", "dscr", "ebitda_margin")
            and t.direction == "deteriorating" and t.magnitude == "significant"
            for t in trend.trends
        )
        trend_sev = "high" if core_sig else "medium"
    elif traj == "mixed" or trend.commentary_signal == "risk":
        trend_sev = "medium"
    factors.append(_factor(
        RatingFactorType.DETERIORATING_TREND, trend_sev,
        [f"Overall trajectory: {traj}", f"Management-commentary signal: {trend.commentary_signal}"], []))

    # --- Sector underperformance ---
    pos = benchmark.overall_position
    n_gaps = len(benchmark.material_gaps)
    sector_sev = "high" if (pos == "weak" and n_gaps >= 2) else "medium" if pos == "weak" else "low"
    factors.append(_factor(
        RatingFactorType.SECTOR_UNDERPERFORMANCE, sector_sev,
        [f"Sector position: {pos}",
         f"Material gaps: {', '.join(benchmark.material_gaps) or 'none'}"], []))

    # --- Size / scale ---
    # Revenue is not itself a ratio; the agent graph passes the latest period's revenue
    # (unit-normalised) so this factor needs no raw line items. None => factor stays 'low'.
    revenue = latest_revenue
    size_sev = "low"
    if revenue is not None:
        if revenue < SIZE_MICRO_REVENUE:
            size_sev = "high"
        elif revenue < SIZE_SMALL_REVENUE:
            size_sev = "medium"
    factors.append(_factor(
        RatingFactorType.SIZE_SCALE, size_sev,
        [f"Annual revenue ~ {revenue:,.0f}" if revenue is not None else "Revenue scale unknown"], []))

    # --- Jurisdiction (placeholder low; wire to a country-risk feed in production) ---
    factors.append(_factor(
        RatingFactorType.JURISDICTION, "low",
        [f"Country: {company.country or 'unknown'}"], []))

    # --- Audit quality (HARD STOP on adverse / disclaimer / going concern) ---
    going_concern = any(n.category == "going_concern" for n in notes)
    audit_sev = "low"
    if company.audit_opinion == AuditOpinion.ADVERSE or company.audit_opinion == AuditOpinion.DISCLAIMER:
        audit_sev = "critical"
        hard_stop_reason = f"Audit opinion: {company.audit_opinion.value} (non-reliance)."
    elif going_concern:
        audit_sev = "critical"
        hard_stop_reason = "Going-concern uncertainty disclosed in the notes."
    elif company.audit_opinion == AuditOpinion.QUALIFIED:
        audit_sev = "high"
    factors.append(_factor(
        RatingFactorType.AUDIT_QUALITY, audit_sev,
        [f"Audit opinion: {company.audit_opinion.value}",
         "Going-concern note present" if going_concern else "No going-concern note"], []))

    # --- Off-balance-sheet / contingent liabilities ---
    obs = [n for n in notes if n.category in ("guarantee", "litigation", "operating_lease")]
    obs_sev = "low"
    if obs:
        has_litigation = any(n.category == "litigation" for n in obs)
        obs_sev = "high" if has_litigation else "medium"
    factors.append(_factor(
        RatingFactorType.OFF_BALANCE_SHEET, obs_sev,
        [f"{n.category}: {n.description[:80]}" for n in obs] or ["No material off-balance-sheet items."],
        [n.page for n in obs if n.page]))

    # --- web-sentiment factors (only when a company name was supplied & search ran) ---
    adverse_sev, adverse_ev = _adverse_media_severity(web_sentiment)
    factors.append(_factor(
        RatingFactorType.ADVERSE_MEDIA, adverse_sev,
        adverse_ev or ["No adverse web/news findings."], []))
    sentiment_sev, sentiment_ev = _market_sentiment_severity(web_sentiment)
    factors.append(_factor(
        RatingFactorType.MARKET_SENTIMENT, sentiment_sev,
        sentiment_ev or ["No web sentiment assessed."], []))

    # --- aggregate ---
    raw = round(sum(f.contribution for f in factors), 2)
    normalized = round(min(100.0, raw), 2)
    hard_stop = hard_stop_reason is not None
    if hard_stop:
        normalized = 100.0

    grade = _grade_for_score(normalized)
    band = RiskBand(GRADE_TO_BAND[grade.value])

    pd = pd_table().get(grade.value, 0.05)
    lgd = lgd_for_industry(company.industry)
    el = round(pd * lgd, 6)

    decision = _decide(band, n_gaps, hard_stop)
    conditions = _conditions(band, factors) if decision != LendingDecision.DECLINE else []

    return CreditScorecard(
        factors=factors,
        raw_score=raw,
        normalized_score=normalized,
        grade=grade,
        band=band,
        probability_of_default=pd,
        loss_given_default=lgd,
        expected_loss_pct=el,
        decision=decision,
        suggested_pricing=_pricing_for_band(band) if decision != LendingDecision.DECLINE else None,
        conditions=conditions,
        hard_stop_reason=hard_stop_reason,
    )

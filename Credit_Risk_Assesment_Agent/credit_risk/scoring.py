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
FACTOR_WEIGHTS: dict[RatingFactorType, float] = {
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

SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

MAX_CREDIT_SCORE = 100.0
EVIDENCE_SUMMARY_LENGTH = 90
SENTIMENT_SUMMARY_LENGTH = 160
OFF_BALANCE_DESCRIPTION_LENGTH = 80
MIN_CORROBORATING_HIGH_FINDINGS = 2
MIN_MATERIAL_GAPS_FOR_HIGH_SECTOR = 2

CORE_TREND_RATIOS = (
    "net_debt_to_ebitda",
    "interest_coverage",
    "dscr",
    "ebitda_margin",
)


def _latest(ratios_by_period: list[FinancialRatios]) -> FinancialRatios | None:
    """Return the newest period's ratios, or None when no periods exist."""
    return ratios_by_period[-1] if ratios_by_period else None


def _ratio_value(financial_ratios: FinancialRatios | None, name: str) -> float | None:
    """Return a ratio's value from the latest period, or None when missing."""
    if not financial_ratios:
        return None
    ratio_entry = financial_ratios.ratios.get(name)
    return ratio_entry.value if ratio_entry else None


def _factor(
    factor_type: RatingFactorType,
    severity: str,
    evidence: list[str],
    pages: list[int],
) -> RatingFactorResult:
    """Build a factor result. severity 'low' => not present / zero contribution."""
    is_present = severity != SEVERITY_LOW
    weight = FACTOR_WEIGHTS[factor_type]
    multiplier = _SEVERITY_MULT.get(severity, 0.0)
    return RatingFactorResult(
        factor=factor_type,
        present=is_present,
        severity=severity,
        weight=weight,
        contribution=round(weight * multiplier, 2),
        evidence=evidence,
        pages=sorted({page for page in pages if page is not None}),
    )


def _severity_for_threshold(
    value: float | None, thresholds: dict[str, float], higher_worse: bool
) -> str:
    """Map a value to a severity using medium/high/critical thresholds."""
    if value is None:
        return SEVERITY_LOW
    if higher_worse:
        if value >= thresholds["critical"]:
            return SEVERITY_CRITICAL
        if value >= thresholds["high"]:
            return SEVERITY_HIGH
        if value >= thresholds["medium"]:
            return SEVERITY_MEDIUM
        return SEVERITY_LOW
    if value <= thresholds["critical"]:
        return SEVERITY_CRITICAL
    if value <= thresholds["high"]:
        return SEVERITY_HIGH
    if value <= thresholds["medium"]:
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


def _sev_higher_worse(value: float | None, thresholds: dict[str, float]) -> str:
    """Severity when a HIGHER value is worse (e.g. leverage)."""
    return _severity_for_threshold(value, thresholds, higher_worse=True)


def _sev_lower_worse(value: float | None, thresholds: dict[str, float]) -> str:
    """Severity when a LOWER value is worse (e.g. coverage, margin, liquidity)."""
    return _severity_for_threshold(value, thresholds, higher_worse=False)


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
        return SEVERITY_LOW, []
    discounted_ranks: list[int] = []
    high_severity_count = 0
    for finding in web_sentiment.adverse_findings:
        rank = _SEV_RANK.get((finding.severity or "medium").lower(), 2)
        if (finding.severity or "").lower() == "high":
            high_severity_count += 1
        discounted_ranks.append(max(1, rank - 1))  # credibility discount
    top_rank = max(discounted_ranks) if discounted_ranks else 1
    top_rank = min(top_rank, 2)  # a single tier of web evidence caps at medium
    if high_severity_count >= MIN_CORROBORATING_HIGH_FINDINGS:
        top_rank = max(top_rank, 3)  # corroboration restores high
    if (web_sentiment.disambiguation_confidence or "low").lower() == "low":
        top_rank = min(top_rank, 2)  # unsure it's even the right company
    severity = _RANK_SEV.get(top_rank, SEVERITY_LOW)
    evidence = [
        f"{finding.category}: {finding.summary[:EVIDENCE_SUMMARY_LENGTH]} [{finding.severity}]"
        for finding in web_sentiment.adverse_findings
    ]
    return severity, evidence


def _market_sentiment_severity(web_sentiment) -> tuple:
    """Deterministic severity for overall public/news sentiment. Returns (severity, evidence)."""
    if web_sentiment is None:
        return SEVERITY_LOW, []
    overall = (web_sentiment.overall_sentiment or "neutral").lower()
    confidence = (web_sentiment.disambiguation_confidence or "low").lower()
    if overall == "negative":
        severity = SEVERITY_HIGH if confidence == "high" else SEVERITY_MEDIUM
    elif overall == "mixed":
        severity = SEVERITY_MEDIUM if confidence in ("medium", "high") else SEVERITY_LOW
    else:  # positive / neutral
        severity = SEVERITY_LOW
    evidence = [f"Overall web sentiment: {overall} (disambiguation: {confidence})"]
    if web_sentiment.sentiment_summary:
        evidence.append(web_sentiment.sentiment_summary[:SENTIMENT_SUMMARY_LENGTH])
    return severity, evidence


def _grade_for_score(score: float) -> RatingGrade:
    """Map a 0-100 risk score to an internal rating grade."""
    for max_score, grade in SCORE_TO_GRADE:
        if score <= max_score:
            return RatingGrade(grade)
    return RatingGrade.D


def _pricing_for_band(band: RiskBand) -> str | None:
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
    """Map a risk band to a lending decision, honouring hard stops."""
    if hard_stop or band == RiskBand.DEFAULT:
        return LendingDecision.DECLINE
    if band == RiskBand.DISTRESSED:
        return LendingDecision.DECLINE
    if band == RiskBand.SPECULATIVE:
        return LendingDecision.REFER_CREDIT_COMMITTEE
    if band == RiskBand.SUB_INVESTMENT_GRADE or material_gaps >= 1:
        return LendingDecision.APPROVE_WITH_CONDITIONS
    return LendingDecision.APPROVE


def _conditions(band: RiskBand, factors: list[RatingFactorResult]) -> list[str]:
    """Suggest standard conditions/covenants driven by which factors fired."""
    suggested: list[str] = []
    fired = {scored.factor for scored in factors if scored.present}
    if RatingFactorType.LEVERAGE in fired:
        suggested.append("Maximum Net Debt / EBITDA covenant with quarterly testing.")
    if RatingFactorType.DEBT_SERVICE_COVERAGE in fired:
        suggested.append("Minimum DSCR / interest-cover covenant.")
    if RatingFactorType.LIQUIDITY in fired:
        suggested.append("Minimum liquidity / current-ratio maintenance covenant.")
    if RatingFactorType.CASH_FLOW_QUALITY in fired:
        suggested.append("Cash-sweep on excess free cash flow; dividend restriction.")
    if RatingFactorType.OFF_BALANCE_SHEET in fired:
        suggested.append(
            "Full disclosure & cap on guarantees / contingent liabilities."
        )
    if band in (RiskBand.SUB_INVESTMENT_GRADE, RiskBand.SPECULATIVE):
        suggested.append(
            "Security / collateral package and personal guarantee where applicable."
        )
        suggested.append("Enhanced monitoring: quarterly management accounts.")
    return suggested


def _pages_for_ratio(latest: FinancialRatios | None, name: str) -> list[int]:
    """Return citation pages for one ratio in the latest period."""
    if latest is None:
        return []
    ratio_entry = latest.ratios.get(name)
    return list(ratio_entry.pages) if ratio_entry else []


def _leverage_factor(latest: FinancialRatios | None) -> RatingFactorResult:
    """Build the leverage factor from net debt / EBITDA."""
    leverage = _ratio_value(latest, "net_debt_to_ebitda")
    severity = _sev_higher_worse(leverage, LEVERAGE_THRESHOLDS)
    evidence = (
        [f"Net Debt/EBITDA = {leverage}"]
        if leverage is not None
        else ["Net Debt/EBITDA not computable"]
    )
    return _factor(
        RatingFactorType.LEVERAGE,
        severity,
        evidence,
        _pages_for_ratio(latest, "net_debt_to_ebitda"),
    )


def _coverage_factor(latest: FinancialRatios | None) -> RatingFactorResult:
    """Build debt-service coverage from the worse of interest cover and DSCR."""
    interest_cover = _ratio_value(latest, "interest_coverage")
    dscr = _ratio_value(latest, "dscr")
    interest_severity = _sev_lower_worse(interest_cover, INTEREST_COVER_THRESHOLDS)
    dscr_severity = _sev_lower_worse(dscr, DSCR_THRESHOLDS)
    worst_severity = max(
        [interest_severity, dscr_severity],
        key=lambda severity: _SEVERITY_MULT.get(severity, 0.0),
    )
    return _factor(
        RatingFactorType.DEBT_SERVICE_COVERAGE,
        worst_severity,
        [f"Interest coverage = {interest_cover}", f"DSCR = {dscr}"],
        _pages_for_ratio(latest, "interest_coverage")
        + _pages_for_ratio(latest, "dscr"),
    )


def _single_ratio_factor(
    latest: FinancialRatios | None,
    factor_type: RatingFactorType,
    ratio_name: str,
    label: str,
    thresholds: dict[str, float],
) -> RatingFactorResult:
    """Build a factor driven by one lower-worse ratio."""
    value = _ratio_value(latest, ratio_name)
    severity = _sev_lower_worse(value, thresholds)
    evidence = (
        [f"{label} = {value}"] if value is not None else [f"{label} not computable"]
    )
    return _factor(
        factor_type, severity, evidence, _pages_for_ratio(latest, ratio_name)
    )


def _trend_factor(trend: TrendAnalysis) -> RatingFactorResult:
    """Build the deteriorating-trend factor from trajectory and commentary."""
    trajectory = trend.overall_trajectory
    severity = SEVERITY_LOW
    if trajectory == "deteriorating":
        has_significant_core_deterioration = any(
            trend_item.ratio_name in CORE_TREND_RATIOS
            and trend_item.direction == "deteriorating"
            and trend_item.magnitude == "significant"
            for trend_item in trend.trends
        )
        severity = (
            SEVERITY_HIGH if has_significant_core_deterioration else SEVERITY_MEDIUM
        )
    elif trajectory == "mixed" or trend.commentary_signal == "risk":
        severity = SEVERITY_MEDIUM
    return _factor(
        RatingFactorType.DETERIORATING_TREND,
        severity,
        [
            f"Overall trajectory: {trajectory}",
            f"Management-commentary signal: {trend.commentary_signal}",
        ],
        [],
    )


def _sector_factor(benchmark: SectorBenchmarkResult) -> RatingFactorResult:
    """Build the sector-underperformance factor from benchmark position."""
    position = benchmark.overall_position
    gap_count = len(benchmark.material_gaps)
    if position == "weak" and gap_count >= MIN_MATERIAL_GAPS_FOR_HIGH_SECTOR:
        severity = SEVERITY_HIGH
    elif position == "weak":
        severity = SEVERITY_MEDIUM
    else:
        severity = SEVERITY_LOW
    return _factor(
        RatingFactorType.SECTOR_UNDERPERFORMANCE,
        severity,
        [
            f"Sector position: {position}",
            f"Material gaps: {', '.join(benchmark.material_gaps) or 'none'}",
        ],
        [],
    )


def _size_factor(latest_revenue: float | None) -> RatingFactorResult:
    """Build the size/scale factor from annual revenue."""
    # Revenue is not itself a ratio; the agent graph passes the latest period's revenue
    # (unit-normalised) so this factor needs no raw line items. None stays 'low'.
    severity = SEVERITY_LOW
    if latest_revenue is not None:
        if latest_revenue < SIZE_MICRO_REVENUE:
            severity = SEVERITY_HIGH
        elif latest_revenue < SIZE_SMALL_REVENUE:
            severity = SEVERITY_MEDIUM
    evidence = (
        [f"Annual revenue ~ {latest_revenue:,.0f}"]
        if latest_revenue is not None
        else ["Revenue scale unknown"]
    )
    return _factor(RatingFactorType.SIZE_SCALE, severity, evidence, [])


def _audit_factor(
    company: CompanyMetadata, notes: list[NoteItem]
) -> tuple[RatingFactorResult, str | None]:
    """Build audit-quality factor; returns (factor, hard_stop_reason)."""
    has_going_concern = any(note.category == "going_concern" for note in notes)
    hard_stop_reason: str | None = None
    severity = SEVERITY_LOW
    if company.audit_opinion in (AuditOpinion.ADVERSE, AuditOpinion.DISCLAIMER):
        severity = SEVERITY_CRITICAL
        hard_stop_reason = (
            f"Audit opinion: {company.audit_opinion.value} (non-reliance)."
        )
    elif has_going_concern:
        severity = SEVERITY_CRITICAL
        hard_stop_reason = "Going-concern uncertainty disclosed in the notes."
    elif company.audit_opinion == AuditOpinion.QUALIFIED:
        severity = SEVERITY_HIGH
    factor = _factor(
        RatingFactorType.AUDIT_QUALITY,
        severity,
        [
            f"Audit opinion: {company.audit_opinion.value}",
            "Going-concern note present"
            if has_going_concern
            else "No going-concern note",
        ],
        [],
    )
    return factor, hard_stop_reason


def _off_balance_sheet_factor(notes: list[NoteItem]) -> RatingFactorResult:
    """Build the off-balance-sheet factor from guarantee/litigation/lease notes."""
    relevant_notes = [
        note
        for note in notes
        if note.category in ("guarantee", "litigation", "operating_lease")
    ]
    severity = SEVERITY_LOW
    if relevant_notes:
        has_litigation = any(note.category == "litigation" for note in relevant_notes)
        severity = SEVERITY_HIGH if has_litigation else SEVERITY_MEDIUM
    evidence = [
        f"{note.category}: {note.description[:OFF_BALANCE_DESCRIPTION_LENGTH]}"
        for note in relevant_notes
    ] or ["No material off-balance-sheet items."]
    return _factor(
        RatingFactorType.OFF_BALANCE_SHEET,
        severity,
        evidence,
        [note.page for note in relevant_notes if note.page],
    )


def _sentiment_factors(web_sentiment) -> list[RatingFactorResult]:
    """Build web-sentiment factors (no-ops when search did not run)."""
    adverse_severity, adverse_evidence = _adverse_media_severity(web_sentiment)
    sentiment_severity, sentiment_evidence = _market_sentiment_severity(web_sentiment)
    return [
        _factor(
            RatingFactorType.ADVERSE_MEDIA,
            adverse_severity,
            adverse_evidence or ["No adverse web/news findings."],
            [],
        ),
        _factor(
            RatingFactorType.MARKET_SENTIMENT,
            sentiment_severity,
            sentiment_evidence or ["No web sentiment assessed."],
            [],
        ),
    ]


def score_case(
    ratios_by_period: list[FinancialRatios],
    benchmark: SectorBenchmarkResult,
    trend: TrendAnalysis,
    company: CompanyMetadata,
    notes: list[NoteItem],
    latest_revenue: float | None = None,
    web_sentiment=None,
) -> CreditScorecard:
    """Build the full scorecard, grade, band, PD/LGD and lending decision deterministically."""
    latest = _latest(ratios_by_period)

    factors: list[RatingFactorResult] = [
        _leverage_factor(latest),
        _coverage_factor(latest),
        _single_ratio_factor(
            latest,
            RatingFactorType.LIQUIDITY,
            "current_ratio",
            "Current ratio",
            CURRENT_RATIO_THRESHOLDS,
        ),
        _single_ratio_factor(
            latest,
            RatingFactorType.PROFITABILITY,
            "ebitda_margin",
            "EBITDA margin",
            EBITDA_MARGIN_THRESHOLDS,
        ),
        _single_ratio_factor(
            latest,
            RatingFactorType.CASH_FLOW_QUALITY,
            "cash_conversion",
            "Cash conversion (OCF/NI)",
            CASH_CONVERSION_THRESHOLDS,
        ),
        _trend_factor(trend),
        _sector_factor(benchmark),
        _size_factor(latest_revenue),
        _factor(
            RatingFactorType.JURISDICTION,
            SEVERITY_LOW,
            [f"Country: {company.country or 'unknown'}"],
            [],
        ),
    ]
    audit_factor, hard_stop_reason = _audit_factor(company, notes)
    factors.append(audit_factor)
    factors.append(_off_balance_sheet_factor(notes))
    factors.extend(_sentiment_factors(web_sentiment))

    raw_score = round(sum(scored.contribution for scored in factors), 2)
    normalized_score = round(min(MAX_CREDIT_SCORE, raw_score), 2)
    if hard_stop_reason is not None:
        normalized_score = MAX_CREDIT_SCORE

    grade = _grade_for_score(normalized_score)
    band = RiskBand(GRADE_TO_BAND[grade.value])

    default_probability = pd_table().get(grade.value, 0.05)
    loss_given_default = lgd_for_industry(company.industry)
    expected_loss = round(default_probability * loss_given_default, 6)

    is_hard_stop = hard_stop_reason is not None
    decision = _decide(band, len(benchmark.material_gaps), is_hard_stop)
    conditions = (
        _conditions(band, factors) if decision != LendingDecision.DECLINE else []
    )

    return CreditScorecard(
        factors=factors,
        raw_score=raw_score,
        normalized_score=normalized_score,
        grade=grade,
        band=band,
        probability_of_default=default_probability,
        loss_given_default=loss_given_default,
        expected_loss_pct=expected_loss,
        decision=decision,
        suggested_pricing=(
            _pricing_for_band(band) if decision != LendingDecision.DECLINE else None
        ),
        conditions=conditions,
        hard_stop_reason=hard_stop_reason,
    )

"""
Deterministic sector benchmarking + multi-period trend analysis.

NO LLM calls. Given the computed ratios (from `ratios.py`), this module:
  * compares the latest period's ratios against the sector quartiles, classifying each
    as above/below median or in the weak/strong quartile (honouring whether higher is
    better for that ratio); and
  * classifies each key ratio's multi-period trend direction and magnitude from its
    time series.

Both are pure functions of the inputs, so they are reproducible and auditable.
"""

from __future__ import annotations

from .reference import (
    HIGHER_IS_BETTER,
    MATERIAL_RATIOS,
    resolve_industry_key,
    sector_benchmarks,
)
from .schemas import (
    BenchmarkComparison,
    FinancialRatios,
    RatioTrend,
    SectorBenchmarkResult,
    TrendAnalysis,
)

# Ratios tracked for the headline trend view (the ones a credit committee watches).
_TREND_RATIOS = [
    "net_debt_to_ebitda",
    "interest_coverage",
    "dscr",
    "ebitda_margin",
    "net_margin",
    "current_ratio",
    "ocf_to_total_debt",
]

# Trend magnitude cut-offs on |relative change| from oldest to newest.
_MAG_MODERATE = 0.10  # >=10% move
_MAG_SIGNIFICANT = 0.25  # >=25% move

# Overall position cut-offs on the fraction of scored ratios landing strong/weak.
STRONG_FRACTION_FOR_STRONG_POSITION = 0.6
WEAK_FRACTION_FOR_WEAK_POSITION = 0.5
MATERIAL_GAPS_FOR_WEAK_POSITION = 2
MIN_TREND_POINTS = 2
MIN_PERIODS_FOR_TREND_COUNTS = 2
TINY_BASE_GUARD = 1e-9


# --------------------------------------------------------------------------- #
# Benchmarking
# --------------------------------------------------------------------------- #
def _classify(
    value: float, p25: float, median: float, p75: float, higher_better: bool
) -> str:
    """Classify a value against quartile boundaries.

    p25/p75 are the raw distribution boundaries. The 'strong' end is p75 when higher is
    better, p25 when lower is better — we phrase the classification in risk terms so the
    UI/scorecard read consistently.
    """
    if higher_better:
        return _classify_higher_better(value, p25, median, p75)
    return _classify_lower_better(value, p25, median, p75)


def _classify_higher_better(value: float, p25: float, median: float, p75: float) -> str:
    """Classify when larger values mean lower risk."""
    if value >= p75:
        return "above_75th"
    if value >= median:
        return "above_median"
    if value <= p25:
        return "below_25th"
    return "below_median"


def _classify_lower_better(value: float, p25: float, median: float, p75: float) -> str:
    """Classify when smaller values mean lower risk."""
    if value <= p25:
        return "above_75th"  # i.e. strong end
    if value <= median:
        return "above_median"
    if value >= p75:
        return "below_25th"  # weak end
    return "below_median"


def benchmark_ratios(
    latest: FinancialRatios, industry: str | None
) -> SectorBenchmarkResult:
    """Benchmark the latest period's ratios against the resolved sector profile."""
    industry_key = resolve_industry_key(industry)
    sector_profiles = sector_benchmarks()
    profile = sector_profiles.get(industry_key, sector_profiles.get("general", {}))

    comparisons: list[BenchmarkComparison] = []
    material_gaps: list[str] = []
    strong_count = weak_count = scored = 0

    for ratio_name, quartile_bounds in profile.items():
        ratio_value = latest.ratios.get(ratio_name)
        company_value = ratio_value.value if ratio_value else None
        higher_better = HIGHER_IS_BETTER.get(ratio_name, True)
        p25 = quartile_bounds.get("p25")
        median = quartile_bounds.get("median")
        p75 = quartile_bounds.get("p75")

        if company_value is None or median is None:
            comparisons.append(
                BenchmarkComparison(
                    ratio_name=ratio_name,
                    company_value=company_value,
                    sector_median=median,
                    sector_p25=p25,
                    sector_p75=p75,
                    classification="not_available",
                    higher_is_better=higher_better,
                )
            )
            continue

        classification = _classify(company_value, p25, median, p75, higher_better)
        is_gap = (classification == "below_25th") and (ratio_name in MATERIAL_RATIOS)
        if is_gap:
            material_gaps.append(ratio_name)

        if classification in ("above_75th", "above_median"):
            strong_count += 1
        elif classification in ("below_median", "below_25th"):
            weak_count += 1
        scored += 1

        comparisons.append(
            BenchmarkComparison(
                ratio_name=ratio_name,
                company_value=company_value,
                sector_median=median,
                sector_p25=p25,
                sector_p75=p75,
                classification=classification,
                higher_is_better=higher_better,
                is_material_gap=is_gap,
            )
        )

    # Overall position: weighted by how many ratios land strong vs weak.
    overall = "average"
    if scored:
        strong_fraction = strong_count / scored
        weak_fraction = weak_count / scored
        if strong_fraction >= STRONG_FRACTION_FOR_STRONG_POSITION and not material_gaps:
            overall = "strong"
        elif (
            weak_fraction >= WEAK_FRACTION_FOR_WEAK_POSITION
            or len(material_gaps) >= MATERIAL_GAPS_FOR_WEAK_POSITION
        ):
            overall = "weak"

    return SectorBenchmarkResult(
        industry_used=industry_key,
        comparisons=comparisons,
        overall_position=overall,
        material_gaps=material_gaps,
    )


# --------------------------------------------------------------------------- #
# Trend analysis
# --------------------------------------------------------------------------- #
def _trend_direction(series: list[float | None], higher_better: bool) -> tuple:
    """Return (direction, magnitude, pct_change) from oldest->newest series."""
    dated_points = [
        (index, value) for index, value in enumerate(series) if value is not None
    ]
    if len(dated_points) < MIN_TREND_POINTS:
        return "stable", "minor", None

    first_value = dated_points[0][1]
    last_value = dated_points[-1][1]

    # relative change vs the magnitude of the starting value (guard tiny denominators)
    base = max(TINY_BASE_GUARD, abs(first_value))
    pct_change = (last_value - first_value) / base

    # volatility: count sign changes in the step-to-step deltas
    step_deltas = [
        dated_points[next_index + 1][1] - dated_points[next_index][1]
        for next_index in range(len(dated_points) - 1)
    ]
    sign_changes = sum(
        1
        for index in range(len(step_deltas) - 1)
        if step_deltas[index] != 0
        and step_deltas[index + 1] != 0
        and (step_deltas[index] > 0) != (step_deltas[index + 1] > 0)
    )

    absolute_change = abs(pct_change)
    if absolute_change >= _MAG_SIGNIFICANT:
        magnitude = "significant"
    elif absolute_change >= _MAG_MODERATE:
        magnitude = "moderate"
    else:
        magnitude = "minor"

    if (
        len(step_deltas) >= MIN_TREND_POINTS
        and sign_changes >= 1
        and absolute_change < _MAG_SIGNIFICANT
    ):
        return "volatile", magnitude, round(pct_change, 4)

    if absolute_change < _MAG_MODERATE:
        return "stable", "minor", round(pct_change, 4)

    # An increase is "improving" only when higher is better for that ratio.
    improving = (pct_change > 0) == higher_better
    return (
        ("improving" if improving else "deteriorating"),
        magnitude,
        round(pct_change, 4),
    )


def _series_for_ratio(
    ratio_name: str, periods: list[str], by_period: dict[str, FinancialRatios]
) -> list[float | None]:
    """Extract oldest-to-newest values for one ratio across periods."""
    series: list[float | None] = []
    for period in periods:
        period_ratios = by_period.get(period)
        ratio_entry = period_ratios.ratios.get(ratio_name) if period_ratios else None
        series.append(ratio_entry.value if ratio_entry else None)
    return series


def _overall_trajectory(deteriorating: int, improving: int) -> str:
    """Derive headline trajectory from deteriorating/improving counts."""
    if deteriorating >= MIN_PERIODS_FOR_TREND_COUNTS and deteriorating > improving:
        return "deteriorating"
    if improving >= MIN_PERIODS_FOR_TREND_COUNTS and improving > deteriorating:
        return "improving"
    if deteriorating and improving:
        return "mixed"
    return "stable"


def analyse_trends(
    periods: list[str],
    ratios_by_period: list[FinancialRatios],
    commentary: str | None = None,
) -> TrendAnalysis:
    """Classify each key ratio's trend across periods and derive overall trajectory."""
    ratios_by_label = {
        financial_ratios.period: financial_ratios
        for financial_ratios in ratios_by_period
    }
    trends: list[RatioTrend] = []

    deteriorating = improving = 0
    for ratio_name in _TREND_RATIOS:
        series = _series_for_ratio(ratio_name, periods, ratios_by_label)
        higher_better = HIGHER_IS_BETTER.get(ratio_name, True)
        direction, magnitude, pct_change = _trend_direction(series, higher_better)
        trends.append(
            RatioTrend(
                ratio_name=ratio_name,
                series=series,
                periods=list(periods),
                direction=direction,
                magnitude=magnitude,
                pct_change=pct_change,
            )
        )
        if direction == "deteriorating" and magnitude in ("moderate", "significant"):
            deteriorating += 1
        elif direction == "improving" and magnitude in ("moderate", "significant"):
            improving += 1

    trajectory = _overall_trajectory(deteriorating, improving)

    # Lightweight, deterministic keyword read of management commentary.
    signal = _commentary_signal(commentary)

    notes: list[str] = []
    if len(periods) < MIN_PERIODS_FOR_TREND_COUNTS:
        notes.append("Single period only — trend analysis is not meaningful.")
    return TrendAnalysis(
        trends=trends,
        overall_trajectory=trajectory,
        commentary_signal=signal,
        notes=notes,
    )


_RISK_WORDS = (
    "going concern",
    "loss",
    "decline",
    "decrease",
    "impairment",
    "default",
    "covenant breach",
    "uncertainty",
    "litigation",
    "restructur",
    "downturn",
    "headwind",
    "challenging",
    "shortfall",
)
_POSITIVE_WORDS = (
    "growth",
    "record",
    "improved",
    "strong",
    "expansion",
    "increase in revenue",
    "margin expansion",
    "robust",
    "outperform",
)


def _commentary_signal(commentary: str | None) -> str:
    """Deterministic risk/neutral/positive read of MD&A text by keyword counting."""
    if not commentary:
        return "neutral"
    lowered_commentary = commentary.lower()
    risk_hits = sum(1 for word in _RISK_WORDS if word in lowered_commentary)
    positive_hits = sum(1 for word in _POSITIVE_WORDS if word in lowered_commentary)
    if risk_hits > positive_hits and risk_hits >= 1:
        return "risk"
    if positive_hits > risk_hits and positive_hits >= 1:
        return "positive"
    return "neutral"

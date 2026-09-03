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

from typing import Dict, List, Optional

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
    "net_debt_to_ebitda", "interest_coverage", "dscr",
    "ebitda_margin", "net_margin", "current_ratio", "ocf_to_total_debt",
]

# Trend magnitude cut-offs on |relative change| from oldest to newest.
_MAG_MODERATE = 0.10     # >=10% move
_MAG_SIGNIFICANT = 0.25  # >=25% move


# --------------------------------------------------------------------------- #
# Benchmarking
# --------------------------------------------------------------------------- #
def _classify(value: float, p25: float, median: float, p75: float, higher_better: bool) -> str:
    """Classify a value against quartile boundaries.

    p25/p75 are the raw distribution boundaries. The 'strong' end is p75 when higher is
    better, p25 when lower is better — we phrase the classification in risk terms so the
    UI/scorecard read consistently.
    """
    if higher_better:
        if value >= p75:
            return "above_75th"
        if value >= median:
            return "above_median"
        if value <= p25:
            return "below_25th"
        return "below_median"
    else:
        # lower is better: small values are strong
        if value <= p25:
            return "above_75th"          # i.e. strong end
        if value <= median:
            return "above_median"
        if value >= p75:
            return "below_25th"          # weak end
        return "below_median"


def benchmark_ratios(
    latest: FinancialRatios, industry: Optional[str]
) -> SectorBenchmarkResult:
    """Benchmark the latest period's ratios against the resolved sector profile."""
    key = resolve_industry_key(industry)
    profile = sector_benchmarks().get(key, sector_benchmarks().get("general", {}))

    comparisons: List[BenchmarkComparison] = []
    material_gaps: List[str] = []
    strong_count = weak_count = scored = 0

    for name, bench in profile.items():
        rv = latest.ratios.get(name)
        company_value = rv.value if rv else None
        higher_better = HIGHER_IS_BETTER.get(name, True)
        p25, median, p75 = bench.get("p25"), bench.get("median"), bench.get("p75")

        if company_value is None or median is None:
            comparisons.append(BenchmarkComparison(
                ratio_name=name, company_value=company_value,
                sector_median=median, sector_p25=p25, sector_p75=p75,
                classification="not_available", higher_is_better=higher_better))
            continue

        cls = _classify(company_value, p25, median, p75, higher_better)
        is_gap = (cls == "below_25th") and (name in MATERIAL_RATIOS)
        if is_gap:
            material_gaps.append(name)

        if cls in ("above_75th", "above_median"):
            strong_count += 1
        elif cls in ("below_median", "below_25th"):
            weak_count += 1
        scored += 1

        comparisons.append(BenchmarkComparison(
            ratio_name=name, company_value=company_value,
            sector_median=median, sector_p25=p25, sector_p75=p75,
            classification=cls, higher_is_better=higher_better, is_material_gap=is_gap))

    # Overall position: weighted by how many ratios land strong vs weak.
    overall = "average"
    if scored:
        strong_frac = strong_count / scored
        weak_frac = weak_count / scored
        if strong_frac >= 0.6 and not material_gaps:
            overall = "strong"
        elif weak_frac >= 0.5 or len(material_gaps) >= 2:
            overall = "weak"

    return SectorBenchmarkResult(
        industry_used=key, comparisons=comparisons,
        overall_position=overall, material_gaps=material_gaps)


# --------------------------------------------------------------------------- #
# Trend analysis
# --------------------------------------------------------------------------- #
def _trend_direction(series: List[Optional[float]], higher_better: bool) -> tuple:
    """Return (direction, magnitude, pct_change) from oldest->newest series."""
    pts = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(pts) < 2:
        return "stable", "minor", None

    first_v = pts[0][1]
    last_v = pts[-1][1]

    # relative change vs the magnitude of the starting value (guard tiny denominators)
    base = abs(first_v) if abs(first_v) > 1e-9 else 1e-9
    pct_change = (last_v - first_v) / base

    # volatility: count sign changes in the step-to-step deltas
    deltas = [pts[j + 1][1] - pts[j][1] for j in range(len(pts) - 1)]
    sign_changes = sum(
        1 for j in range(len(deltas) - 1)
        if deltas[j] != 0 and deltas[j + 1] != 0 and (deltas[j] > 0) != (deltas[j + 1] > 0)
    )

    abs_change = abs(pct_change)
    if abs_change >= _MAG_SIGNIFICANT:
        magnitude = "significant"
    elif abs_change >= _MAG_MODERATE:
        magnitude = "moderate"
    else:
        magnitude = "minor"

    if len(deltas) >= 2 and sign_changes >= 1 and abs_change < _MAG_SIGNIFICANT:
        return "volatile", magnitude, round(pct_change, 4)

    if abs_change < _MAG_MODERATE:
        return "stable", "minor", round(pct_change, 4)

    # An increase is "improving" only when higher is better for that ratio.
    improving = (pct_change > 0) == higher_better
    return ("improving" if improving else "deteriorating"), magnitude, round(pct_change, 4)


def analyse_trends(
    periods: List[str], ratios_by_period: List[FinancialRatios],
    commentary: Optional[str] = None,
) -> TrendAnalysis:
    """Classify each key ratio's trend across periods and derive overall trajectory."""
    by_period = {fr.period: fr for fr in ratios_by_period}
    trends: List[RatioTrend] = []

    deteriorating = improving = 0
    for name in _TREND_RATIOS:
        series = [
            (by_period[p].ratios.get(name).value if by_period.get(p) and by_period[p].ratios.get(name) else None)
            for p in periods
        ]
        higher_better = HIGHER_IS_BETTER.get(name, True)
        direction, magnitude, pct = _trend_direction(series, higher_better)
        trends.append(RatioTrend(
            ratio_name=name, series=series, periods=list(periods),
            direction=direction, magnitude=magnitude, pct_change=pct))
        if direction == "deteriorating" and magnitude in ("moderate", "significant"):
            deteriorating += 1
        elif direction == "improving" and magnitude in ("moderate", "significant"):
            improving += 1

    if deteriorating >= 2 and deteriorating > improving:
        trajectory = "deteriorating"
    elif improving >= 2 and improving > deteriorating:
        trajectory = "improving"
    elif deteriorating and improving:
        trajectory = "mixed"
    else:
        trajectory = "stable"

    # Lightweight, deterministic keyword read of management commentary.
    signal = _commentary_signal(commentary)

    notes: List[str] = []
    if len(periods) < 2:
        notes.append("Single period only — trend analysis is not meaningful.")
    return TrendAnalysis(
        trends=trends, overall_trajectory=trajectory,
        commentary_signal=signal, notes=notes)


_RISK_WORDS = (
    "going concern", "loss", "decline", "decrease", "impairment", "default", "covenant breach",
    "uncertainty", "litigation", "restructur", "downturn", "headwind", "challenging", "shortfall",
)
_POSITIVE_WORDS = (
    "growth", "record", "improved", "strong", "expansion", "increase in revenue",
    "margin expansion", "robust", "outperform",
)


def _commentary_signal(commentary: Optional[str]) -> str:
    """Deterministic risk/neutral/positive read of MD&A text by keyword counting."""
    if not commentary:
        return "neutral"
    low = commentary.lower()
    risk = sum(1 for w in _RISK_WORDS if w in low)
    pos = sum(1 for w in _POSITIVE_WORDS if w in low)
    if risk > pos and risk >= 1:
        return "risk"
    if pos > risk and pos >= 1:
        return "positive"
    return "neutral"

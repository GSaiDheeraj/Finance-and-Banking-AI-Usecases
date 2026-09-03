"""
Reference data for deterministic ratio benchmarking and credit scoring.

Everything here is **illustrative sample data** so the system runs end-to-end out of
the box. In production, point each table at a licensed / validated source via the
*_PATH environment variables (see .env.example):
  * SECTOR_BENCHMARKS_PATH  → Dun & Bradstreet / Refinitiv sector quartiles, or your
    own portfolio-derived medians.
  * RATING_PD_TABLE_PATH    → your IRB-validated grade→PD master scale.
  * INDUSTRY_LGD_TABLE_PATH → your downturn LGD estimates by industry/collateral.

The scoring/benchmarking logic in `benchmarks.py` and `scoring.py` does not change when
the data is swapped — only the numbers do.

Each loader prefers a JSON file at the configured path and falls back to the bundled
sample.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional


# --------------------------------------------------------------------------- #
# Whether a higher value of each ratio is "better" (lower credit risk).
# The benchmark engine uses this to decide which quartile is the strong end.
# --------------------------------------------------------------------------- #
HIGHER_IS_BETTER: Dict[str, bool] = {
    # leverage — lower is better
    "debt_to_equity": False,
    "debt_to_assets": False,
    "net_debt_to_ebitda": False,
    "total_liabilities_to_equity": False,
    # liquidity — higher is better
    "current_ratio": True,
    "quick_ratio": True,
    "cash_ratio": True,
    # profitability — higher is better
    "ebitda_margin": True,
    "ebit_margin": True,
    "net_margin": True,
    "return_on_equity": True,
    "return_on_assets": True,
    "return_on_capital_employed": True,
    # coverage — higher is better
    "interest_coverage": True,
    "dscr": True,
    "ocf_to_total_debt": True,
    # cash-flow quality — higher is better
    "cash_conversion": True,
    "fcf_margin": True,
    # efficiency — context dependent; treat higher turnover / lower days as better
    "asset_turnover": True,
    "inventory_days": False,
    "days_sales_outstanding": False,
}

# Which ratios are "material" — a bottom-quartile reading on these is a flagged gap.
MATERIAL_RATIOS = {
    "net_debt_to_ebitda", "interest_coverage", "dscr", "current_ratio",
    "ebitda_margin", "ocf_to_total_debt",
}


# --------------------------------------------------------------------------- #
# Sector benchmarks: per industry, {ratio: {"p25","median","p75"}}.
# p25 / p75 are the weak / strong quartile boundaries on the *raw* ratio value;
# the engine flips them by HIGHER_IS_BETTER. Illustrative figures only.
# --------------------------------------------------------------------------- #
_SAMPLE_BENCHMARKS: Dict[str, Dict[str, Dict[str, float]]] = {
    "manufacturing": {
        "net_debt_to_ebitda": {"p25": 1.0, "median": 2.5, "p75": 4.0},
        "debt_to_equity": {"p25": 0.5, "median": 1.0, "p75": 1.8},
        "current_ratio": {"p25": 1.1, "median": 1.5, "p75": 2.2},
        "quick_ratio": {"p25": 0.7, "median": 1.0, "p75": 1.5},
        "ebitda_margin": {"p25": 0.08, "median": 0.14, "p75": 0.22},
        "ebit_margin": {"p25": 0.05, "median": 0.10, "p75": 0.16},
        "net_margin": {"p25": 0.03, "median": 0.07, "p75": 0.12},
        "interest_coverage": {"p25": 2.5, "median": 5.0, "p75": 10.0},
        "dscr": {"p25": 1.1, "median": 1.5, "p75": 2.2},
        "return_on_equity": {"p25": 0.06, "median": 0.12, "p75": 0.20},
        "return_on_assets": {"p25": 0.03, "median": 0.07, "p75": 0.12},
        "ocf_to_total_debt": {"p25": 0.10, "median": 0.25, "p75": 0.45},
        "cash_conversion": {"p25": 0.6, "median": 1.0, "p75": 1.3},
        "asset_turnover": {"p25": 0.7, "median": 1.1, "p75": 1.6},
        "inventory_days": {"p25": 45.0, "median": 75.0, "p75": 110.0},
        "days_sales_outstanding": {"p25": 40.0, "median": 60.0, "p75": 90.0},
    },
    "services": {
        "net_debt_to_ebitda": {"p25": 0.8, "median": 2.0, "p75": 3.5},
        "debt_to_equity": {"p25": 0.3, "median": 0.8, "p75": 1.5},
        "current_ratio": {"p25": 1.0, "median": 1.4, "p75": 2.0},
        "quick_ratio": {"p25": 0.9, "median": 1.3, "p75": 1.9},
        "ebitda_margin": {"p25": 0.10, "median": 0.18, "p75": 0.28},
        "ebit_margin": {"p25": 0.07, "median": 0.13, "p75": 0.22},
        "net_margin": {"p25": 0.05, "median": 0.10, "p75": 0.18},
        "interest_coverage": {"p25": 3.0, "median": 6.0, "p75": 12.0},
        "dscr": {"p25": 1.2, "median": 1.6, "p75": 2.5},
        "return_on_equity": {"p25": 0.08, "median": 0.16, "p75": 0.26},
        "return_on_assets": {"p25": 0.05, "median": 0.10, "p75": 0.18},
        "ocf_to_total_debt": {"p25": 0.15, "median": 0.30, "p75": 0.55},
        "cash_conversion": {"p25": 0.7, "median": 1.0, "p75": 1.3},
        "asset_turnover": {"p25": 0.9, "median": 1.4, "p75": 2.0},
        "inventory_days": {"p25": 5.0, "median": 15.0, "p75": 35.0},
        "days_sales_outstanding": {"p25": 35.0, "median": 55.0, "p75": 85.0},
    },
    "retail": {
        "net_debt_to_ebitda": {"p25": 1.2, "median": 2.8, "p75": 4.5},
        "debt_to_equity": {"p25": 0.6, "median": 1.2, "p75": 2.2},
        "current_ratio": {"p25": 0.9, "median": 1.2, "p75": 1.8},
        "quick_ratio": {"p25": 0.3, "median": 0.6, "p75": 1.0},
        "ebitda_margin": {"p25": 0.05, "median": 0.09, "p75": 0.15},
        "ebit_margin": {"p25": 0.03, "median": 0.06, "p75": 0.10},
        "net_margin": {"p25": 0.01, "median": 0.04, "p75": 0.08},
        "interest_coverage": {"p25": 2.0, "median": 4.0, "p75": 8.0},
        "dscr": {"p25": 1.0, "median": 1.4, "p75": 2.0},
        "return_on_equity": {"p25": 0.07, "median": 0.14, "p75": 0.24},
        "return_on_assets": {"p25": 0.03, "median": 0.07, "p75": 0.13},
        "ocf_to_total_debt": {"p25": 0.10, "median": 0.22, "p75": 0.40},
        "cash_conversion": {"p25": 0.7, "median": 1.0, "p75": 1.4},
        "asset_turnover": {"p25": 1.2, "median": 2.0, "p75": 3.0},
        "inventory_days": {"p25": 30.0, "median": 55.0, "p75": 90.0},
        "days_sales_outstanding": {"p25": 5.0, "median": 15.0, "p75": 35.0},
    },
    # Conservative all-sector fallback when the industry is unknown / unmapped.
    "general": {
        "net_debt_to_ebitda": {"p25": 1.0, "median": 2.5, "p75": 4.0},
        "debt_to_equity": {"p25": 0.5, "median": 1.0, "p75": 2.0},
        "current_ratio": {"p25": 1.0, "median": 1.4, "p75": 2.0},
        "quick_ratio": {"p25": 0.6, "median": 1.0, "p75": 1.5},
        "ebitda_margin": {"p25": 0.07, "median": 0.13, "p75": 0.20},
        "ebit_margin": {"p25": 0.04, "median": 0.09, "p75": 0.15},
        "net_margin": {"p25": 0.02, "median": 0.06, "p75": 0.11},
        "interest_coverage": {"p25": 2.5, "median": 5.0, "p75": 10.0},
        "dscr": {"p25": 1.1, "median": 1.5, "p75": 2.2},
        "return_on_equity": {"p25": 0.06, "median": 0.13, "p75": 0.22},
        "return_on_assets": {"p25": 0.03, "median": 0.07, "p75": 0.13},
        "ocf_to_total_debt": {"p25": 0.12, "median": 0.25, "p75": 0.45},
        "cash_conversion": {"p25": 0.65, "median": 1.0, "p75": 1.3},
        "asset_turnover": {"p25": 0.8, "median": 1.3, "p75": 1.9},
        "inventory_days": {"p25": 30.0, "median": 60.0, "p75": 100.0},
        "days_sales_outstanding": {"p25": 35.0, "median": 58.0, "p75": 88.0},
    },
}

# Map free-text industry strings onto a benchmark profile key. Substring match,
# checked in order; falls back to 'general'.
_INDUSTRY_ALIASES: List[tuple] = [
    ("manufactur", "manufacturing"),
    ("industr", "manufacturing"),
    ("factory", "manufacturing"),
    ("production", "manufacturing"),
    ("retail", "retail"),
    ("consumer", "retail"),
    ("store", "retail"),
    ("wholesale", "retail"),
    ("service", "services"),
    ("software", "services"),
    ("technology", "services"),
    ("consult", "services"),
    ("financ", "services"),
]


def resolve_industry_key(industry: Optional[str]) -> str:
    """Map a free-text industry to a benchmark-profile key."""
    if not industry:
        return "general"
    low = industry.lower()
    for needle, key in _INDUSTRY_ALIASES:
        if needle in low:
            return key
    return "general"


# --------------------------------------------------------------------------- #
# Rating-grade → probability of default (1-year, illustrative master scale).
# Replace with your IRB-validated PD master scale in production.
# --------------------------------------------------------------------------- #
_SAMPLE_PD_TABLE: Dict[str, float] = {
    "AAA": 0.0001,
    "AA": 0.0003,
    "A": 0.0008,
    "BBB": 0.0025,
    "BB": 0.0100,
    "B": 0.0400,
    "CCC": 0.1200,
    "CC": 0.2500,
    "C": 0.4500,
    "D": 1.0000,
}

# Industry → downturn loss given default (illustrative). Higher for asset-light /
# low-recovery sectors; lower where tangible collateral is typical.
_SAMPLE_LGD_TABLE: Dict[str, float] = {
    "manufacturing": 0.40,   # tangible plant & equipment as collateral
    "retail": 0.45,
    "services": 0.55,        # asset-light, lower recovery
    "general": 0.45,
}


# --------------------------------------------------------------------------- #
# Score → rating grade cut-offs. Score is 0..100, higher = riskier.
# Each tuple is (max_score_inclusive, grade). First match wins.
# --------------------------------------------------------------------------- #
SCORE_TO_GRADE: List[tuple] = [
    (5.0, "AAA"),
    (12.0, "AA"),
    (20.0, "A"),
    (32.0, "BBB"),
    (45.0, "BB"),
    (60.0, "B"),
    (75.0, "CCC"),
    (88.0, "CC"),
    (97.0, "C"),
    (100.0, "D"),
]

GRADE_TO_BAND: Dict[str, str] = {
    "AAA": "investment_grade_strong",
    "AA": "investment_grade_strong",
    "A": "investment_grade",
    "BBB": "investment_grade",
    "BB": "sub_investment_grade",
    "B": "speculative",
    "CCC": "speculative",
    "CC": "distressed",
    "C": "distressed",
    "D": "default",
}


def _load(path_env: str, fallback):
    """Load a JSON list/dict from the path in `path_env`, else return the fallback."""
    path = os.getenv(path_env)
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return fallback
    return fallback


def sector_benchmarks() -> Dict[str, Dict[str, Dict[str, float]]]:
    return _load("SECTOR_BENCHMARKS_PATH", _SAMPLE_BENCHMARKS)


def pd_table() -> Dict[str, float]:
    return _load("RATING_PD_TABLE_PATH", _SAMPLE_PD_TABLE)


def lgd_table() -> Dict[str, float]:
    return _load("INDUSTRY_LGD_TABLE_PATH", _SAMPLE_LGD_TABLE)


def lgd_for_industry(industry: Optional[str]) -> float:
    return lgd_table().get(resolve_industry_key(industry), lgd_table().get("general", 0.45))


# --------------------------------------------------------------------------- #
# Scoring thresholds (config-driven; fixed at run time for reproducibility).
# These set where each rating factor flips from low->medium->high->critical.
# All are "credit-risk worsening" directions.
# --------------------------------------------------------------------------- #
LEVERAGE_THRESHOLDS = {            # net_debt_to_ebitda
    "medium": 3.0, "high": 4.5, "critical": 6.0,
}
INTEREST_COVER_THRESHOLDS = {      # interest_coverage — LOWER is worse
    "medium": 4.0, "high": 2.0, "critical": 1.0,
}
DSCR_THRESHOLDS = {                # dscr — LOWER is worse
    "medium": 1.5, "high": 1.2, "critical": 1.0,
}
CURRENT_RATIO_THRESHOLDS = {       # current_ratio — LOWER is worse
    "medium": 1.2, "high": 1.0, "critical": 0.8,
}
EBITDA_MARGIN_THRESHOLDS = {       # ebitda_margin — LOWER is worse
    "medium": 0.10, "high": 0.05, "critical": 0.0,
}
CASH_CONVERSION_THRESHOLDS = {     # cash_conversion (OCF/NI) — LOWER is worse
    "medium": 0.8, "high": 0.5, "critical": 0.2,
}

# Annual revenue (in reporting currency, absolute) below which scale is a concern.
SIZE_SMALL_REVENUE = 10_000_000.0      # below this: small-cap scale risk (medium)
SIZE_MICRO_REVENUE = 2_000_000.0       # below this: micro scale risk (high)

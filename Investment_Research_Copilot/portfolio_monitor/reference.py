"""
Reference data for deterministic policy, risk limits, construction and scoring.

Everything here is **illustrative sample data** so the system runs end-to-end out of the
box. In production, point each table at your firm's Investment Policy Statement, risk
framework and approved universe via the *_PATH environment variables (see .env.example):
  * IPS_TARGETS_PATH      → strategic asset allocation + tolerance bands per mandate
  * RISK_LIMITS_PATH      → single-name / sector caps, vol / drawdown / VaR ceilings
  * UNIVERSE_SEED_PATH     → approved candidate universe (fail-soft seed pool)

The allocation / drift / risk / scoring / construction logic does not change when the data
is swapped — only the numbers do. Each loader prefers a JSON file at the configured path and
falls back to the bundled sample.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .schemas import AssetClass, HealthBand, RiskFactorType


# --------------------------------------------------------------------------- #
# Strategic asset allocation (SAA) per mandate: {asset_class: target_weight}.
# Weights sum to 1.0. Illustrative figures.
# --------------------------------------------------------------------------- #
_SAMPLE_IPS: Dict[str, Dict] = {
    "conservative": {
        "targets": {
            "equity": 0.30, "fixed_income": 0.55, "cash": 0.10, "alternatives": 0.05,
        },
        "band_abs": 0.05,                 # ± absolute tolerance band on each sleeve
        "benchmark": "AOK",               # iShares Core Conservative Allocation ETF
    },
    "balanced": {
        "targets": {
            "equity": 0.55, "fixed_income": 0.35, "cash": 0.05, "alternatives": 0.05,
        },
        "band_abs": 0.05,
        "benchmark": "AOM",               # iShares Core Moderate Allocation ETF
    },
    "growth": {
        "targets": {
            "equity": 0.75, "fixed_income": 0.18, "cash": 0.02, "alternatives": 0.05,
        },
        "band_abs": 0.06,
        "benchmark": "AOR",               # iShares Core Growth Allocation ETF
    },
    "aggressive": {
        "targets": {
            "equity": 0.90, "fixed_income": 0.05, "cash": 0.00, "alternatives": 0.05,
        },
        "band_abs": 0.07,
        "benchmark": "AOA",               # iShares Core Aggressive Allocation ETF
    },
}

_DEFAULT_MANDATE = "balanced"


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


def ips_table() -> Dict[str, Dict]:
    return _load("IPS_TARGETS_PATH", _SAMPLE_IPS)


def resolve_mandate(mandate: Optional[str]) -> str:
    """Map a free-text mandate to a known IPS profile key."""
    if not mandate:
        return _DEFAULT_MANDATE
    low = mandate.strip().lower()
    table = ips_table()
    if low in table:
        return low
    aliases = {
        "defensive": "conservative", "income": "conservative", "cautious": "conservative",
        "moderate": "balanced", "60/40": "balanced", "60-40": "balanced",
        "growth": "growth", "equity": "growth",
        "aggressive": "aggressive", "high_growth": "aggressive", "max_growth": "aggressive",
    }
    return aliases.get(low, _DEFAULT_MANDATE if low not in table else low)


def ips_for_mandate(mandate: Optional[str]) -> Dict:
    key = resolve_mandate(mandate)
    return ips_table().get(key, ips_table()[_DEFAULT_MANDATE])


def target_allocation(mandate: Optional[str]) -> Dict[str, float]:
    return dict(ips_for_mandate(mandate).get("targets", {}))


def tolerance_band(mandate: Optional[str]) -> float:
    return float(ips_for_mandate(mandate).get("band_abs", 0.05))


def benchmark_symbol(mandate: Optional[str]) -> str:
    return str(ips_for_mandate(mandate).get("benchmark", "AOM"))


# --------------------------------------------------------------------------- #
# Risk limits (config-driven; fixed at run time for reproducibility).
# --------------------------------------------------------------------------- #
_SAMPLE_RISK_LIMITS: Dict[str, float] = {
    "single_name_max_weight": 0.10,       # no one security above 10%
    "sector_max_weight": 0.30,            # no one sector above 30%
    "min_cash_weight": 0.00,              # minimum cash buffer (mandate may override via SAA)
    "max_volatility_annual": 0.20,        # annualised vol ceiling (warn above)
    "max_drawdown_alert": 0.20,           # alert if trailing max drawdown worse than -20%
    "var_95_1d_limit_pct": 0.04,          # 1-day 95% VaR ceiling, as a fraction of MV
    "max_tracking_error": 0.10,           # annualised TE ceiling vs benchmark
}


def risk_limits() -> Dict[str, float]:
    merged = dict(_SAMPLE_RISK_LIMITS)
    merged.update(_load("RISK_LIMITS_PATH", {}))
    return merged


# Per-mandate volatility ceilings (override the global one).
_MANDATE_VOL_CEILING: Dict[str, float] = {
    "conservative": 0.10, "balanced": 0.14, "growth": 0.18, "aggressive": 0.24,
}


def vol_ceiling(mandate: Optional[str]) -> float:
    return _MANDATE_VOL_CEILING.get(resolve_mandate(mandate),
                                    risk_limits().get("max_volatility_annual", 0.20))


# --------------------------------------------------------------------------- #
# Auto-fetch universe: liquid-ticker SEED POOL per asset class (fail-soft fallback).
# `universe.py` first tries to pull live constituent lists from public sources; if that
# is unavailable it falls back to this pool and enriches each name from market data.
# These are widely-held, liquid US-listed ETFs / names chosen only so the demo runs.
# --------------------------------------------------------------------------- #
_SAMPLE_UNIVERSE_SEED: Dict[str, List[str]] = {
    "equity": [
        "VTI", "VOO", "IVV",            # US broad
        "VEA", "EFA",                   # developed ex-US
        "VWO", "IEMG",                  # emerging markets
        "QQQ", "VUG", "VTV",            # style
        "AAPL", "MSFT", "JNJ", "JPM", "XOM",   # large-cap single names across sectors
    ],
    "fixed_income": [
        "AGG", "BND",                   # aggregate
        "TLT", "IEF",                   # treasuries
        "LQD",                          # IG credit
        "TIP",                          # inflation-linked
        "HYG",                          # high yield
    ],
    "cash": ["BIL", "SHV", "SGOV"],     # T-bill / ultra-short
    "alternatives": ["GLD", "IAU", "DBC", "VNQ"],   # gold, commodities, REITs
}


def universe_seed() -> Dict[str, List[str]]:
    return _load("UNIVERSE_SEED_PATH", _SAMPLE_UNIVERSE_SEED)


# --------------------------------------------------------------------------- #
# Mutual-fund category taxonomy (SEBI/AMFI naming). Country-agnostic definitions — SEBI's own
# published category rules, so confidence here is high independent of any ticker. Per-country
# real tickers live in each country's "mutual_fund_categories" dict below; a category absent
# from a country's dict has no real, currently-listed proxy confidently known for that country
# — it stays glossary-only there rather than being filled with an invented symbol (this data
# feeds live yfinance pricing and Construct's candidate universe, so a wrong ticker is bad
# financial data, not a cosmetic gap — same "unknown rather than guess" posture as
# research/deterministic.py).
# --------------------------------------------------------------------------- #
_MF_CATEGORY_INFO: Dict[str, Dict] = {
    "liquid": {"label": "Liquid Fund", "asset_class": AssetClass.CASH,
              "description": "Invests in short-term instruments maturing in up to 91 days."},
    "overnight": {"label": "Overnight Fund", "asset_class": AssetClass.CASH,
                 "description": "Invests in assets maturing in just one day."},
    "corporate_bond": {"label": "Corporate Bond Fund", "asset_class": AssetClass.FIXED_INCOME,
                       "description": "Puts most money into top-rated company bonds."},
    "gilt": {"label": "Gilt Fund", "asset_class": AssetClass.FIXED_INCOME,
            "description": "Puts most money into government securities."},
    "banking_psu": {"label": "Banking and PSU Fund", "asset_class": AssetClass.FIXED_INCOME,
                   "description": "Invests in debt papers from banks and public sector undertakings."},
    "large_cap": {"label": "Large-Cap Fund", "asset_class": AssetClass.EQUITY,
                 "description": "Invests mainly in the largest companies by market capitalisation."},
    "mid_cap": {"label": "Mid-Cap Fund", "asset_class": AssetClass.EQUITY,
               "description": "Invests mainly in mid-sized companies by market capitalisation."},
    "small_cap": {"label": "Small-Cap Fund", "asset_class": AssetClass.EQUITY,
                 "description": "Invests mainly in smaller companies by market capitalisation."},
    "sector_thematic": {"label": "Sector/Thematic Fund", "asset_class": AssetClass.EQUITY,
                        "description": "Concentrates in a specific sector or investment theme."},
    "elss": {"label": "ELSS", "asset_class": AssetClass.EQUITY,
            "description": "Tax-saving equity fund with a mandatory lock-in period."},
    "balanced_advantage": {"label": "Balanced Advantage / Dynamic Asset Allocation Fund",
                           "asset_class": AssetClass.MULTI_ASSET,
                           "description": "Dynamically shifts its equity/debt mix based on market conditions."},
    "aggressive_hybrid": {"label": "Aggressive Hybrid Fund", "asset_class": AssetClass.MULTI_ASSET,
                          "description": "Invests mostly in equity with a smaller allocation to debt."},
    "conservative_hybrid": {"label": "Conservative Hybrid Fund", "asset_class": AssetClass.MULTI_ASSET,
                            "description": "Invests mostly in debt with a smaller allocation to equity."},
    "arbitrage": {"label": "Arbitrage Fund", "asset_class": AssetClass.MULTI_ASSET,
                 "description": "Profits from price gaps between the cash and derivatives markets."},
    "multi_asset": {"label": "Multi Asset Allocation Fund", "asset_class": AssetClass.MULTI_ASSET,
                    "description": "Invests across three or more asset classes (e.g. equity, debt, gold)."},
    "index_etf": {"label": "Index Fund / ETF", "asset_class": AssetClass.EQUITY,
                 "description": "Passively tracks a specified market index."},
    "fund_of_funds": {"label": "Fund of Funds (FoF)", "asset_class": AssetClass.MULTI_ASSET,
                      "description": "Invests in units of other funds rather than directly in securities."},
    "international": {"label": "International / Global Fund", "asset_class": AssetClass.EQUITY,
                      "description": "Invests in companies or debt outside the investor's home country."},
    "gold": {"label": "Gold Fund", "asset_class": AssetClass.ALTERNATIVES,
            "description": "Invests in gold or gold-mining-related instruments."},
}


def mutual_fund_category_info() -> Dict[str, Dict]:
    return dict(_MF_CATEGORY_INFO)


# --------------------------------------------------------------------------- #
# Country universe: per country, the "most trusted" liquid names by market-cap tier, plus
# broad funds/ETFs. Illustrative curated lists (NOT index memberships) so construction runs
# with the free yfinance stack — yfinance then prices/ranks them live. Tickers use Yahoo
# suffixes (.NS India, .L UK, .T Japan, .DE Germany, .TO Canada, .AX Australia, .HK HK).
# Replace with your approved buy-list in production via COUNTRY_UNIVERSE_PATH.
#
# "mutual_fund_categories": {category_key: [tickers]} (keys match _MF_CATEGORY_INFO above) is
# the granular SEBI-style taxonomy — populated only where a real, currently-listed ticker is
# confidently known for that country; an absent category is glossary-only there, not a gap.
# --------------------------------------------------------------------------- #
_SAMPLE_COUNTRY_UNIVERSE: Dict[str, Dict] = {
    # Structure per country:
    #   large_cap / mid_cap / small_cap : equity stocks (one per sector, sector-diverse)
    #   etf_equity    : broad + sector equity ETFs
    #   etf_bond      : government + corporate bond ETFs
    #   etf_commodity : gold, silver, commodities
    #   etf_reit      : real estate / REIT ETFs
    #   etf_sector    : sector-specific ETFs (tech, pharma, bank, …)
    #   mutual_fund_equity  : equity MF category proxies (ETF-accessible)
    #   mutual_fund_debt    : debt/bond MF proxies
    #   mutual_fund_liquid  : liquid/money-market MF proxies
    #   fixed_income  : bond instruments (for the debt sleeve)
    #   cash          : liquid/T-bill instruments (for the cash sleeve)
    #   alternatives  : gold, commodities, REITs
    #   sector_hints  : {ticker: sector} — fills in when yfinance doesn't return a sector
    "united_states": {
        # Stocks — one per major sector to keep the pool diverse from the start
        "large_cap": [
            "AAPL",   # Technology
            "MSFT",   # Technology (software — distinct from hardware)
            "JNJ",    # Healthcare
            "JPM",    # Financials
            "PG",     # Consumer Staples
            "XOM",    # Energy
            "V",      # Financials (payments — distinct from banking)
            "UNH",    # Healthcare (insurance)
            "CAT",    # Industrials
            "AMZN",   # Consumer Discretionary / Technology
        ],
        "mid_cap": [
            "DECK",   # Consumer Discretionary
            "FND",    # Consumer Discretionary
            "SAIA",   # Industrials
            "EXP",    # Materials
            "WING",   # Consumer Staples / Restaurant
            "RXRX",   # Healthcare / Biotech
        ],
        "small_cap": [
            "CALM",   # Consumer Staples
            "SHAK",   # Consumer Discretionary
            "PRGS",   # Technology
            "CRVL",   # Healthcare
            "PLUS",   # Technology
        ],
        "sector_hints": {
            "AAPL": "Technology", "MSFT": "Technology", "JNJ": "Healthcare",
            "JPM": "Financial Services", "PG": "Consumer Defensive", "XOM": "Energy",
            "V": "Financial Services", "UNH": "Healthcare", "CAT": "Industrials",
            "AMZN": "Consumer Cyclical", "DECK": "Consumer Cyclical", "FND": "Consumer Cyclical",
            "SAIA": "Industrials", "EXP": "Basic Materials", "WING": "Consumer Cyclical",
            "RXRX": "Healthcare", "CALM": "Consumer Defensive", "SHAK": "Consumer Cyclical",
            "PRGS": "Technology", "CRVL": "Healthcare", "PLUS": "Technology",
        },
        # ETFs by type — ensures full coverage when ETF instrument is chosen
        "etf_equity":    ["VOO", "VTI", "VEA", "VWO", "QQQ"],       # broad equity
        "etf_bond":      ["AGG", "BND", "TLT", "IEF", "LQD", "TIP", "HYG"],
        "etf_commodity": ["GLD", "IAU", "DBC", "SLV"],
        "etf_reit":      ["VNQ", "SCHH"],
        "etf_sector":    ["XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP"],
        # Mutual fund category proxies (accessible via yfinance)
        "mutual_fund_equity": ["VFINX", "VIMAX", "NAESX", "VGTSX"],  # large/mid/small/intl
        "mutual_fund_debt":   ["VBMFX", "VTABX"],                     # US bond, global bond
        "mutual_fund_liquid": ["VMMXX", "VMFXX"],                     # money market
        # SEBI/AMFI-style granular categories (real tickers only — see _MF_CATEGORY_INFO)
        "mutual_fund_categories": {
            "liquid": ["BIL", "SHV"], "overnight": ["SGOV"], "corporate_bond": ["LQD"],
            "gilt": ["TLT", "IEF"], "large_cap": ["VOO", "VTI", "IVV"], "mid_cap": ["VO"],
            "small_cap": ["VB", "IWM"],
            "sector_thematic": ["XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP"],
            "balanced_advantage": ["AOM"], "aggressive_hybrid": ["AOR"],
            "conservative_hybrid": ["AOK"], "multi_asset": ["AOA"],
            "index_etf": ["VOO", "VTI", "IVV", "QQQ"],
            "international": ["VEA", "VWO", "VGTSX"], "gold": ["GLD", "IAU"],
        },
        # Sleeve instruments
        "fixed_income":  ["AGG", "BND", "TLT", "IEF", "LQD", "TIP", "HYG"],
        "cash":          ["BIL", "SHV", "SGOV"],
        "alternatives":  ["GLD", "IAU", "DBC", "VNQ"],
    },
    "india": {
        # Stocks — deliberately one per sector for mandatory sector diversity
        "large_cap": [
            "RELIANCE.NS",    # Energy / Conglomerate
            "TCS.NS",         # Information Technology
            "HDFCBANK.NS",    # Banking
            "INFY.NS",        # Information Technology
            "ICICIBANK.NS",   # Banking
            "HINDUNILVR.NS",  # FMCG / Consumer Staples
            "ITC.NS",         # FMCG / Cigarettes
            "LT.NS",          # Industrials / Capital Goods
            "SBIN.NS",        # Banking (public sector)
            "BHARTIARTL.NS",  # Telecom
            "SUNPHARMA.NS",   # Pharmaceuticals
            "MARUTI.NS",      # Automobiles
            "TATASTEEL.NS",   # Metals / Steel
            "NTPC.NS",        # Utilities / Power
            "WIPRO.NS",       # Information Technology
        ],
        "mid_cap": [
            "PERSISTENT.NS",  # IT Services
            "MPHASIS.NS",     # IT Services
            "TATAPOWER.NS",   # Utilities
            "ABCAPITAL.NS",   # Financial Services
            "FEDERALBNK.NS",  # Banking
            "ASHOKLEY.NS",    # Automobiles
            "PIIND.NS",       # Agro Chemicals
            "CANFINHOME.NS",  # Housing Finance
        ],
        "small_cap": [
            "IRCON.NS",       # Industrials / Infrastructure
            "RBLBANK.NS",     # Banking
            "CYIENT.NS",      # IT / Engineering
            "KEC.NS",         # Power Infra
            "TASTYBITE.NS",   # FMCG / Food
        ],
        "sector_hints": {
            "RELIANCE.NS": "Energy", "TCS.NS": "Technology", "HDFCBANK.NS": "Financial Services",
            "INFY.NS": "Technology", "ICICIBANK.NS": "Financial Services",
            "HINDUNILVR.NS": "Consumer Defensive", "ITC.NS": "Consumer Defensive",
            "LT.NS": "Industrials", "SBIN.NS": "Financial Services", "BHARTIARTL.NS": "Communication Services",
            "SUNPHARMA.NS": "Healthcare", "MARUTI.NS": "Consumer Cyclical",
            "TATASTEEL.NS": "Basic Materials", "NTPC.NS": "Utilities", "WIPRO.NS": "Technology",
            "PERSISTENT.NS": "Technology", "MPHASIS.NS": "Technology", "TATAPOWER.NS": "Utilities",
            "ABCAPITAL.NS": "Financial Services", "FEDERALBNK.NS": "Financial Services",
            "ASHOKLEY.NS": "Consumer Cyclical", "PIIND.NS": "Basic Materials",
            "CANFINHOME.NS": "Financial Services", "IRCON.NS": "Industrials",
            "RBLBANK.NS": "Financial Services", "CYIENT.NS": "Technology", "KEC.NS": "Industrials",
        },
        # ETFs by type (NSE-listed)
        "etf_equity":    ["NIFTYBEES.NS", "JUNIORBEES.NS", "SETFNIF50.NS"],
        "etf_bond":      ["LIQUIDBEES.NS", "LIQUIDCASE.NS"],
        "etf_commodity": ["GOLDBEES.NS", "SILVERBEES.NS", "AXISGOLD.NS"],
        "etf_reit":      [],                            # limited REIT ETF market in India
        "etf_sector":    ["BANKBEES.NS", "ITETF.NS"],   # banking, IT sector
        # Mutual fund category proxies (NSE ETFs representing fund categories)
        "mutual_fund_equity": ["NIFTYBEES.NS", "JUNIORBEES.NS", "BANKBEES.NS"],  # large/mid/sector equity
        "mutual_fund_debt":   ["LIQUIDBEES.NS", "HDFCLIQUID.NS"],                # debt/bond
        "mutual_fund_liquid": ["LIQUIDBEES.NS", "LIQUIDCASE.NS"],                # liquid
        # SEBI/AMFI-style granular categories (real tickers only — see _MF_CATEGORY_INFO).
        # Overnight/Corporate Bond/Gilt/Banking&PSU/ELSS/Hybrid funds aren't exchange-traded
        # in India (open-ended AMC schemes, no ticker) — glossary-only, omitted here.
        "mutual_fund_categories": {
            "liquid": ["LIQUIDBEES.NS", "LIQUIDCASE.NS"], "large_cap": ["NIFTYBEES.NS"],
            "mid_cap": ["JUNIORBEES.NS"], "sector_thematic": ["BANKBEES.NS", "ITETF.NS"],
            "index_etf": ["NIFTYBEES.NS", "JUNIORBEES.NS", "SETFNIF50.NS"],
            "gold": ["GOLDBEES.NS", "SILVERBEES.NS", "AXISGOLD.NS"],
        },
        # Sleeve instruments
        "fixed_income":  ["LIQUIDBEES.NS", "LIQUIDCASE.NS", "HDFCLIQUID.NS"],
        "cash":          ["LIQUIDBEES.NS", "LIQUIDCASE.NS"],
        "alternatives":  ["GOLDBEES.NS", "SILVERBEES.NS", "AXISGOLD.NS"],
    },
    "united_kingdom": {
        "large_cap": [
            "AZN.L",   # Healthcare
            "SHEL.L",  # Energy
            "HSBA.L",  # Financials
            "ULVR.L",  # Consumer Staples
            "BP.L",    # Energy
            "GSK.L",   # Healthcare
            "DGE.L",   # Consumer Staples (beverages)
            "RIO.L",   # Basic Materials
            "LSEG.L",  # Financials (exchanges)
            "BT-A.L",  # Telecom
        ],
        "mid_cap": ["WTB.L", "HWDN.L", "BBY.L", "MGGT.L"],
        "small_cap": ["TPK.L", "GHH.L"],
        "sector_hints": {
            "AZN.L": "Healthcare", "SHEL.L": "Energy", "HSBA.L": "Financial Services",
            "ULVR.L": "Consumer Defensive", "BP.L": "Energy", "GSK.L": "Healthcare",
            "DGE.L": "Consumer Defensive", "RIO.L": "Basic Materials",
            "LSEG.L": "Financial Services", "BT-A.L": "Communication Services",
        },
        "etf_equity":    ["ISF.L", "VUKE.L", "VMID.L"],
        "etf_bond":      ["IGLT.L", "CORP.L", "SLXX.L"],
        "etf_commodity": ["PHAU.L", "IGLN.L"],
        "etf_reit":      ["IUKP.L"],
        "etf_sector":    [],
        "mutual_fund_equity": ["ISF.L", "VMID.L"],
        "mutual_fund_debt":   ["IGLT.L", "CORP.L"],
        "mutual_fund_liquid": ["CSH2.L"],
        "mutual_fund_categories": {
            "liquid": ["CSH2.L"], "overnight": ["CSH2.L"], "corporate_bond": ["CORP.L"],
            "gilt": ["IGLT.L"], "large_cap": ["ISF.L"], "mid_cap": ["VMID.L"],
            "index_etf": ["ISF.L", "VUKE.L", "VMID.L"], "gold": ["PHAU.L", "IGLN.L"],
        },
        "fixed_income":  ["IGLT.L", "CORP.L", "SLXX.L"],
        "cash":          ["CSH2.L"],
        "alternatives":  ["PHAU.L", "IGLN.L"],
    },
    "japan": {
        "large_cap": [
            "7203.T",  # Automobiles (Toyota)
            "6758.T",  # Technology (Sony)
            "9984.T",  # Technology (SoftBank)
            "8306.T",  # Financials (MUFG)
            "6861.T",  # Technology (Keyence)
            "9433.T",  # Telecom (KDDI)
            "8035.T",  # Technology (Tokyo Electron)
            "4502.T",  # Healthcare (Takeda)
            "7974.T",  # Consumer Cyclical (Nintendo)
        ],
        "mid_cap": ["6471.T", "4661.T", "7733.T"],
        "small_cap": ["3092.T"],
        "sector_hints": {
            "7203.T": "Consumer Cyclical", "6758.T": "Technology", "9984.T": "Technology",
            "8306.T": "Financial Services", "6861.T": "Technology", "9433.T": "Communication Services",
            "8035.T": "Technology", "4502.T": "Healthcare", "7974.T": "Consumer Cyclical",
        },
        "etf_equity":    ["1306.T", "1321.T"],
        "etf_bond":      ["1677.T", "1482.T"],
        "etf_commodity": ["1540.T", "1328.T"],
        "etf_reit":      ["1343.T"],
        "etf_sector":    [],
        "mutual_fund_equity": ["1306.T", "1321.T"],
        "mutual_fund_debt":   ["1677.T"],
        "mutual_fund_liquid": ["1343.T"],
        "mutual_fund_categories": {
            "liquid": ["1343.T"], "large_cap": ["1306.T"],
            "index_etf": ["1306.T", "1321.T"], "gold": ["1540.T", "1328.T"],
        },
        "fixed_income":  ["1677.T", "1482.T"],
        "cash":          ["1343.T"],
        "alternatives":  ["1540.T", "1328.T"],
    },
    "germany": {
        "large_cap": [
            "SAP.DE",   # Technology
            "SIE.DE",   # Industrials
            "ALV.DE",   # Financials (insurance)
            "DTE.DE",   # Telecom
            "MBG.DE",   # Consumer Cyclical (Mercedes)
            "BAS.DE",   # Basic Materials (BASF)
            "BAYN.DE",  # Healthcare (Bayer)
            "MRK.DE",   # Healthcare
            "RWE.DE",   # Utilities
        ],
        "mid_cap": ["EVK.DE", "LEG.DE", "NEM.DE"],
        "small_cap": ["DRW3.DE"],
        "sector_hints": {
            "SAP.DE": "Technology", "SIE.DE": "Industrials", "ALV.DE": "Financial Services",
            "DTE.DE": "Communication Services", "MBG.DE": "Consumer Cyclical",
            "BAS.DE": "Basic Materials", "BAYN.DE": "Healthcare", "MRK.DE": "Healthcare",
            "RWE.DE": "Utilities",
        },
        "etf_equity":    ["EXS1.DE", "DBXD.DE"],
        "etf_bond":      ["EXX5.DE", "IBGM.DE"],
        "etf_commodity": ["EXS5.DE", "4GLD.DE"],
        "etf_reit":      [],
        "etf_sector":    [],
        "mutual_fund_equity": ["EXS1.DE", "DBXD.DE"],
        "mutual_fund_debt":   ["EXX5.DE"],
        "mutual_fund_liquid": ["XEON.DE"],
        "mutual_fund_categories": {
            "liquid": ["XEON.DE"], "overnight": ["XEON.DE"], "gilt": ["EXX5.DE", "IBGM.DE"],
            "large_cap": ["EXS1.DE"], "index_etf": ["EXS1.DE", "DBXD.DE"],
            "gold": ["EXS5.DE", "4GLD.DE"],
        },
        "fixed_income":  ["EXX5.DE", "IBGM.DE"],
        "cash":          ["XEON.DE"],
        "alternatives":  ["EXS5.DE", "4GLD.DE"],
    },
    "canada": {
        "large_cap": [
            "RY.TO",    # Financials
            "TD.TO",    # Financials
            "ENB.TO",   # Energy
            "CNR.TO",   # Industrials
            "BNS.TO",   # Financials
            "SHOP.TO",  # Technology
            "BCE.TO",   # Telecom
            "SU.TO",    # Energy
            "MFC.TO",   # Financials (insurance)
        ],
        "mid_cap": ["GIB-A.TO", "WCN.TO"],
        "small_cap": ["DOO.TO"],
        "sector_hints": {
            "RY.TO": "Financial Services", "TD.TO": "Financial Services", "ENB.TO": "Energy",
            "CNR.TO": "Industrials", "BNS.TO": "Financial Services", "SHOP.TO": "Technology",
            "BCE.TO": "Communication Services", "SU.TO": "Energy", "MFC.TO": "Financial Services",
        },
        "etf_equity":    ["XIU.TO", "XIC.TO"],
        "etf_bond":      ["ZAG.TO", "XBB.TO", "XGB.TO"],
        "etf_commodity": ["CGL.TO", "ZGD.TO"],
        "etf_reit":      ["XRE.TO"],
        "etf_sector":    [],
        "mutual_fund_equity": ["XIU.TO", "XIC.TO"],
        "mutual_fund_debt":   ["ZAG.TO"],
        "mutual_fund_liquid": ["ZST.TO"],
        "mutual_fund_categories": {
            "liquid": ["ZST.TO"], "overnight": ["PSA.TO"], "gilt": ["XGB.TO"],
            "large_cap": ["XIU.TO"], "index_etf": ["XIU.TO", "XIC.TO"],
            "gold": ["CGL.TO", "ZGD.TO"],
        },
        "fixed_income":  ["ZAG.TO", "XBB.TO", "XGB.TO"],
        "cash":          ["ZST.TO", "PSA.TO"],
        "alternatives":  ["CGL.TO", "ZGD.TO"],
    },
    "australia": {
        "large_cap": [
            "BHP.AX",  # Basic Materials
            "CBA.AX",  # Financials
            "CSL.AX",  # Healthcare
            "NAB.AX",  # Financials
            "WBC.AX",  # Financials
            "WES.AX",  # Consumer Cyclical
            "TLS.AX",  # Telecom
            "FMG.AX",  # Basic Materials (iron ore)
            "RIO.AX",  # Basic Materials
        ],
        "mid_cap": ["ALU.AX", "ALX.AX"],
        "small_cap": ["CTD.AX"],
        "sector_hints": {
            "BHP.AX": "Basic Materials", "CBA.AX": "Financial Services", "CSL.AX": "Healthcare",
            "NAB.AX": "Financial Services", "WBC.AX": "Financial Services",
            "WES.AX": "Consumer Cyclical", "TLS.AX": "Communication Services",
            "FMG.AX": "Basic Materials", "RIO.AX": "Basic Materials",
        },
        "etf_equity":    ["STW.AX", "VAS.AX"],
        "etf_bond":      ["IAF.AX", "BOND.AX", "GOVT.AX"],
        "etf_commodity": ["GOLD.AX", "QAU.AX"],
        "etf_reit":      ["VAP.AX"],
        "etf_sector":    [],
        "mutual_fund_equity": ["STW.AX", "VAS.AX"],
        "mutual_fund_debt":   ["IAF.AX"],
        "mutual_fund_liquid": ["AAA.AX"],
        "mutual_fund_categories": {
            "liquid": ["AAA.AX"], "overnight": ["BILL.AX"], "gilt": ["GOVT.AX"],
            "large_cap": ["STW.AX", "VAS.AX"], "index_etf": ["STW.AX", "VAS.AX"],
            "gold": ["GOLD.AX", "QAU.AX"],
        },
        "fixed_income":  ["IAF.AX", "BOND.AX", "GOVT.AX"],
        "cash":          ["AAA.AX", "BILL.AX"],
        "alternatives":  ["GOLD.AX", "QAU.AX"],
    },
    "china": {
        "large_cap": [
            "0700.HK",  # Technology (Tencent)
            "9988.HK",  # Consumer Cyclical (Alibaba)
            "3690.HK",  # Consumer Cyclical (Meituan)
            "1299.HK",  # Financials (AIA)
            "0939.HK",  # Financials (CCB)
            "2318.HK",  # Financials (Ping An)
            "0941.HK",  # Telecom (China Mobile)
            "2020.HK",  # Consumer Cyclical (ANTA)
            "1093.HK",  # Healthcare (CSPC Pharma)
        ],
        "mid_cap": ["1818.HK"],
        "small_cap": [],
        "sector_hints": {
            "0700.HK": "Technology", "9988.HK": "Consumer Cyclical", "3690.HK": "Consumer Cyclical",
            "1299.HK": "Financial Services", "0939.HK": "Financial Services",
            "2318.HK": "Financial Services", "0941.HK": "Communication Services",
            "2020.HK": "Consumer Cyclical", "1093.HK": "Healthcare",
        },
        "etf_equity":    ["2800.HK", "2823.HK"],
        "etf_bond":      ["3141.HK", "2821.HK"],
        "etf_commodity": ["2840.HK", "3081.HK"],
        "etf_reit":      [],
        "etf_sector":    [],
        "mutual_fund_equity": ["2800.HK", "2823.HK"],
        "mutual_fund_debt":   ["3141.HK"],
        "mutual_fund_liquid": ["3053.HK"],
        "mutual_fund_categories": {
            "liquid": ["3053.HK"], "large_cap": ["2800.HK"],
            "index_etf": ["2800.HK", "2823.HK"], "gold": ["2840.HK", "3081.HK"],
        },
        "fixed_income":  ["3141.HK", "2821.HK"],
        "cash":          ["3053.HK"],
        "alternatives":  ["2840.HK", "3081.HK"],
    },
}

# Map free-text country input -> canonical country key above.
_COUNTRY_ALIASES: Dict[str, str] = {
    "us": "united_states", "usa": "united_states", "united states": "united_states",
    "u.s.": "united_states", "america": "united_states", "united_states": "united_states",
    "india": "india", "in": "india", "bharat": "india",
    "uk": "united_kingdom", "u.k.": "united_kingdom", "britain": "united_kingdom",
    "england": "united_kingdom", "united kingdom": "united_kingdom", "united_kingdom": "united_kingdom",
    "japan": "japan", "jp": "japan",
    "germany": "germany", "de": "germany", "deutschland": "germany",
    "canada": "canada", "ca": "canada",
    "australia": "australia", "au": "australia", "aus": "australia",
    "china": "china", "cn": "china", "hong kong": "china", "hongkong": "china", "hk": "china",
}


def country_universe() -> Dict[str, Dict[str, List[str]]]:
    return _load("COUNTRY_UNIVERSE_PATH", _SAMPLE_COUNTRY_UNIVERSE)


def normalize_country(name: Optional[str]) -> Optional[str]:
    """Map a free-text country to a canonical key, or None if unknown."""
    if not name:
        return None
    low = name.strip().lower()
    if low in country_universe():
        return low
    return _COUNTRY_ALIASES.get(low)


def available_countries() -> List[str]:
    return sorted(country_universe().keys())


# Market-cap thresholds (USD-equivalent) for classifying a name from its yfinance marketCap.
# Production can localise these per market via MARKET_CAP_THRESHOLDS_PATH.
_SAMPLE_MARKET_CAP_THRESHOLDS: Dict[str, float] = {
    "large_min": 10_000_000_000.0,        # >= $10B = large cap
    "mid_min": 2_000_000_000.0,           # $2B–$10B = mid cap; below = small cap
}


def market_cap_thresholds() -> Dict[str, float]:
    merged = dict(_SAMPLE_MARKET_CAP_THRESHOLDS)
    merged.update(_load("MARKET_CAP_THRESHOLDS_PATH", {}))
    return merged


def classify_market_cap(market_cap_value: Optional[float]) -> str:
    """Classify a market-cap value into large/mid/small. 'unknown' when absent."""
    if market_cap_value is None or market_cap_value <= 0:
        return "unknown"
    t = market_cap_thresholds()
    if market_cap_value >= t["large_min"]:
        return "large_cap"
    if market_cap_value >= t["mid_min"]:
        return "mid_cap"
    return "small_cap"


# Thematic baskets — keyword in the requested theme -> representative liquid ETFs/names.
_THEME_BASKETS: Dict[str, List[str]] = {
    "clean energy": ["ICLN", "TAN", "QCLN", "FAN"],
    "renewable": ["ICLN", "TAN", "QCLN"],
    "ai": ["BOTZ", "ROBO", "AIQ", "NVDA", "MSFT"],
    "artificial intelligence": ["BOTZ", "ROBO", "AIQ", "NVDA"],
    "semiconductor": ["SMH", "SOXX", "NVDA", "AVGO"],
    "technology": ["QQQ", "VGT", "XLK"],
    "healthcare": ["XLV", "VHT", "IHI"],
    "biotech": ["IBB", "XBI"],
    "cybersecurity": ["CIBR", "BUG", "HACK"],
    "ev": ["DRIV", "LIT", "TSLA"],
    "electric vehicle": ["DRIV", "LIT"],
    "water": ["PHO", "FIW"],
    "infrastructure": ["IFRA", "PAVE"],
    "esg": ["ESGU", "ESGV", "SUSA"],
    "gold": ["GLD", "IAU", "GDX"],
    "real estate": ["VNQ", "SCHH"],
    "dividend": ["SCHD", "VYM", "DVY"],
}


def theme_basket(theme: Optional[str]) -> List[str]:
    """Return representative tickers for a free-text theme (substring match)."""
    if not theme:
        return []
    low = theme.lower()
    for needle, syms in _THEME_BASKETS.items():
        if needle in low:
            return list(syms)
    return []


# --------------------------------------------------------------------------- #
# Mapping yfinance quoteType / asset-type strings onto our AssetClass vocabulary.
# --------------------------------------------------------------------------- #
def asset_class_from_quote_type(quote_type: Optional[str], symbol: str = "") -> AssetClass:
    qt = (quote_type or "").upper()
    sym = (symbol or "").upper()
    if qt in ("EQUITY",):
        return AssetClass.EQUITY
    if qt in ("ETF", "MUTUALFUND", "INDEX"):
        # We cannot know an ETF's exposure from quoteType alone; the universe seed pool
        # carries the intended sleeve. Default ETFs to multi_asset only when unknown.
        return AssetClass.MULTI_ASSET
    if qt in ("CRYPTOCURRENCY",):
        return AssetClass.ALTERNATIVES
    if qt in ("CURRENCY",):
        return AssetClass.CASH
    if "BOND" in qt or sym in ("AGG", "BND", "TLT", "IEF", "LQD", "TIP", "HYG"):
        return AssetClass.FIXED_INCOME
    return AssetClass.OTHER


# --------------------------------------------------------------------------- #
# Scoring: factor weights + severity multipliers + score->health-band cut-offs.
# Score is 0..100, higher = riskier / less healthy. Mirrors the sibling scorecard.
# --------------------------------------------------------------------------- #
FACTOR_WEIGHTS: Dict[RiskFactorType, float] = {
    RiskFactorType.ALLOCATION_DRIFT: 20.0,
    RiskFactorType.CONCENTRATION: 18.0,
    RiskFactorType.VOLATILITY: 18.0,
    RiskFactorType.DRAWDOWN: 16.0,
    RiskFactorType.CASHFLOW_VARIANCE: 10.0,
    RiskFactorType.RISK_LIMIT_BREACH: 22.0,
    RiskFactorType.EMERGING_RISK: 12.0,
    RiskFactorType.COUNTRY_RISK: 10.0,
}

SEVERITY_MULT: Dict[str, float] = {"low": 0.0, "medium": 0.6, "high": 1.0, "critical": 1.5}

# (max_score_inclusive, health_band). First match wins.
SCORE_TO_BAND: List[tuple] = [
    (25.0, HealthBand.HEALTHY),
    (50.0, HealthBand.WATCH),
    (75.0, HealthBand.ELEVATED),
    (100.0, HealthBand.CRITICAL),
]


def health_band_for_score(score: float) -> HealthBand:
    for max_score, band in SCORE_TO_BAND:
        if score <= max_score:
            return band
    return HealthBand.CRITICAL


# Assumed annual income yield per asset class — the OFFLINE basis for "expected" income in
# cash-flow variance (refined per-symbol by the live yfinance dividend rate when available).
# Illustrative; override via ASSUMED_YIELDS_PATH.
_SAMPLE_ASSUMED_YIELDS: Dict[str, float] = {
    "equity": 0.015,
    "fixed_income": 0.040,
    "cash": 0.050,
    "alternatives": 0.030,
    "real_estate": 0.035,
    "commodity": 0.000,
    "multi_asset": 0.020,
    "other": 0.000,
}


def assumed_yields() -> Dict[str, float]:
    merged = dict(_SAMPLE_ASSUMED_YIELDS)
    merged.update(_load("ASSUMED_YIELDS_PATH", {}))
    return merged


# Cash-flow variance thresholds (fraction of expected) for severity.
CASHFLOW_TOLERANCE = float(os.getenv("CASHFLOW_TOLERANCE", "0.10"))      # ±10% = on track
CASHFLOW_SHORTFALL_HIGH = float(os.getenv("CASHFLOW_SHORTFALL_HIGH", "0.40"))   # >40% short = high
# Expected income below this absolute amount is immaterial — a 'missing' distribution this
# small is not flagged (avoids noise from trivially-yielding positions).
MATERIAL_INCOME_FLOOR = float(os.getenv("MATERIAL_INCOME_FLOOR", "250"))
# Absolute size (base currency) above which an unexpected outflow/fee is itself flagged.
UNEXPECTED_FLOW_ABS = float(os.getenv("UNEXPECTED_FLOW_ABS", "10000"))

# Assumed round-trip transaction cost for rebalancing estimates (bps of traded notional).
TRANSACTION_COST_BPS = float(os.getenv("TRANSACTION_COST_BPS", "5"))

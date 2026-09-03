"""
Build the candidate universe for portfolio construction — enriched live from yfinance.

The candidate set is driven by the INVESTOR PROFILE on the request and comes from a single
FREE source, yfinance (Yahoo Finance, no API key):
  * geography — global, one country, or a list of countries (`reference.country_universe`,
    swappable via `COUNTRY_UNIVERSE_PATH`);
  * style — large / mid / small / multi-cap, fund-of-funds, thematic, or ELSS;
  * the EQUITY sleeve is drawn from the "most trusted" curated names of the chosen countries
    at the chosen cap tier; the DEBT / cash / alternatives sleeves come from broad funds; and
  * every candidate is enriched with a live yfinance quote (price, sector, country, market
    cap, dividend) plus a history-derived annualised volatility AND expected return so the
    constructor can rank "by expected return".

It **fails soft**: with no network it returns the curated names with whatever is
cached/available, so the constructor still runs. yfinance is the only market-data source —
no paid feeds, no scraped index lists.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel

from . import analytics
from .logconf import get_logger
from .marketdata import get_price_history, get_quote, market_data_available
from .reference import (
    asset_class_from_quote_type,
    classify_market_cap,
    country_universe,
    mutual_fund_category_info,
    normalize_country,
    theme_basket,
    universe_seed,
)
from .research.deterministic import (
    country_risk_penalty,
    meets_credit_floor,
    should_exclude_country,
)
from .schemas import (
    AssetClass,
    ConstructionConstraints,
    ConstructionRequest,
    CountryResearchProfile,
    GeographyMode,
    InstrumentType,
    MarketCap,
    PortfolioStyle,
    Region,
)

_log = get_logger()

# Default country set for GLOBAL mode (major liquid markets; bounds the live fetch).
_GLOBAL_DEFAULT_COUNTRIES = ["united_states", "united_kingdom", "japan", "germany", "india"]

_SEED_REGION: Dict[str, Region] = {
    "AGG": Region.US, "BND": Region.US, "TLT": Region.US, "IEF": Region.US,
    "LQD": Region.US, "TIP": Region.US, "HYG": Region.US,
    "BIL": Region.US, "SHV": Region.US, "SGOV": Region.US,
    "GLD": Region.GLOBAL, "IAU": Region.GLOBAL, "DBC": Region.GLOBAL, "VNQ": Region.US,
}

_COUNTRY_REGION = {
    "united_states": Region.US, "canada": Region.US,
    "united_kingdom": Region.DEVELOPED_EX_US, "japan": Region.DEVELOPED_EX_US,
    "germany": Region.DEVELOPED_EX_US, "australia": Region.DEVELOPED_EX_US,
    "india": Region.EMERGING_MARKETS, "china": Region.EMERGING_MARKETS,
}

_SLEEVE_AC = {
    "equity": AssetClass.EQUITY, "fixed_income": AssetClass.FIXED_INCOME,
    "cash": AssetClass.CASH, "alternatives": AssetClass.ALTERNATIVES,
}

_CAP_TIERS = {
    PortfolioStyle.LARGE_CAP: ["large_cap"],
    PortfolioStyle.MID_CAP: ["mid_cap"],
    PortfolioStyle.SMALL_CAP: ["small_cap"],
    PortfolioStyle.MULTI_CAP: ["large_cap", "mid_cap", "small_cap"],
    PortfolioStyle.ELSS: ["large_cap", "etf"],          # tax-advantaged equity: blue chips + broad funds
    PortfolioStyle.FUND_OF_FUNDS: ["etf"],
}


class Candidate(BaseModel):
    symbol: str
    name: Optional[str] = None
    asset_class: AssetClass = AssetClass.OTHER
    sector: Optional[str] = None
    region: Region = Region.OTHER
    country: Optional[str] = None
    market_cap: MarketCap = MarketCap.UNKNOWN
    market_cap_value: Optional[float] = None
    instrument_type: InstrumentType = InstrumentType.OTHER
    price: Optional[float] = None
    volatility: Optional[float] = None             # annualised, from history (None if unknown)
    expected_return: Optional[float] = None        # annualised historical mean (noisy proxy)
    dividend_yield: Optional[float] = None
    pe_ratio: Optional[float] = None               # trailing/forward P/E (stocks only)
    expense_ratio: Optional[float] = None          # fund/ETF expense ratio
    has_market_data: bool = False


def _instrument_from_quote(quote_type: Optional[str], fallback: InstrumentType) -> InstrumentType:
    # Curated hint wins for ETF / MUTUAL_FUND: yfinance quoteType is unreliable for non-US
    # listings (e.g. NSE ETFs often return quoteType="EQUITY" which would wrongly classify
    # NIFTYBEES.NS as a stock and make it invisible in ETF-only filters).
    if fallback in (InstrumentType.ETF, InstrumentType.MUTUAL_FUND):
        return fallback
    qt = (quote_type or "").upper()
    if qt == "EQUITY":
        return InstrumentType.STOCK
    if qt == "ETF":
        return InstrumentType.ETF
    if qt in ("MUTUALFUND", "MUTUALFUNDANDUNIT"):
        return InstrumentType.MUTUAL_FUND
    return fallback


def _make_candidate(
    sym: str,
    sleeve_ac: Optional[AssetClass],
    do_fetch: bool,
    country_hint: Optional[str] = None,
    cap_hint: Optional[str] = None,
    instrument_hint: InstrumentType = InstrumentType.OTHER,
    sector_hint: Optional[str] = None,
) -> Candidate:
    sym = sym.upper()
    quote = get_quote(sym) if do_fetch else None
    vol = exp_ret = None
    if do_fetch:
        rets = analytics.daily_returns(get_price_history(sym))
        vol = analytics.annualised_volatility(rets)
        exp_ret = analytics.annualised_return(rets)
    ac = sleeve_ac or asset_class_from_quote_type(quote.quote_type if quote else None, sym)
    div_y = None
    if quote and quote.dividend_yield is not None:
        div_y = quote.dividend_yield if quote.dividend_yield < 1 else quote.dividend_yield / 100.0
    mcap_val = quote.market_cap if quote else None
    # Cap classification: the curated tier hint (from reference data) takes priority.
    # Raw yfinance market_cap is in local currency (INR for India, JPY for Japan…) so a
    # direct USD-threshold comparison misclassifies non-US stocks. Our curated lists already
    # assign the correct tier; use it as the primary source and yfinance as a fallback only
    # when no curated hint is available.
    if cap_hint and cap_hint in ("large_cap", "mid_cap", "small_cap"):
        cap_class = cap_hint
    else:
        cap_class = classify_market_cap(mcap_val)
    country = (quote.country if quote and quote.country else None) or _pretty_country(country_hint)
    region = _SEED_REGION.get(sym) or _COUNTRY_REGION.get(country_hint or "", Region.OTHER)
    # Sector: yfinance value takes priority; fall back to the curated sector_hint
    sector = (quote.sector if quote and quote.sector else None) or sector_hint
    return Candidate(
        symbol=sym,
        name=(quote.name if quote and quote.name else None),
        asset_class=ac,
        sector=sector,
        region=region,
        country=country,
        market_cap=_cap_enum(cap_class),
        market_cap_value=mcap_val,
        instrument_type=_instrument_from_quote(quote.quote_type if quote else None, instrument_hint),
        price=(quote.price if quote else None),
        volatility=vol,
        expected_return=exp_ret,
        dividend_yield=div_y,
        pe_ratio=(quote.pe_ratio if quote else None),
        expense_ratio=(quote.expense_ratio if quote else None),
        has_market_data=bool(quote and quote.price),
    )


def resolve_countries(request: ConstructionRequest) -> List[str]:
    if request.geography_mode == GeographyMode.GLOBAL:
        requested = [normalize_country(c) for c in request.countries]
        requested = [c for c in requested if c]
        return requested or list(_GLOBAL_DEFAULT_COUNTRIES)
    resolved = [normalize_country(c) for c in request.countries]
    resolved = [c for c in resolved if c]
    if not resolved:
        return ["united_states"]
    if request.geography_mode == GeographyMode.COUNTRY_SPECIFIC:
        return resolved[:1]
    return resolved


def _equity_symbols(countries: List[str], style: PortfolioStyle, theme: Optional[str]):
    """Return [(symbol, country, cap_tier, instrument_hint, sector_hint)] for the equity sleeve."""
    cu = country_universe()
    out: List[tuple] = []

    if style == PortfolioStyle.THEMATIC:
        for sym in theme_basket(theme):
            out.append((sym, None, "large_cap", InstrumentType.OTHER, None))
        if not out:
            for c in countries:
                cmap = cu.get(c, {})
                for sym in cmap.get("etf_equity", cmap.get("etf", [])):
                    out.append((sym, c, "etf", InstrumentType.ETF, None))
        return out

    if style == PortfolioStyle.FUND_OF_FUNDS:
        for c in countries:
            cmap = cu.get(c, {})
            for sym in cmap.get("etf_equity", cmap.get("etf", [])):
                out.append((sym, c, "large_cap", InstrumentType.ETF, None))
        return out

    tiers = _CAP_TIERS.get(style, ["large_cap", "mid_cap", "small_cap"])
    for c in countries:
        cmap = cu.get(c, {})
        hints = cmap.get("sector_hints", {})
        for tier in tiers:
            if tier == "etf":
                for sym in cmap.get("etf_equity", cmap.get("etf", [])):
                    out.append((sym, c, "large_cap", InstrumentType.ETF, None))
            else:
                for sym in cmap.get(tier, []):
                    out.append((sym, c, tier, InstrumentType.STOCK, hints.get(sym)))
    return out


def _etf_symbols(countries: List[str]) -> List[tuple]:
    """All ETF sub-type symbols: equity/bond/commodity/REIT/sector per country.

    Returns [(symbol, country, etf_sub_type, asset_class)] where etf_sub_type is one of:
    etf_equity|etf_bond|etf_commodity|etf_reit|etf_sector.
    """
    cu = country_universe()
    out: List[tuple] = []
    etf_ac = {
        "etf_equity": AssetClass.EQUITY,
        "etf_bond": AssetClass.FIXED_INCOME,
        "etf_commodity": AssetClass.ALTERNATIVES,
        "etf_reit": AssetClass.REAL_ESTATE,
        "etf_sector": AssetClass.EQUITY,
    }
    for c in countries:
        cmap = cu.get(c, {})
        for sub, ac in etf_ac.items():
            for sym in cmap.get(sub, []):
                out.append((sym, c, sub, ac))
    return out


_LEGACY_MF_AC = {
    "mutual_fund_equity": AssetClass.EQUITY,
    "mutual_fund_debt": AssetClass.FIXED_INCOME,
    "mutual_fund_liquid": AssetClass.CASH,
}


def _mutual_fund_symbols(countries: List[str]) -> List[tuple]:
    """Mutual fund proxies per country: (symbol, country, asset_class, category_label).

    Prefers the granular SEBI/AMFI-style `mutual_fund_categories` taxonomy (real ticker only
    where one is confidently known — see reference.py's `_MF_CATEGORY_INFO`), which carries a
    specific label (e.g. "Gilt Fund") used as the position's sector. Falls back to the legacy
    broad equity/debt/liquid buckets (label=None, same as before) for any ticker not already
    covered by the granular taxonomy, so nothing that worked before regresses.
    """
    cu = country_universe()
    info = mutual_fund_category_info()
    out: List[tuple] = []
    covered: set = set()
    for c in countries:
        cmap = cu.get(c, {})
        for cat_key, tickers in cmap.get("mutual_fund_categories", {}).items():
            meta = info.get(cat_key)
            if not meta:
                continue
            for sym in tickers:
                out.append((sym, c, meta["asset_class"], meta["label"]))
                covered.add((sym, c))
        for cat, ac in _LEGACY_MF_AC.items():
            for sym in cmap.get(cat, []):
                if (sym, c) not in covered:
                    out.append((sym, c, ac, None))
    return out


def _meets_ratio_constraints(c: Candidate, constraints: ConstructionConstraints) -> bool:
    """Ratio-style filters (dividend yield floor, P/E ceiling, expense-ratio ceiling).

    Never rejects on missing data — only on a data point that's actually present and violates
    a threshold the caller set. A `None` field means "unknown," not "fails the floor/ceiling."
    """
    if (constraints.min_dividend_yield is not None and c.dividend_yield is not None
            and c.dividend_yield < constraints.min_dividend_yield):
        return False
    if (constraints.max_pe_ratio is not None and c.instrument_type == InstrumentType.STOCK
            and c.pe_ratio is not None and c.pe_ratio > constraints.max_pe_ratio):
        return False
    if (constraints.max_expense_ratio is not None
            and c.instrument_type in (InstrumentType.ETF, InstrumentType.MUTUAL_FUND)
            and c.expense_ratio is not None and c.expense_ratio > constraints.max_expense_ratio):
        return False
    return True


def build_universe(
    request: ConstructionRequest,
    country_research: Optional[Dict[str, CountryResearchProfile]] = None,
) -> List[Candidate]:
    """Assemble and enrich the candidate universe per the investor profile (yfinance only).

    `country_research` (keyed by the same normalized country key `resolve_countries()`
    produces) is optional — when supplied, a country whose research reads as acutely risky
    excludes its candidates the same way a symbol/sector `exclusions` entry does, and
    `min_credit_rating` is checked against the bond-risk agent's country-level read for
    fixed-income candidates (see research/deterministic.py — yfinance has no per-security
    rating, so this is a country-level proxy, not a securities-grade rating).
    """
    exclusions = {x.strip().upper() for x in request.constraints.exclusions}
    md = market_data_available()
    candidates: List[Candidate] = []
    seen: set = set()

    def _add(sym, ac, country=None, cap=None, inst=InstrumentType.OTHER, sector_hint=None):
        sym = sym.upper()
        if sym in exclusions:
            return
        # Deduplicate by (symbol, asset_class): the same ETF can appear in both the
        # fixed_income and cash sleeves (e.g. LIQUIDBEES.NS), but each gets its own
        # candidate record so the constructor can fund each sleeve independently.
        key = (sym, ac)
        if key in seen:
            return
        seen.add(key)
        candidate = _make_candidate(sym, ac, md, country, cap, inst, sector_hint)
        if not _meets_ratio_constraints(candidate, request.constraints):
            return
        profile = country_research.get(country) if (country_research and country) else None
        if profile is not None:
            if should_exclude_country(profile):
                return
            if (ac == AssetClass.FIXED_INCOME and request.constraints.min_credit_rating
                    and not meets_credit_floor(profile, request.constraints.min_credit_rating)):
                return
            # Soft ranking penalty, applied once here (not threaded through every ranking/
            # selection function in construct/deterministic.py — they all sort by
            # `expected_return`, so adjusting it at the source reaches every one of them for
            # free). Bounded well below what a hard exclusion above already does.
            if candidate.expected_return is not None:
                candidate.expected_return -= country_risk_penalty(profile)
        candidates.append(candidate)

    if request.universe_symbols:
        for sym in request.universe_symbols:
            _add(sym, None)
        _log.info("universe: using %d user-supplied symbol(s)", len(request.universe_symbols))
    else:
        countries = resolve_countries(request)
        cu = country_universe()
        seed = universe_seed()
        is_country_specific = (request.geography_mode != GeographyMode.GLOBAL and countries)

        # Determine which instrument types are active.
        # Empty filter means ALL types → build a fully diversified combined universe.
        active = set(request.instrument_types) if request.instrument_types else None
        want_stocks = active is None or InstrumentType.STOCK in active
        want_etf    = active is None or InstrumentType.ETF   in active
        want_mf     = active is None or InstrumentType.MUTUAL_FUND in active

        # --- Equity / stock sleeve ---
        if want_stocks or want_etf:
            for sym, country, cap, inst, shint in _equity_symbols(
                    countries, request.style, request.theme):
                # When only ETFs are requested, skip pure-stock tickers
                if not want_stocks and inst == InstrumentType.STOCK:
                    continue
                # When only stocks are requested, skip ETF tickers
                if not want_etf and inst == InstrumentType.ETF:
                    continue
                _add(sym, AssetClass.EQUITY, country, cap, inst, sector_hint=shint)

        # --- Mutual fund universe (when MF is explicitly or implicitly requested) ---
        # Runs BEFORE the plain ETF pass below: several tickers (e.g. GOLDBEES.NS) are
        # curated under both an etf_* bucket and a granular mutual_fund_categories entry
        # with the same resulting asset class, and _add()'s (symbol, asset_class) dedup keeps
        # whichever call claims that key first — this ordering lets the specific category
        # label (e.g. "Gold Fund") win over the plain ETF pass's blank sector_hint.
        if want_mf:
            for sym, country, ac, label in _mutual_fund_symbols(countries):
                _add(sym, ac, country, None, InstrumentType.MUTUAL_FUND, sector_hint=label)

        # --- ETF diversified universe (when ETFs are explicitly or implicitly requested) ---
        if want_etf:
            for sym, country, sub, ac in _etf_symbols(countries):
                _add(sym, ac, country, None, InstrumentType.ETF)

        # --- Non-equity sleeves (fixed_income / cash / alternatives) ---
        # RULE: country-specific → use only that country's instruments; global → use seed pool.
        for sleeve in ("fixed_income", "cash", "alternatives"):
            ac = _SLEEVE_AC[sleeve]
            if is_country_specific:
                country_syms: List[tuple] = []
                for ckey in countries:
                    for sym in cu.get(ckey, {}).get(sleeve, []):
                        country_syms.append((sym, ckey))
                if country_syms:
                    for sym, ckey in country_syms:
                        _add(sym, ac, ckey, None, InstrumentType.ETF)
                else:
                    _log.info(
                        "universe: no %s instruments found for %s — sleeve will be unfunded. "
                        "Add country-specific tickers to COUNTRY_UNIVERSE_PATH to fund it.",
                        sleeve, countries,
                    )
            else:
                for sym in seed.get(sleeve, []):
                    _add(sym, ac, None, None, InstrumentType.ETF)

        _log.info("universe: geography=%s countries=%s style=%s want_stocks=%s want_etf=%s want_mf=%s",
                  request.geography_mode.value, countries, request.style.value,
                  want_stocks, want_etf, want_mf)

    # instrument-type filter (applies to the whole universe when set)
    if request.instrument_types:
        keep = set(request.instrument_types)
        filtered = [c for c in candidates
                    if c.instrument_type in keep or c.asset_class != AssetClass.EQUITY]
        if filtered:
            candidates = filtered

    # legacy region-preference filter
    pref = request.constraints.region_preference
    if pref:
        keep = {pref, Region.GLOBAL, Region.US}
        filtered = [c for c in candidates if c.region in keep]
        if filtered:
            candidates = filtered

    n_priced = sum(1 for c in candidates if c.has_market_data)
    _log.info("universe: %d candidate(s) (%d priced with live market data)",
              len(candidates), n_priced)
    return candidates


def candidates_by_sleeve(candidates: List[Candidate]) -> Dict[AssetClass, List[Candidate]]:
    out: Dict[AssetClass, List[Candidate]] = {}
    for c in candidates:
        out.setdefault(c.asset_class, []).append(c)
    return out


def _cap_enum(cap_class: str) -> MarketCap:
    return {
        "large_cap": MarketCap.LARGE_CAP, "mid_cap": MarketCap.MID_CAP,
        "small_cap": MarketCap.SMALL_CAP,
    }.get(cap_class, MarketCap.UNKNOWN)


def _pretty_country(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return key.replace("_", " ").title()

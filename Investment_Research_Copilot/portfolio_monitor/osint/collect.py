"""
Emerging-risk evidence collection (free DuckDuckGo news).

**Key rule:** yfinance uses tickers; DuckDuckGo uses company names.
This module always converts to a company-name search term before querying DuckDuckGo, and
passes the ticker as `related_symbol` (for internal cross-referencing only, never in the
query). A `fallback_query` (just the company name) is passed to the search wrapper so that
"no results" on a complex query is automatically retried with a simpler one.

Exchange-suffix stripping: `.NS`, `.L`, `.T`, `.DE`, `.TO`, `.AX`, `.HK`, `.BO` are ticker
artifacts that DuckDuckGo does not understand; they are always removed from search terms.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from ..logconf import get_logger
from ..schemas import AllocationBreakdown, NewsEvidenceItem, RiskDimension
from .search import news_search, search_available

_log = get_logger()

# Exchange suffixes that DuckDuckGo should never see.
_EXCHANGE_SUFFIX = re.compile(
    r'\.(NS|BO|L|T|DE|TO|AX|HK|PA|AS|BR|LS|SW|SI|KS|KQ|SS|SZ|BK|BSE|NSE)$',
    re.IGNORECASE,
)


def _clean_ticker(symbol: str) -> str:
    """Strip exchange suffix from a ticker, e.g. 'RELIANCE.NS' → 'RELIANCE'."""
    return _EXCHANGE_SUFFIX.sub("", symbol.strip()).strip()


def _search_name(symbol: str, symbol_to_name: Dict[str, str]) -> str:
    """Return the best search term for DuckDuckGo: company name if known, else clean ticker.

    'Reliance Industries' finds far more news than 'RELIANCE' or 'RELIANCE.NS'.
    """
    name = symbol_to_name.get(symbol.upper(), "").strip()
    if name:
        return name
    return _clean_ticker(symbol)


def _max_results() -> int:
    try:
        return int(os.getenv("PM_NEWS_MAX_RESULTS", "4"))
    except (TypeError, ValueError):
        return 4


# Risk angle → query template. {subject} is filled with the COMPANY NAME (not the ticker).
_COMPANY_ANGLES = {
    RiskDimension.COMPANY_SPECIFIC: '{subject} stock risk warning OR lawsuit OR downgrade OR guidance cut',
    RiskDimension.REGULATORY: '{subject} regulatory investigation OR fine OR probe',
}
_SECTOR_ANGLES = {
    RiskDimension.SECTOR: '{subject} sector outlook risk selloff 2026',
}
_MACRO_ANGLES = {
    RiskDimension.MARKET: 'stock market volatility selloff risk this week',
    RiskDimension.MACRO: 'inflation interest rates recession risk outlook',
    RiskDimension.GEOPOLITICAL: 'geopolitical risk markets tariffs conflict',
}

# Broad index / fund tickers — no point searching for "VTI risk warning"
_SKIP_FUNDS = frozenset([
    "VTI","VOO","IVV","VEA","VWO","IEMG","AGG","BND","TLT","IEF","LQD","TIP","HYG",
    "BIL","SHV","SGOV","GLD","IAU","DBC","VNQ","QQQ","NIFTYBEES.NS","JUNIORBEES.NS",
    "ISF.L","VUKE.L","1306.T","EXS1.DE","XIU.TO","STW.AX","2800.HK",
])


def collect_evidence(
    allocation: AllocationBreakdown,
    symbol_to_name: Optional[Dict[str, str]] = None,
    top_n_holdings: int = 3,
    top_n_sectors: int = 2,
    region: Optional[str] = None,
) -> List[NewsEvidenceItem]:
    """Gather public-news evidence across company / sector / macro risk angles.

    `symbol_to_name` maps uppercase ticker → company name (e.g. {'RELIANCE.NS': 'Reliance
    Industries'}). When supplied, searches use the company name, which gives far better results
    from DuckDuckGo than exchange-suffixed tickers.
    """
    if not search_available():
        _log.info("news: ddgs not available — emerging-risk scan will be empty.")
        return []

    s2n: Dict[str, str] = {k.upper(): v for k, v in (symbol_to_name or {}).items()}
    max_r = _max_results()
    evidence: List[NewsEvidenceItem] = []

    # --- company-specific: top holdings that are not broad index funds ---
    symbols = [
        b.name for b in allocation.by_position
        if b.name.upper() not in _SKIP_FUNDS
    ][:top_n_holdings]

    for sym in symbols:
        name = _search_name(sym, s2n)
        for dim, tmpl in _COMPANY_ANGLES.items():
            full_query = tmpl.format(subject=name)
            fallback = name                        # just the company name on retry
            evidence.extend(_run(dim, full_query, max_r, region,
                                 fallback_query=fallback, related_symbol=sym))

    # --- sector ---
    sectors = [
        b.name for b in allocation.by_sector
        if b.name not in ("Unclassified", "Fixed Income", "Cash")
    ][:top_n_sectors]
    for sec in sectors:
        for dim, tmpl in _SECTOR_ANGLES.items():
            evidence.extend(_run(dim, tmpl.format(subject=sec), max_r, region))

    # --- macro / market / geopolitical (no company name needed) ---
    for dim, q in _MACRO_ANGLES.items():
        evidence.extend(_run(dim, q, max_r, region))

    _log.info("news: collected %d evidence item(s) for %d holding(s) searched",
              len(evidence), len(symbols))
    return evidence


def _run(
    dim: RiskDimension,
    query: str,
    max_r: int,
    region: Optional[str],
    fallback_query: Optional[str] = None,
    related_symbol: Optional[str] = None,
) -> List[NewsEvidenceItem]:
    items = []
    for r in news_search(query, max_results=max_r, region=region,
                         fallback_query=fallback_query):
        items.append(NewsEvidenceItem(
            dimension=dim,
            query=query,
            title=r.get("title"),
            url=r.get("url"),
            snippet=r.get("snippet"),
            source=r.get("source", "news"),
            published=r.get("published"),
            related_symbol=related_symbol,
        ))
    return items

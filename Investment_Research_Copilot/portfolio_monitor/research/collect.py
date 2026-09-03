"""
Per-country query templates for the 5 research topics — geopolitical, sectoral, bond risk,
bank FD/deposit rates, and market risk. Same pattern as `osint/collect.py`'s per-symbol/
per-sector query templates, substituting a country name for a company name; reuses
`osint/search.py`'s DuckDuckGo wrappers directly rather than duplicating an HTTP client.
"""
from __future__ import annotations

import os
from typing import List, Optional

from ..osint.search import news_search, web_search
from ..schemas import NewsEvidenceItem, RiskDimension

_GEOPOLITICAL_QUERY = "{country} geopolitical risk outlook 2026"
_SECTORAL_QUERY = "{country} stock market sector outlook 2026"
_BOND_QUERY = "{country} sovereign bond yield credit rating risk 2026"
_FD_RATE_QUERY = "{country} bank fixed deposit interest rate 2026"
_MARKET_QUERY = "{country} stock market risk volatility outlook 2026"
_GROWTH_QUERY = "{country} economic growth outlook fastest growing sectors 2026"


def _max_results() -> int:
    return int(os.getenv("PM_NEWS_MAX_RESULTS", "4"))


def _collect(
    query: str, dimension: RiskDimension, region: Optional[str], use_web: bool,
) -> List[NewsEvidenceItem]:
    search_fn = web_search if use_web else news_search
    items: List[NewsEvidenceItem] = []
    for r in search_fn(query, max_results=_max_results(), region=region):
        items.append(NewsEvidenceItem(
            dimension=dimension, query=query, title=r.get("title"), url=r.get("url"),
            snippet=r.get("snippet"), source=r.get("source", "web" if use_web else "news"),
            published=r.get("published"),
        ))
    return items


def collect_geopolitical(country: str, region: Optional[str] = None) -> List[NewsEvidenceItem]:
    return _collect(_GEOPOLITICAL_QUERY.format(country=country),
                    RiskDimension.GEOPOLITICAL, region, use_web=False)


def collect_sectoral(country: str, region: Optional[str] = None) -> List[NewsEvidenceItem]:
    return _collect(_SECTORAL_QUERY.format(country=country),
                    RiskDimension.SECTOR, region, use_web=False)


def collect_bond_risk(country: str, region: Optional[str] = None) -> List[NewsEvidenceItem]:
    return _collect(_BOND_QUERY.format(country=country),
                    RiskDimension.FIXED_INCOME, region, use_web=False)


def collect_fd_rates(country: str, region: Optional[str] = None) -> List[NewsEvidenceItem]:
    # Bank rate pages tend to be finance-site content rather than news articles.
    return _collect(_FD_RATE_QUERY.format(country=country),
                    RiskDimension.MACRO, region, use_web=True)


def collect_market_risk(country: str, region: Optional[str] = None) -> List[NewsEvidenceItem]:
    return _collect(_MARKET_QUERY.format(country=country),
                    RiskDimension.MARKET, region, use_web=False)


def collect_growth_outlook(country: str, region: Optional[str] = None) -> List[NewsEvidenceItem]:
    return _collect(_GROWTH_QUERY.format(country=country),
                    RiskDimension.SECTOR, region, use_web=False)

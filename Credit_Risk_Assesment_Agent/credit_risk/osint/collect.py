"""
Evidence collection — turn a company name + a few details into public web findings.

For each sentiment angle (general news, financial performance, adverse, credit
distress, governance, market view) we build a handful of plain search queries from the
seed, run them against the free DuckDuckGo backends (web + news), and keep the results
as `WebEvidenceItem`s. Duplicate links are removed.

No analysis happens here — this step only *gathers*. The LLM turns the gathered evidence
into a grounded `WebSentimentResult` in `sentiment.py`, and all risk scoring stays
deterministic.
"""

from __future__ import annotations

import os

from ..logconf import get_logger
from ..schemas import CompanySeed, SentimentDimension, WebEvidenceItem
from .search import news_search, web_search

_log = get_logger()

DEFAULT_OSINT_MAX_RESULTS = 5
SNIPPET_DEDUP_LENGTH = 120


def _max_results() -> int:
    """Return per-query result cap, falling back to default on malformed env."""
    try:
        return int(os.getenv("OSINT_MAX_RESULTS", str(DEFAULT_OSINT_MAX_RESULTS)))
    except (TypeError, ValueError):
        return DEFAULT_OSINT_MAX_RESULTS


def _ctx(seed: CompanySeed) -> str:
    """A short disambiguation suffix, e.g. 'Acme Ltd UK manufacturing'."""
    context_bits = [seed.country, seed.industry]
    return " ".join(bit for bit in context_bits if bit)


def _build_queries(seed: CompanySeed) -> dict[SentimentDimension, list[str]]:
    """Plain-language queries per sentiment angle, built from whatever the user supplied."""
    company_name = seed.company_name
    context = _ctx(seed)
    company_names = [company_name] + list(seed.aliases)

    queries: dict[SentimentDimension, list[str]] = {
        SentimentDimension.GENERAL_NEWS: [
            f"{company_name} {context}".strip(),
            f"{company_name} news",
        ],
        SentimentDimension.FINANCIAL_PERFORMANCE: [
            f"{company_name} results OR revenue OR profit OR guidance",
            f"{company_name} earnings OR financial performance",
        ],
        SentimentDimension.ADVERSE: [
            f"{company_name} fraud OR investigation OR lawsuit OR scandal",
            f"{company_name} regulatory OR penalty OR fine OR sanctions",
        ],
        SentimentDimension.CREDIT_DISTRESS: [
            f"{company_name} debt OR default OR restructuring OR covenant breach",
            f"{company_name} layoffs OR insolvency OR bankruptcy OR liquidation",
        ],
        SentimentDimension.GOVERNANCE: [
            f"{company_name} CEO OR board OR management resignation",
            f"{company_name} accounting OR audit OR going concern",
        ],
        SentimentDimension.MARKET_VIEW: [
            f"{company_name} analyst OR rating OR downgrade OR outlook",
            f"{company_name} investor OR shares OR bond",
        ],
    }
    # add alias variants for the highest-value angles
    for alias in company_names[1:]:
        queries[SentimentDimension.ADVERSE].append(
            f"{alias} fraud OR investigation OR lawsuit"
        )
    return queries


# Angles where fresh news adds the most signal.
_NEWS_ANGLES = {
    SentimentDimension.GENERAL_NEWS,
    SentimentDimension.FINANCIAL_PERFORMANCE,
    SentimentDimension.ADVERSE,
    SentimentDimension.CREDIT_DISTRESS,
}


class _EvidenceDeduper:
    """Track seen URLs/snippets and append only new evidence items."""

    def __init__(self) -> None:
        self.seen_urls = set()
        self.seen_snippets = set()
        self.evidence: list[WebEvidenceItem] = []

    def add(self, dimension: SentimentDimension, query: str, rows: list[dict]) -> int:
        """Append new rows for one query; returns the count kept."""
        kept_count = 0
        for row in rows:
            if self._is_duplicate(row):
                continue
            self._remember(row)
            self.evidence.append(
                WebEvidenceItem(
                    dimension=dimension,
                    query=query,
                    title=row.get("title"),
                    url=row.get("url"),
                    snippet=(row.get("snippet") or "").strip() or None,
                    source=row.get("source", "web"),
                    published=row.get("published"),
                )
            )
            kept_count += 1
        return kept_count

    def _dedup_key(self, row: dict) -> str:
        """Dedup key: URL when present, else the snippet prefix."""
        url = row.get("url")
        if url:
            return url
        return (row.get("snippet") or "").strip()[:SNIPPET_DEDUP_LENGTH]

    def _is_duplicate(self, row: dict) -> bool:
        """Check whether a row was already collected."""
        url = row.get("url")
        snippet_prefix = (row.get("snippet") or "").strip()[:SNIPPET_DEDUP_LENGTH]
        if not url and not snippet_prefix:
            return True
        if url and url in self.seen_urls:
            return True
        return not url and snippet_prefix in self.seen_snippets

    def _remember(self, row: dict) -> None:
        """Record a row's URL/snippet as seen."""
        url = row.get("url")
        if url:
            self.seen_urls.add(url)
        else:
            snippet_prefix = (row.get("snippet") or "").strip()[:SNIPPET_DEDUP_LENGTH]
            self.seen_snippets.add(snippet_prefix)


def collect_evidence(seed: CompanySeed) -> list[WebEvidenceItem]:
    """Run every query across the free backends and return de-duplicated evidence."""
    results_per_query = _max_results()
    region = seed.search_region
    queries = _build_queries(seed)
    deduper = _EvidenceDeduper()

    _log.info(
        "COLLECT start: company=%r region=%r results_per_query=%d",
        seed.company_name,
        region,
        results_per_query,
    )
    _log.debug("context suffix: %r", _ctx(seed))

    items_per_dimension: dict[SentimentDimension, int] = {}
    for dimension, query_list in queries.items():
        _log.debug("ANGLE: %s", dimension.value)
        for query in query_list:
            if not query.strip():
                continue
            kept = deduper.add(
                dimension,
                query,
                web_search(query, max_results=results_per_query, region=region),
            )
            _log.debug("  web  · %r -> kept %d new item(s)", query, kept)
            items_per_dimension[dimension] = (
                items_per_dimension.get(dimension, 0) + kept
            )
            if dimension in _NEWS_ANGLES:
                kept_news = deduper.add(
                    dimension,
                    query,
                    news_search(query, max_results=results_per_query, region=region),
                )
                _log.debug("  news · %r -> kept %d new item(s)", query, kept_news)
                items_per_dimension[dimension] = (
                    items_per_dimension.get(dimension, 0) + kept_news
                )

    collected = deduper.evidence
    _log.info(
        "COLLECT done: %d total evidence item(s) — %s",
        len(collected),
        ", ".join(f"{dim.value}={count}" for dim, count in items_per_dimension.items()),
    )
    if not collected:
        _log.warning(
            "COLLECT: zero evidence collected — backend returned nothing "
            "(no search library installed, or network unavailable)."
        )
    return collected

"""
Evidence collection — turn a person's name + details into a pile of public findings.

For each of the eight research angles (biography, professional, company performance,
adverse media, political exposure, social/behavioral, relationships, wealth) we build a
handful of plain search queries from the seed details, run them against the free search
backends, and keep the results as `EvidenceItem`s. Duplicate links are removed.

No analysis happens here — this step only *gathers*. The LLM turns the gathered evidence
into a structured profile in `profile.py`, and all risk scoring stays deterministic.
"""

from __future__ import annotations

import os
from typing import Any

from ..schemas import EvidenceItem, OsintDimension, SubjectSeed
from .search import dbg, news_search, web_search, wikipedia_summary

_DEFAULT_MAX_RESULTS = 5
_SNIPPET_DEDUP_LENGTH = 120
_NEWS_ENRICHED_DIMENSIONS = (
    OsintDimension.ADVERSE_MEDIA,
    OsintDimension.COMPANY_PERFORMANCE,
)


def _max_results() -> int:
    try:
        return int(os.getenv("OSINT_MAX_RESULTS", str(_DEFAULT_MAX_RESULTS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESULTS


def _disambiguation_suffix(seed: SubjectSeed) -> str:
    """A short disambiguation suffix, e.g. 'London businessman' or just the location."""
    context_bits = [seed.location, seed.nationality]
    if seed.work_history:
        context_bits.append(seed.work_history[0])
    return " ".join(bit for bit in context_bits if bit)


def _build_queries(seed: SubjectSeed) -> dict[OsintDimension, list[str]]:
    """Plain-language queries per research angle, built from whatever the user supplied."""
    full_name = seed.full_name
    context = _disambiguation_suffix(seed)
    career_hint = seed.work_history[0] if seed.work_history else "career company"

    queries: dict[OsintDimension, list[str]] = {
        OsintDimension.BIOGRAPHY: [
            f"{full_name} {context}".strip(),
            f"{full_name} biography profile",
        ],
        OsintDimension.PROFESSIONAL: [
            f"{full_name} director OR founder OR partner OR CEO",
            f"{full_name} {career_hint}",
        ],
        OsintDimension.COMPANY_PERFORMANCE: [
            f"{company} revenue OR results OR performance OR funding"
            for company in seed.known_companies
        ]
        or [f"{full_name} company revenue results"],
        OsintDimension.ADVERSE_MEDIA: [
            f"{full_name} fraud OR investigation OR lawsuit OR scandal",
            f"{full_name} sanctions OR money laundering OR regulatory action",
        ],
        OsintDimension.PEP: [
            f"{full_name} politician OR government OR minister OR official OR political donation",
        ],
        OsintDimension.SOCIAL_BEHAVIORAL: [
            f"{full_name} interview OR statement OR controversy",
            f"{full_name} linkedin OR twitter OR instagram profile",
        ],
        OsintDimension.RELATIONSHIPS: [
            f"{full_name} wife OR husband OR spouse OR married OR partner",
            f"{full_name} family OR business associate",
        ],
        OsintDimension.WEALTH: [
            f"{full_name} net worth OR wealth OR fortune OR rich list",
            f"{full_name} source of wealth OR how made money",
        ],
    }
    for alias in seed.aliases:
        queries[OsintDimension.ADVERSE_MEDIA].append(f"{alias} fraud OR investigation OR lawsuit")
    return queries


class _EvidenceCollector:
    """De-duplicating accumulator for collected evidence items."""

    def __init__(self) -> None:
        self.seen_urls: set[str] = set()
        self.seen_snippets: set[str] = set()
        self.evidence: list[EvidenceItem] = []

    def add(self, dimension: OsintDimension, query: str, rows: list[dict[str, Any]]) -> int:
        kept_count = 0
        for row in rows:
            url = row.get("url")
            snippet = (row.get("snippet") or "").strip()
            dedup_key = url or snippet[:_SNIPPET_DEDUP_LENGTH]
            if not dedup_key:
                continue
            if url and url in self.seen_urls:
                continue
            if not url and snippet[:_SNIPPET_DEDUP_LENGTH] in self.seen_snippets:
                continue
            if url:
                self.seen_urls.add(url)
            else:
                self.seen_snippets.add(snippet[:_SNIPPET_DEDUP_LENGTH])
            self.evidence.append(
                EvidenceItem(
                    dimension=dimension,
                    query=query,
                    title=row.get("title"),
                    url=url,
                    snippet=snippet or None,
                    source=row.get("source", "web"),
                    published=row.get("published"),
                )
            )
            kept_count += 1
        return kept_count


def _collect_wikipedia(seed: SubjectSeed, collector: _EvidenceCollector) -> None:
    kept = collector.add(
        OsintDimension.BIOGRAPHY,
        f"{seed.full_name} (wikipedia)",
        wikipedia_summary(seed.full_name),
    )
    dbg(f"[biography] wikipedia -> kept {kept} new item(s)")


def _collect_angle(
    dimension: OsintDimension,
    queries: list[str],
    region: str | None,
    per_query: int,
    collector: _EvidenceCollector,
    per_dimension: dict[OsintDimension, int],
) -> None:
    dbg("-" * 72)
    non_empty = [query for query in queries if query.strip()]
    dbg(f"ANGLE: {dimension.value}  ({len(non_empty)} query/queries)")
    for query in queries:
        if not query.strip():
            continue
        kept = collector.add(
            dimension, query, web_search(query, max_results=per_query, region=region)
        )
        dbg(f"  web  · {query!r} -> kept {kept} new item(s)")
        per_dimension[dimension] = per_dimension.get(dimension, 0) + kept
        if dimension in _NEWS_ENRICHED_DIMENSIONS:
            news_kept = collector.add(dimension, query, news_search(query, max_results=per_query))
            dbg(f"  news · {query!r} -> kept {news_kept} new item(s)")
            per_dimension[dimension] = per_dimension.get(dimension, 0) + news_kept


def collect_evidence(seed: SubjectSeed) -> list[EvidenceItem]:
    """Run every query across the free backends and return de-duplicated evidence."""
    per_query = _max_results()
    region = seed.search_region
    queries = _build_queries(seed)
    collector = _EvidenceCollector()

    dbg("=" * 72)
    dbg(
        f"COLLECT start: subject={seed.full_name!r} region={region!r} results_per_query={per_query}"
    )
    dbg(f"context suffix: {_disambiguation_suffix(seed)!r}")
    dbg(f"angles to search: {[dimension.value for dimension in queries]}")

    _collect_wikipedia(seed, collector)

    per_dimension: dict[OsintDimension, int] = {}
    for dimension, dimension_queries in queries.items():
        _collect_angle(dimension, dimension_queries, region, per_query, collector, per_dimension)

    dbg("=" * 72)
    dbg(f"COLLECT done: {len(collector.evidence)} total evidence item(s)")
    for dimension in queries:
        dbg(f"   {dimension.value:<20} {per_dimension.get(dimension, 0)} item(s)")
    if not collector.evidence:
        dbg(
            "!! ZERO evidence collected — every backend returned nothing. "
            "Likely: no search library installed, or network/Google throttling."
        )
    return collector.evidence

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
from typing import Dict, List

from ..logconf import get_logger
from ..schemas import CompanySeed, SentimentDimension, WebEvidenceItem
from .search import news_search, web_search

_log = get_logger()


def _max_results() -> int:
    try:
        return int(os.getenv("OSINT_MAX_RESULTS", "5"))
    except (TypeError, ValueError):
        return 5


def _ctx(seed: CompanySeed) -> str:
    """A short disambiguation suffix, e.g. 'Acme Ltd UK manufacturing'."""
    bits = [seed.country, seed.industry]
    return " ".join(b for b in bits if b)


def _build_queries(seed: CompanySeed) -> Dict[SentimentDimension, List[str]]:
    """Plain-language queries per sentiment angle, built from whatever the user supplied."""
    name = seed.company_name
    ctx = _ctx(seed)
    names = [name] + list(seed.aliases)

    q: Dict[SentimentDimension, List[str]] = {
        SentimentDimension.GENERAL_NEWS: [
            f"{name} {ctx}".strip(), f"{name} news"],
        SentimentDimension.FINANCIAL_PERFORMANCE: [
            f"{name} results OR revenue OR profit OR guidance",
            f"{name} earnings OR financial performance"],
        SentimentDimension.ADVERSE: [
            f"{name} fraud OR investigation OR lawsuit OR scandal",
            f"{name} regulatory OR penalty OR fine OR sanctions"],
        SentimentDimension.CREDIT_DISTRESS: [
            f"{name} debt OR default OR restructuring OR covenant breach",
            f"{name} layoffs OR insolvency OR bankruptcy OR liquidation"],
        SentimentDimension.GOVERNANCE: [
            f"{name} CEO OR board OR management resignation",
            f"{name} accounting OR audit OR going concern"],
        SentimentDimension.MARKET_VIEW: [
            f"{name} analyst OR rating OR downgrade OR outlook",
            f"{name} investor OR shares OR bond"],
    }
    # add alias variants for the highest-value angles
    for alt in names[1:]:
        q[SentimentDimension.ADVERSE].append(f"{alt} fraud OR investigation OR lawsuit")
    return q


# Angles where fresh news adds the most signal.
_NEWS_ANGLES = {
    SentimentDimension.GENERAL_NEWS,
    SentimentDimension.FINANCIAL_PERFORMANCE,
    SentimentDimension.ADVERSE,
    SentimentDimension.CREDIT_DISTRESS,
}


def collect_evidence(seed: CompanySeed) -> List[WebEvidenceItem]:
    """Run every query across the free backends and return de-duplicated evidence."""
    per_query = _max_results()
    region = seed.search_region
    queries = _build_queries(seed)
    seen_urls = set()
    seen_snippets = set()
    evidence: List[WebEvidenceItem] = []

    _log.info("COLLECT start: company=%r region=%r results_per_query=%d",
               seed.company_name, region, per_query)
    _log.debug("context suffix: %r", _ctx(seed))

    def add(dim: SentimentDimension, query: str, rows: List[Dict]) -> int:
        kept = 0
        for r in rows:
            url = r.get("url")
            snippet = (r.get("snippet") or "").strip()
            key = url or snippet[:120]
            if not key:
                continue
            if url and url in seen_urls:
                continue
            if not url and snippet[:120] in seen_snippets:
                continue
            if url:
                seen_urls.add(url)
            else:
                seen_snippets.add(snippet[:120])
            evidence.append(WebEvidenceItem(
                dimension=dim, query=query,
                title=r.get("title"), url=url, snippet=snippet or None,
                source=r.get("source", "web"), published=r.get("published"),
            ))
            kept += 1
        return kept

    per_dim: Dict[SentimentDimension, int] = {}
    for dim, qlist in queries.items():
        _log.debug("ANGLE: %s", dim.value)
        for query in qlist:
            if not query.strip():
                continue
            kept = add(dim, query, web_search(query, max_results=per_query, region=region))
            _log.debug("  web  · %r -> kept %d new item(s)", query, kept)
            per_dim[dim] = per_dim.get(dim, 0) + kept
            if dim in _NEWS_ANGLES:
                keptn = add(dim, query, news_search(query, max_results=per_query, region=region))
                _log.debug("  news · %r -> kept %d new item(s)", query, keptn)
                per_dim[dim] = per_dim.get(dim, 0) + keptn

    _log.info("COLLECT done: %d total evidence item(s) — %s", len(evidence),
               ", ".join(f"{dim.value}={count}" for dim, count in per_dim.items()))
    if not evidence:
        _log.warning("COLLECT: zero evidence collected — backend returned nothing "
                      "(no search library installed, or network unavailable).")
    return evidence

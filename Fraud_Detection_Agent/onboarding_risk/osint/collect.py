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
from typing import Dict, List

from ..schemas import EvidenceItem, OsintDimension, SubjectSeed
from .search import dbg, news_search, web_search, wikipedia_summary


def _max_results() -> int:
    try:
        return int(os.getenv("OSINT_MAX_RESULTS", "5"))
    except (TypeError, ValueError):
        return 5


def _ctx(seed: SubjectSeed) -> str:
    """A short disambiguation suffix, e.g. 'London businessman' or just the location."""
    bits = [seed.location, seed.nationality]
    if seed.work_history:
        bits.append(seed.work_history[0])
    return " ".join(b for b in bits if b)


def _build_queries(seed: SubjectSeed) -> Dict[OsintDimension, List[str]]:
    """Plain-language queries per research angle, built from whatever the user supplied."""
    name = seed.full_name
    ctx = _ctx(seed)
    names = [name] + list(seed.aliases)

    q: Dict[OsintDimension, List[str]] = {
        OsintDimension.BIOGRAPHY: [f"{name} {ctx}".strip(), f"{name} biography profile"],
        OsintDimension.PROFESSIONAL: [
            f"{name} director OR founder OR partner OR CEO",
            f"{name} {seed.work_history[0]}" if seed.work_history else f"{name} career company",
        ],
        OsintDimension.COMPANY_PERFORMANCE: [
            f"{c} revenue OR results OR performance OR funding" for c in seed.known_companies
        ] or [f"{name} company revenue results"],
        OsintDimension.ADVERSE_MEDIA: [
            f"{name} fraud OR investigation OR lawsuit OR scandal",
            f"{name} sanctions OR money laundering OR regulatory action",
        ],
        OsintDimension.PEP: [
            f"{name} politician OR government OR minister OR official OR political donation",
        ],
        OsintDimension.SOCIAL_BEHAVIORAL: [
            f"{name} interview OR statement OR controversy",
            f"{name} linkedin OR twitter OR instagram profile",
        ],
        OsintDimension.RELATIONSHIPS: [
            f"{name} wife OR husband OR spouse OR married OR partner",
            f"{name} family OR business associate",
        ],
        OsintDimension.WEALTH: [
            f"{name} net worth OR wealth OR fortune OR rich list",
            f"{name} source of wealth OR how made money",
        ],
    }
    # add alias variants for the highest-value angles
    for alt in names[1:]:
        q[OsintDimension.ADVERSE_MEDIA].append(f"{alt} fraud OR investigation OR lawsuit")
    return q


def collect_evidence(seed: SubjectSeed) -> List[EvidenceItem]:
    """Run every query across the free backends and return de-duplicated evidence."""
    per_query = _max_results()
    region = seed.search_region            # None -> search() falls back to OSINT_COUNTRY env
    queries = _build_queries(seed)
    seen_urls = set()
    seen_snippets = set()
    evidence: List[EvidenceItem] = []

    dbg("=" * 72)
    dbg(f"COLLECT start: subject={seed.full_name!r} region={region!r} "
        f"results_per_query={per_query}")
    dbg(f"context suffix: {_ctx(seed)!r}")
    dbg(f"angles to search: {[d.value for d in queries]}")

    def add(dim: OsintDimension, query: str, rows: List[Dict]) -> int:
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
            evidence.append(EvidenceItem(
                dimension=dim, query=query,
                title=r.get("title"), url=url, snippet=snippet or None,
                source=r.get("source", "web"), published=r.get("published"),
            ))
            kept += 1
        return kept

    # Biography also pulls Wikipedia (one authoritative summary if it exists).
    wk = add(OsintDimension.BIOGRAPHY, f"{seed.full_name} (wikipedia)",
             wikipedia_summary(seed.full_name))
    dbg(f"[biography] wikipedia -> kept {wk} new item(s)")

    per_dim: Dict[OsintDimension, int] = {}
    for dim, qlist in queries.items():
        dbg("-" * 72)
        dbg(f"ANGLE: {dim.value}  ({len([x for x in qlist if x.strip()])} query/queries)")
        for query in qlist:
            if not query.strip():
                continue
            kept = add(dim, query, web_search(query, max_results=per_query, region=region))
            dbg(f"  web  · {query!r} -> kept {kept} new item(s)")
            per_dim[dim] = per_dim.get(dim, 0) + kept
            # adverse media and company performance benefit from fresh news too
            if dim in (OsintDimension.ADVERSE_MEDIA, OsintDimension.COMPANY_PERFORMANCE):
                keptn = add(dim, query, news_search(query, max_results=per_query))
                dbg(f"  news · {query!r} -> kept {keptn} new item(s)")
                per_dim[dim] = per_dim.get(dim, 0) + keptn

    dbg("=" * 72)
    dbg(f"COLLECT done: {len(evidence)} total evidence item(s)")
    for dim in queries:
        dbg(f"   {dim.value:<20} {per_dim.get(dim, 0)} item(s)")
    if not evidence:
        dbg("!! ZERO evidence collected — every backend returned nothing. "
            "Likely: no search library installed, or network/Google throttling.")
    return evidence

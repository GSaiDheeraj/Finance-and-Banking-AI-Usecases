"""
Deterministic screening engine.

NO LLM calls. Every party (the applicant, related parties and resolved UBOs) is matched
against the configured reference lists — PEP, sanctions, adverse media — and every
party country is resolved against the high-risk-jurisdiction list. Matching is plain
normalized string comparison so the *presence* of a hit never depends on a model
sampling decision: identical inputs produce identical hits.

Name matching uses a normalized token-overlap (Jaccard) score with an exact-match
short-circuit. The threshold is deliberately conservative for a first pass; in
production this layer is replaced by your screening provider's matching engine, but the
contract (a list of `WatchlistHit`) stays the same.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .reference import (
    adverse_media_list,
    high_risk_jurisdictions,
    pep_list,
    sanctions_list,
)
from .schemas import GeographyHit, Party, WatchlistHit

_MATCH_THRESHOLD = 0.85          # token-overlap score at/above which two names "match"


def _normalize(name: str) -> List[str]:
    """Lower-case, strip punctuation and common entity suffixes -> token list."""
    text = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    tokens = [t for t in text.split() if t]
    stop = {"mr", "mrs", "ms", "dr", "ltd", "limited", "llc", "inc", "plc",
            "the", "and", "fze", "sa", "ag", "gmbh", "co", "company",
            "holdings", "holding", "trust", "foundation", "fund"}
    return [t for t in tokens if t not in stop]


def _name_score(a: str, b: str) -> float:
    ta, tb = set(_normalize(a)), set(_normalize(b))
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _best_hit(name: str, entries: List[Dict], name_key: str = "name") -> Optional[Tuple[Dict, float]]:
    best: Optional[Tuple[Dict, float]] = None
    for entry in entries:
        score = _name_score(name, str(entry.get(name_key, "")))
        if score >= _MATCH_THRESHOLD and (best is None or score > best[1]):
            best = (entry, score)
    return best


def screen_parties(parties: List[Party]) -> Tuple[List[WatchlistHit], List[GeographyHit]]:
    """Screen all parties against PEP / sanctions / adverse-media lists and geographies."""
    peps = pep_list()
    sanctions = sanctions_list()
    adverse = adverse_media_list()
    jurisdictions = high_risk_jurisdictions()

    hits: List[WatchlistHit] = []
    geo_hits: List[GeographyHit] = []

    for p in parties:
        # --- sanctions (hard stop downstream) ---
        s = _best_hit(p.name, sanctions)
        if s:
            entry, score = s
            hits.append(WatchlistHit(
                party_id=p.party_id, party_name=p.name, list_type="sanctions",
                matched_name=entry.get("name", ""), match_score=round(score, 3),
                source_list=entry.get("list"), details=entry.get("program"),
            ))

        # --- PEP ---
        pep = _best_hit(p.name, peps)
        if pep:
            entry, score = pep
            hits.append(WatchlistHit(
                party_id=p.party_id, party_name=p.name, list_type="pep",
                matched_name=entry.get("name", ""), match_score=round(score, 3),
                details=entry.get("position"), tier=entry.get("tier"),
            ))
        elif p.declared_pep:
            # self-declared PEP on the form even if absent from the sample list
            hits.append(WatchlistHit(
                party_id=p.party_id, party_name=p.name, list_type="pep",
                matched_name=p.name, match_score=1.0,
                details="Self-declared PEP on onboarding form.", tier="medium",
            ))

        # --- adverse media ---
        am = _best_hit(p.name, adverse)
        if am:
            entry, score = am
            hits.append(WatchlistHit(
                party_id=p.party_id, party_name=p.name, list_type="adverse_media",
                matched_name=entry.get("name", ""), match_score=round(score, 3),
                details=f"[{entry.get('category')}] {entry.get('summary', '')}",
            ))

        # --- geography ---
        if p.country:
            entry = jurisdictions.get(p.country.strip().lower())
            if entry and entry.get("tier") in ("high", "prohibited"):
                geo_hits.append(GeographyHit(
                    party_id=p.party_id, party_name=p.name, country=p.country,
                    tier=entry["tier"], offshore=bool(entry.get("offshore")),
                ))

    return hits, geo_hits

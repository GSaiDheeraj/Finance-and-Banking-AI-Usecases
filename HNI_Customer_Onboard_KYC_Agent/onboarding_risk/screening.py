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

from .reference import (
    adverse_media_list,
    high_risk_jurisdictions,
    pep_list,
    sanctions_list,
)
from .schemas import GeographyHit, Party, WatchlistHit

_MATCH_THRESHOLD = 0.85  # token-overlap score at/above which two names "match"
_MATCH_SCORE_PRECISION = 3
_SELF_DECLARED_PEP_SCORE = 1.0
_SELF_DECLARED_PEP_TIER = "medium"
_SCREENED_GEO_TIERS = ("high", "prohibited")

_ENTITY_STOP_WORDS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "ltd",
        "limited",
        "llc",
        "inc",
        "plc",
        "the",
        "and",
        "fze",
        "sa",
        "ag",
        "gmbh",
        "co",
        "company",
        "holdings",
        "holding",
        "trust",
        "foundation",
        "fund",
    }
)


def _normalize(name: str) -> list[str]:
    """Lower-case, strip punctuation and common entity suffixes -> token list."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    tokens = [token for token in cleaned.split() if token]
    return [token for token in tokens if token not in _ENTITY_STOP_WORDS]


def _name_score(first_name: str, second_name: str) -> float:
    first_tokens = set(_normalize(first_name))
    second_tokens = set(_normalize(second_name))
    if not first_tokens or not second_tokens:
        return 0.0
    if first_tokens == second_tokens:
        return 1.0
    overlap = len(first_tokens & second_tokens)
    union = len(first_tokens | second_tokens)
    if not union:
        return 0.0
    return overlap / union


def _best_hit(name: str, entries: list[dict], name_key: str = "name") -> tuple[dict, float] | None:
    best_match: tuple[dict, float] | None = None
    for entry in entries:
        score = _name_score(name, str(entry.get(name_key, "")))
        if score < _MATCH_THRESHOLD:
            continue
        if best_match is not None and score <= best_match[1]:
            continue
        best_match = (entry, score)
    return best_match


def _screen_sanctions(party: Party, sanctions_entries: list[dict]) -> WatchlistHit | None:
    match = _best_hit(party.name, sanctions_entries)
    if match is None:
        return None
    matched_entry, match_score = match
    return WatchlistHit(
        party_id=party.party_id,
        party_name=party.name,
        list_type="sanctions",
        matched_name=matched_entry.get("name", ""),
        match_score=round(match_score, _MATCH_SCORE_PRECISION),
        source_list=matched_entry.get("list"),
        details=matched_entry.get("program"),
    )


def _screen_pep(party: Party, pep_entries: list[dict]) -> WatchlistHit | None:
    match = _best_hit(party.name, pep_entries)
    if match is not None:
        matched_entry, match_score = match
        return WatchlistHit(
            party_id=party.party_id,
            party_name=party.name,
            list_type="pep",
            matched_name=matched_entry.get("name", ""),
            match_score=round(match_score, _MATCH_SCORE_PRECISION),
            details=matched_entry.get("position"),
            tier=matched_entry.get("tier"),
        )
    if not party.declared_pep:
        return None
    return WatchlistHit(
        party_id=party.party_id,
        party_name=party.name,
        list_type="pep",
        matched_name=party.name,
        match_score=_SELF_DECLARED_PEP_SCORE,
        details="Self-declared PEP on onboarding form.",
        tier=_SELF_DECLARED_PEP_TIER,
    )


def _screen_adverse_media(party: Party, adverse_entries: list[dict]) -> WatchlistHit | None:
    match = _best_hit(party.name, adverse_entries)
    if match is None:
        return None
    matched_entry, match_score = match
    category = matched_entry.get("category")
    summary = matched_entry.get("summary", "")
    return WatchlistHit(
        party_id=party.party_id,
        party_name=party.name,
        list_type="adverse_media",
        matched_name=matched_entry.get("name", ""),
        match_score=round(match_score, _MATCH_SCORE_PRECISION),
        details=f"[{category}] {summary}",
    )


def _screen_geography(party: Party, jurisdictions: dict[str, dict]) -> GeographyHit | None:
    if not party.country:
        return None
    jurisdiction = jurisdictions.get(party.country.strip().lower())
    if jurisdiction is None:
        return None
    if jurisdiction.get("tier") not in _SCREENED_GEO_TIERS:
        return None
    return GeographyHit(
        party_id=party.party_id,
        party_name=party.name,
        country=party.country,
        tier=jurisdiction["tier"],
        offshore=bool(jurisdiction.get("offshore")),
    )


def screen_parties(parties: list[Party]) -> tuple[list[WatchlistHit], list[GeographyHit]]:
    """Screen all parties against PEP / sanctions / adverse-media lists and geographies."""
    pep_entries = pep_list()
    sanction_entries = sanctions_list()
    adverse_entries = adverse_media_list()
    jurisdictions = high_risk_jurisdictions()

    hits: list[WatchlistHit] = []
    geo_hits: list[GeographyHit] = []
    for party in parties:
        for watchlist_hit in (
            _screen_sanctions(party, sanction_entries),
            _screen_pep(party, pep_entries),
            _screen_adverse_media(party, adverse_entries),
        ):
            if watchlist_hit is not None:
                hits.append(watchlist_hit)
        geography_hit = _screen_geography(party, jurisdictions)
        if geography_hit is not None:
            geo_hits.append(geography_hit)

    return hits, geo_hits

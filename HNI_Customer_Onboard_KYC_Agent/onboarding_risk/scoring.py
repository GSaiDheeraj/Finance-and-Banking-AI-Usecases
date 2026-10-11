"""
Deterministic risk scorecard + onboarding decision.

NO LLM calls. This module turns the deterministic facts (ownership graph, screening
hits, source-of-wealth, identity gaps) into a fixed-weight scorecard, a risk band, the
fired EDD triggers, and an onboarding decision. Because weights, severities and band
cut-offs are constants, **the same case always yields the same band and decision** —
the reproducibility guarantee in NFR-1.

Scoring model
-------------
Each `RiskFactorType` has a base weight. A factor that fires gets a severity
(low/medium/high -> multiplier 0.5/1.0/1.5); contribution = weight * multiplier. The
raw score is the sum of contributions; the normalized score is `min(100, raw)`.

Two hard stops force band = PROHIBITED regardless of the score: a sanctions match, or a
party in a prohibited jurisdiction.
"""

from __future__ import annotations

from .reference import COMPLEX_ENTITY_COUNT, COMPLEX_LAYERING_DEPTH
from .schemas import (
    GeographyHit,
    IdentityCheck,
    OnboardingDecision,
    OwnershipGraphResult,
    RiskBand,
    RiskFactorResult,
    RiskFactorType,
    RiskScorecard,
    SourceOfWealth,
    WatchlistHit,
)

# Base weights per factor (points). Tuned so a single high-severity AML factor pushes
# a case into 'high'; two medium factors reach 'medium'.
FACTOR_WEIGHTS: dict[RiskFactorType, float] = {
    RiskFactorType.SANCTIONS: 100.0,  # hard stop
    RiskFactorType.PEP: 30.0,
    RiskFactorType.ADVERSE_MEDIA: 28.0,
    RiskFactorType.OPAQUE_SOURCE_OF_WEALTH: 26.0,
    RiskFactorType.NOMINEE_ARRANGEMENT: 24.0,
    RiskFactorType.BEARER_SHARES: 24.0,
    RiskFactorType.HIGH_RISK_GEOGRAPHY: 20.0,
    RiskFactorType.COMPLEX_STRUCTURE: 16.0,
    RiskFactorType.LAYERING: 16.0,
    RiskFactorType.ID_VERIFICATION_GAP: 14.0,
    RiskFactorType.OFFSHORE_JURISDICTION: 10.0,
}

_SEVERITY_MULT = {"low": 0.5, "medium": 1.0, "high": 1.5}
_SEVERITY_LOW = "low"
_SEVERITY_MEDIUM = "medium"
_SEVERITY_HIGH = "high"

# Severity <-> rank helpers for aggregating multiple hits of differing strength.
_SEV_RANK = {"low": 1, "medium": 2, "high": 3}
_RANK_SEV = {1: "low", 2: "medium", 3: "high"}

_MAX_SCORE = 100.0
_OSINT_SOURCE_PREFIX = "osint"

# Band cut-offs on the normalized 0..100 score.
_BAND_MEDIUM = 25.0
_BAND_HIGH = 50.0

# Factors that, when present, force EDD regardless of the numeric band.
_HARD_EDD_TRIGGERS = {
    RiskFactorType.PEP,
    RiskFactorType.ADVERSE_MEDIA,
    RiskFactorType.HIGH_RISK_GEOGRAPHY,
    RiskFactorType.OPAQUE_SOURCE_OF_WEALTH,
    RiskFactorType.COMPLEX_STRUCTURE,
    RiskFactorType.NOMINEE_ARRANGEMENT,
    RiskFactorType.BEARER_SHARES,
}

# Declared source-of-wealth phrases treated as opaque / insufficient.
_OPAQUE_SOW = {"", "unknown", "n/a", "na", "not stated", "undisclosed", "private", "other"}


def _adverse_media_severity(hits: list[WatchlistHit]) -> str:
    """Weight adverse-media findings instead of treating every hit as 'high'.

    Curated-list hits are vetted, so their tier is trusted as-is. Web/OSINT findings are
    weaker evidence (a name appearing near 'fraud'/'lawsuit' in a search is often a false
    positive or the subject merely commenting on someone else), so each is discounted one
    severity notch, and a single web finding cannot exceed 'medium' on its own — reaching
    'high' from web-only evidence requires at least two corroborating high-severity hits.

    This is why a legitimate, well-documented entrepreneur with a couple of soft negative
    news items lands in low/medium, not high.
    """
    if not hits:
        return _SEVERITY_LOW
    severity_ranks: list[int] = []
    osint_high_count = 0
    for hit in hits:
        hit_tier = (hit.tier or _SEVERITY_MEDIUM).lower()
        rank = _SEV_RANK.get(hit_tier, 2)
        if (hit.source_list or "").startswith(_OSINT_SOURCE_PREFIX):
            if hit_tier == _SEVERITY_HIGH:
                osint_high_count += 1
            rank = max(1, rank - 1)
        severity_ranks.append(rank)
    top_rank = max(severity_ranks)
    if osint_high_count >= 2:
        top_rank = max(top_rank, 3)
    return _RANK_SEV[top_rank]


def _make_factor(
    factor_type: RiskFactorType,
    present: bool,
    severity: str,
    evidence: list[str],
    pages: list[int],
) -> RiskFactorResult:
    weight = FACTOR_WEIGHTS[factor_type]
    multiplier = _SEVERITY_MULT.get(severity, 1.0)
    contribution = weight * multiplier if present else 0.0
    cited_pages = sorted({page for page in pages if page is not None})
    return RiskFactorResult(
        factor=factor_type,
        present=present,
        severity=severity,
        weight=weight,
        contribution=round(contribution, 2),
        evidence=evidence,
        pages=cited_pages,
    )


def _band(score: float, hard_stop: bool) -> RiskBand:
    if hard_stop:
        return RiskBand.PROHIBITED
    if score >= _BAND_HIGH:
        return RiskBand.HIGH
    if score >= _BAND_MEDIUM:
        return RiskBand.MEDIUM
    return RiskBand.LOW


def _decide(band: RiskBand, edd_required: bool, hard_stop: bool) -> OnboardingDecision:
    if band == RiskBand.PROHIBITED or hard_stop:
        return OnboardingDecision.DECLINE
    if band == RiskBand.HIGH:
        return OnboardingDecision.ESCALATE_MLRO
    if band == RiskBand.MEDIUM or edd_required:
        return OnboardingDecision.APPROVE_WITH_EDD
    return OnboardingDecision.APPROVE_STANDARD_CDD


def _is_osint_source(source_list: str | None) -> bool:
    return (source_list or "").startswith(_OSINT_SOURCE_PREFIX)


def _sanctions_factor(
    watchlist_hits: list[WatchlistHit],
) -> tuple[RiskFactorResult, str | None]:
    sanctions = [hit for hit in watchlist_hits if hit.list_type == "sanctions"]
    hard_stop_reason: str | None = None
    if sanctions:
        first_hit = sanctions[0]
        source = first_hit.source_list or "list"
        hard_stop_reason = (
            f"Sanctions match: {first_hit.party_name} ~ {first_hit.matched_name} ({source})"
        )
    evidence = [f"{hit.party_name} ~ {hit.matched_name} [{hit.source_list}]" for hit in sanctions]
    factor = _make_factor(RiskFactorType.SANCTIONS, bool(sanctions), _SEVERITY_HIGH, evidence, [])
    return factor, hard_stop_reason


def _pep_factor(watchlist_hits: list[WatchlistHit]) -> RiskFactorResult:
    pep_hits = [hit for hit in watchlist_hits if hit.list_type == "pep"]
    has_high_tier = any((hit.tier or "") == _SEVERITY_HIGH for hit in pep_hits)
    severity = _SEVERITY_HIGH if has_high_tier else _SEVERITY_MEDIUM
    evidence = [f"{hit.party_name}: {hit.details or 'PEP'} (tier={hit.tier})" for hit in pep_hits]
    return _make_factor(RiskFactorType.PEP, bool(pep_hits), severity, evidence, [])


def _adverse_media_factor(watchlist_hits: list[WatchlistHit]) -> RiskFactorResult:
    adverse_hits = [hit for hit in watchlist_hits if hit.list_type == "adverse_media"]
    severity = _adverse_media_severity(adverse_hits)
    evidence = [_format_adverse_evidence(hit) for hit in adverse_hits]
    return _make_factor(RiskFactorType.ADVERSE_MEDIA, bool(adverse_hits), severity, evidence, [])


def _format_adverse_evidence(hit: WatchlistHit) -> str:
    origin = "web" if _is_osint_source(hit.source_list) else "list"
    tier = hit.tier or _SEVERITY_MEDIUM
    return f"{hit.party_name} [{tier}/{origin}]: {hit.details}"


def _geography_factors(
    geography_hits: list[GeographyHit],
) -> tuple[RiskFactorResult, RiskFactorResult, str | None]:
    prohibited = [hit for hit in geography_hits if hit.tier == "prohibited"]
    high_risk = [hit for hit in geography_hits if hit.tier == "high"]
    hard_stop_reason: str | None = None
    if prohibited:
        first_hit = prohibited[0]
        hard_stop_reason = f"Prohibited jurisdiction: {first_hit.party_name} ({first_hit.country})"
    combined = prohibited + high_risk
    geography_factor = _make_factor(
        RiskFactorType.HIGH_RISK_GEOGRAPHY,
        bool(combined),
        _SEVERITY_HIGH if prohibited else _SEVERITY_MEDIUM,
        [f"{hit.party_name}: {hit.country} ({hit.tier})" for hit in combined],
        [],
    )
    offshore = [hit for hit in geography_hits if hit.offshore]
    offshore_factor = _make_factor(
        RiskFactorType.OFFSHORE_JURISDICTION,
        bool(offshore),
        _SEVERITY_LOW,
        [f"{hit.party_name}: {hit.country} (offshore)" for hit in offshore],
        [],
    )
    return geography_factor, offshore_factor, hard_stop_reason


def _structure_factors(ownership: OwnershipGraphResult) -> list[RiskFactorResult]:
    is_complex = (
        ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH
        or ownership.num_entities >= COMPLEX_ENTITY_COUNT
        or ownership.has_circular_ownership
    )
    deep_layering = ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH + 1
    layering_severity = _SEVERITY_HIGH if deep_layering else _SEVERITY_MEDIUM
    circular_suffix = ", circular" if ownership.has_circular_ownership else ""
    structure_evidence = (
        f"layering depth={ownership.max_layering_depth}, "
        f"entities={ownership.num_entities}{circular_suffix}"
    )
    return [
        _make_factor(
            RiskFactorType.COMPLEX_STRUCTURE,
            is_complex,
            layering_severity,
            [structure_evidence],
            [],
        ),
        _make_factor(
            RiskFactorType.LAYERING,
            ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH,
            layering_severity,
            [f"longest ownership chain = {ownership.max_layering_depth} entities"],
            [],
        ),
        _make_factor(
            RiskFactorType.NOMINEE_ARRANGEMENT,
            ownership.has_nominee,
            _SEVERITY_HIGH,
            ["Nominee shareholder/director arrangement present."] if ownership.has_nominee else [],
            [],
        ),
        _make_factor(
            RiskFactorType.BEARER_SHARES,
            ownership.has_bearer_shares,
            _SEVERITY_HIGH,
            ["Bearer shares referenced in the structure."] if ownership.has_bearer_shares else [],
            [],
        ),
    ]


def _is_opaque_wealth(entry: SourceOfWealth) -> bool:
    declared = entry.declared_source.strip().lower()
    return declared in _OPAQUE_SOW or entry.corroborating_evidence_present is False


def _source_of_wealth_factor(
    source_of_wealth: list[SourceOfWealth],
) -> RiskFactorResult:
    opaque_entries = [entry for entry in source_of_wealth if _is_opaque_wealth(entry)]
    evidence = [_format_wealth_evidence(entry) for entry in opaque_entries]
    cited_pages = [entry.page for entry in opaque_entries]
    return _make_factor(
        RiskFactorType.OPAQUE_SOURCE_OF_WEALTH,
        bool(opaque_entries),
        _SEVERITY_HIGH,
        evidence,
        cited_pages,
    )


def _format_wealth_evidence(entry: SourceOfWealth) -> str:
    declared = entry.declared_source or "unstated"
    suffix = " (no corroboration)" if entry.corroborating_evidence_present is False else ""
    return f"{entry.party_id}: '{declared}'{suffix}"


def _identity_factor(identity_checks: list[IdentityCheck]) -> RiskFactorResult:
    gaps = [check for check in identity_checks if check.gaps or check.verified is False]
    evidence = [f"{check.party_id}: {', '.join(check.gaps) or 'not verified'}" for check in gaps]
    cited_pages = [check.page for check in gaps]
    return _make_factor(
        RiskFactorType.ID_VERIFICATION_GAP,
        bool(gaps),
        _SEVERITY_MEDIUM,
        evidence,
        cited_pages,
    )


def score_case(
    ownership: OwnershipGraphResult,
    watchlist_hits: list[WatchlistHit],
    geography_hits: list[GeographyHit],
    source_of_wealth: list[SourceOfWealth],
    identity_checks: list[IdentityCheck],
) -> RiskScorecard:
    """Build the full scorecard, band, EDD triggers and decision deterministically."""
    factors: list[RiskFactorResult] = []

    sanctions_factor, sanctions_stop = _sanctions_factor(watchlist_hits)
    factors.append(sanctions_factor)
    factors.append(_pep_factor(watchlist_hits))
    factors.append(_adverse_media_factor(watchlist_hits))

    geography_factor, offshore_factor, geo_stop = _geography_factors(geography_hits)
    factors.append(geography_factor)
    factors.append(offshore_factor)
    factors.extend(_structure_factors(ownership))
    factors.append(_source_of_wealth_factor(source_of_wealth))
    factors.append(_identity_factor(identity_checks))

    hard_stop_reason = sanctions_stop or geo_stop
    raw_score = round(sum(factor.contribution for factor in factors), 2)
    normalized_score = round(min(_MAX_SCORE, raw_score), 2)
    hard_stop = hard_stop_reason is not None
    band = _band(normalized_score, hard_stop)

    triggers = [
        factor.factor
        for factor in factors
        if factor.present and factor.factor in _HARD_EDD_TRIGGERS
    ]
    edd_required = bool(triggers) or band in (RiskBand.MEDIUM, RiskBand.HIGH)
    decision = _decide(band, edd_required, hard_stop)

    return RiskScorecard(
        factors=factors,
        raw_score=raw_score,
        normalized_score=normalized_score,
        band=band,
        edd_required=edd_required,
        triggers=triggers,
        decision=decision,
        hard_stop_reason=hard_stop_reason,
    )

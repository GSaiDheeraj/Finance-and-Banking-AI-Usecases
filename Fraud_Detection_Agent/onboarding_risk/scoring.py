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

from typing import Dict, List, Optional

from .reference import COMPLEX_ENTITY_COUNT, COMPLEX_LAYERING_DEPTH, ubo_threshold_pct
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
FACTOR_WEIGHTS: Dict[RiskFactorType, float] = {
    RiskFactorType.SANCTIONS: 100.0,             # hard stop
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

# Severity <-> rank helpers for aggregating multiple hits of differing strength.
_SEV_RANK = {"low": 1, "medium": 2, "high": 3}
_RANK_SEV = {1: "low", 2: "medium", 3: "high"}


def _adverse_media_severity(hits: List[WatchlistHit]) -> str:
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
        return "low"
    candidates: List[int] = []
    osint_high = 0
    for h in hits:
        rank = _SEV_RANK.get((h.tier or "medium").lower(), 2)
        if (h.source_list or "").startswith("osint"):
            if (h.tier or "").lower() == "high":
                osint_high += 1
            rank = max(1, rank - 1)              # credibility discount for web mentions
        candidates.append(rank)                 # curated-list hits trusted as-is
    top = max(candidates)
    # corroboration: >=2 high-severity web findings restore 'high' despite the discount
    if osint_high >= 2:
        top = max(top, 3)
    return _RANK_SEV[top]

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


def _factor(
    ftype: RiskFactorType, present: bool, severity: str,
    evidence: List[str], pages: List[int],
) -> RiskFactorResult:
    weight = FACTOR_WEIGHTS[ftype]
    mult = _SEVERITY_MULT.get(severity, 1.0)
    contribution = weight * mult if present else 0.0
    return RiskFactorResult(
        factor=ftype, present=present, severity=severity, weight=weight,
        contribution=round(contribution, 2),
        evidence=evidence, pages=sorted(set(p for p in pages if p is not None)),
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


def score_case(
    ownership: OwnershipGraphResult,
    watchlist_hits: List[WatchlistHit],
    geography_hits: List[GeographyHit],
    source_of_wealth: List[SourceOfWealth],
    identity_checks: List[IdentityCheck],
) -> RiskScorecard:
    """Build the full scorecard, band, EDD triggers and decision deterministically."""
    factors: List[RiskFactorResult] = []
    hard_stop_reason: Optional[str] = None

    # --- screening-driven factors ---
    sanctions = [h for h in watchlist_hits if h.list_type == "sanctions"]
    if sanctions:
        hard_stop_reason = (
            f"Sanctions match: {sanctions[0].party_name} ~ {sanctions[0].matched_name} "
            f"({sanctions[0].source_list or 'list'})"
        )
    factors.append(_factor(
        RiskFactorType.SANCTIONS, bool(sanctions), "high",
        [f"{h.party_name} ~ {h.matched_name} [{h.source_list}]" for h in sanctions], [],
    ))

    peps = [h for h in watchlist_hits if h.list_type == "pep"]
    pep_sev = "high" if any((h.tier or "") == "high" for h in peps) else "medium"
    factors.append(_factor(
        RiskFactorType.PEP, bool(peps), pep_sev,
        [f"{h.party_name}: {h.details or 'PEP'} (tier={h.tier})" for h in peps], [],
    ))

    adverse = [h for h in watchlist_hits if h.list_type == "adverse_media"]
    adverse_sev = _adverse_media_severity(adverse)
    factors.append(_factor(
        RiskFactorType.ADVERSE_MEDIA, bool(adverse), adverse_sev,
        [f"{h.party_name} [{h.tier or 'medium'}/{'web' if (h.source_list or '').startswith('osint') else 'list'}]"
         f": {h.details}" for h in adverse], [],
    ))

    # --- geography ---
    prohibited_geo = [g for g in geography_hits if g.tier == "prohibited"]
    high_geo = [g for g in geography_hits if g.tier == "high"]
    if prohibited_geo and not hard_stop_reason:
        hard_stop_reason = (
            f"Prohibited jurisdiction: {prohibited_geo[0].party_name} "
            f"({prohibited_geo[0].country})"
        )
    factors.append(_factor(
        RiskFactorType.HIGH_RISK_GEOGRAPHY, bool(high_geo or prohibited_geo),
        "high" if prohibited_geo else "medium",
        [f"{g.party_name}: {g.country} ({g.tier})" for g in (prohibited_geo + high_geo)], [],
    ))
    offshore = [g for g in geography_hits if g.offshore]
    factors.append(_factor(
        RiskFactorType.OFFSHORE_JURISDICTION, bool(offshore), "low",
        [f"{g.party_name}: {g.country} (offshore)" for g in offshore], [],
    ))

    # --- structure opacity ---
    is_complex = (
        ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH
        or ownership.num_entities >= COMPLEX_ENTITY_COUNT
        or ownership.has_circular_ownership
    )
    factors.append(_factor(
        RiskFactorType.COMPLEX_STRUCTURE, is_complex,
        "high" if ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH + 1 else "medium",
        [f"layering depth={ownership.max_layering_depth}, entities={ownership.num_entities}"
         + (", circular" if ownership.has_circular_ownership else "")], [],
    ))
    factors.append(_factor(
        RiskFactorType.LAYERING, ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH,
        "high" if ownership.max_layering_depth >= COMPLEX_LAYERING_DEPTH + 1 else "medium",
        [f"longest ownership chain = {ownership.max_layering_depth} entities"], [],
    ))
    factors.append(_factor(
        RiskFactorType.NOMINEE_ARRANGEMENT, ownership.has_nominee, "high",
        ["Nominee shareholder/director arrangement present."] if ownership.has_nominee else [],
        [],
    ))
    factors.append(_factor(
        RiskFactorType.BEARER_SHARES, ownership.has_bearer_shares, "high",
        ["Bearer shares referenced in the structure."] if ownership.has_bearer_shares else [],
        [],
    ))

    # --- source of wealth opacity ---
    opaque = [
        s for s in source_of_wealth
        if s.declared_source.strip().lower() in _OPAQUE_SOW
        or s.corroborating_evidence_present is False
    ]
    factors.append(_factor(
        RiskFactorType.OPAQUE_SOURCE_OF_WEALTH, bool(opaque), "high",
        [f"{s.party_id}: '{s.declared_source or 'unstated'}'"
         f"{' (no corroboration)' if s.corroborating_evidence_present is False else ''}"
         for s in opaque],
        [s.page for s in opaque],
    ))

    # --- identity gaps ---
    id_gaps = [c for c in identity_checks if c.gaps or c.verified is False]
    factors.append(_factor(
        RiskFactorType.ID_VERIFICATION_GAP, bool(id_gaps), "medium",
        [f"{c.party_id}: {', '.join(c.gaps) or 'not verified'}" for c in id_gaps],
        [c.page for c in id_gaps],
    ))

    # --- aggregate ---
    raw = round(sum(f.contribution for f in factors), 2)
    normalized = round(min(100.0, raw), 2)
    hard_stop = hard_stop_reason is not None
    band = _band(normalized, hard_stop)

    triggers = [
        f.factor for f in factors
        if f.present and f.factor in _HARD_EDD_TRIGGERS
    ]
    edd_required = bool(triggers) or band in (RiskBand.MEDIUM, RiskBand.HIGH)
    decision = _decide(band, edd_required, hard_stop)

    return RiskScorecard(
        factors=factors,
        raw_score=raw,
        normalized_score=normalized,
        band=band,
        edd_required=edd_required,
        triggers=triggers,
        decision=decision,
        hard_stop_reason=hard_stop_reason,
    )

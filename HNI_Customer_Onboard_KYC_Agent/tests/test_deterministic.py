"""Tests for the deterministic engine: screening, ownership, and scoring."""

from onboarding_risk.ownership import resolve_ubos
from onboarding_risk.schemas import (
    IdentityCheck,
    OwnershipEdge,
    Party,
    PartyType,
    Relationship,
    RiskBand,
    RoleType,
    SourceOfWealth,
    WatchlistHit,
)
from onboarding_risk.scoring import score_case
from onboarding_risk.screening import _name_score, screen_parties


def _party(party_id: str, name: str, **kwargs) -> Party:
    return Party(party_id=party_id, name=name, **kwargs)


def test_name_score_exact_match_scores_one():
    assert _name_score("Alice Example", "Alice Example") == 1.0


def test_name_score_empty_name_scores_zero():
    assert _name_score("", "Alice Example") == 0.0


def test_self_declared_pep_produces_hit_without_list_match():
    parties = [_party("P1", "Zoe Zed", declared_pep=True)]
    hits, _ = screen_parties(parties)
    pep_hits = [hit for hit in hits if hit.list_type == "pep"]
    assert len(pep_hits) == 1
    assert pep_hits[0].matched_name == "Zoe Zed"


def test_clean_party_produces_no_hits():
    parties = [_party("P1", "Zoe Zed", country="France")]
    hits, geo_hits = screen_parties(parties)
    assert hits == []
    assert geo_hits == []


def test_high_risk_geography_produces_geo_hit():
    parties = [_party("P1", "Zoe Zed", country="Panama")]
    _, geo_hits = screen_parties(parties)
    assert len(geo_hits) == 1
    assert geo_hits[0].tier == "high"


def test_simple_majority_owner_resolves_as_ubo():
    parties = [
        _party("P1", "Alice Example", party_type=PartyType.INDIVIDUAL),
        _party("P2", "Example Holdings", party_type=PartyType.HOLDING_COMPANY),
    ]
    edges = [OwnershipEdge(owner_id="P1", owned_id="P2", percentage=100.0)]
    result = resolve_ubos(parties, edges, [], applicant_party_id="P2")
    assert result.applicant_party_id == "P2"
    assert [ubo.party_id for ubo in result.ubos] == ["P1"]
    assert result.ubos[0].effective_ownership_pct == 100.0


def test_empty_parties_returns_empty_graph():
    result = resolve_ubos([], [], [])
    assert result.ubos == []
    assert result.applicant_party_id is None


def test_director_without_ownership_qualifies_as_control_ubo():
    parties = [
        _party("P1", "Alice Example", party_type=PartyType.INDIVIDUAL),
        _party("P2", "Example Holdings", party_type=PartyType.HOLDING_COMPANY),
    ]
    relationships = [Relationship(party_id="P1", role=RoleType.DIRECTOR)]
    result = resolve_ubos(parties, [], relationships, applicant_party_id="P2")
    assert len(result.ubos) == 1
    assert result.ubos[0].basis == "control"


def _clean_case():
    ownership = resolve_ubos([], [], [])
    return ownership, [], [], [], []


def test_clean_case_scores_low_with_standard_cdd():
    ownership, watchlist_hits, geography_hits, wealth, identity = _clean_case()
    scorecard = score_case(ownership, watchlist_hits, geography_hits, wealth, identity)
    assert scorecard.band == RiskBand.LOW
    assert scorecard.decision.value == "approve_standard_cdd"
    assert not scorecard.edd_required


def test_sanctions_hit_forces_prohibited_and_decline():
    ownership, _, geography_hits, wealth, identity = _clean_case()
    sanctions_hit = WatchlistHit(
        party_id="P1",
        party_name="Viktor Sokolov",
        list_type="sanctions",
        matched_name="Viktor Sokolov",
        match_score=1.0,
    )
    scorecard = score_case(ownership, [sanctions_hit], geography_hits, wealth, identity)
    assert scorecard.band == RiskBand.PROHIBITED
    assert scorecard.decision.value == "decline"
    assert scorecard.hard_stop_reason is not None


def test_opaque_wealth_triggers_edd():
    ownership, watchlist_hits, geography_hits, _, identity = _clean_case()
    wealth = [
        SourceOfWealth(
            party_id="P1",
            declared_source="unknown",
            corroborating_evidence_present=False,
        )
    ]
    scorecard = score_case(ownership, watchlist_hits, geography_hits, wealth, identity)
    assert scorecard.edd_required
    assert any(
        factor.present and factor.factor.value == "opaque_source_of_wealth"
        for factor in scorecard.factors
    )


def test_identity_gap_scores_medium_factor():
    ownership, watchlist_hits, geography_hits, wealth, _ = _clean_case()
    identity = [IdentityCheck(party_id="P1", gaps=["no certified copy"])]
    scorecard = score_case(ownership, watchlist_hits, geography_hits, wealth, identity)
    assert any(
        factor.present and factor.factor.value == "id_verification_gap"
        for factor in scorecard.factors
    )

"""Tests for the grounded extraction layer (JSON parsing + row mapping)."""

from unittest.mock import MagicMock, patch

from onboarding_risk.extraction import (
    _coerce_enum,
    _empty_structure,
    _parse_edges,
    _parse_parties,
    _parse_relationships,
    _safe_json,
    _strip_json,
    detect_case_metadata,
    extract_identity,
    extract_source_of_wealth,
    extract_structure,
)
from onboarding_risk.schemas import Party, PartyType, RoleType


def test_strip_json_returns_plain_json_unchanged():
    assert _strip_json('{"parties": []}') == '{"parties": []}'


def test_strip_json_removes_markdown_fences():
    fenced = '```json\n{"parties": []}\n```'
    assert _strip_json(fenced) == '{"parties": []}'


def test_safe_json_returns_none_for_malformed_input():
    assert _safe_json("not json at all") is None
    assert _safe_json('{"parties": [}') is None


def test_safe_json_parses_valid_payload():
    assert _safe_json('{"parties": []}') == {"parties": []}


def test_coerce_enum_falls_back_on_unknown_value():
    assert _coerce_enum("individual", PartyType, PartyType.OTHER) == PartyType.INDIVIDUAL
    assert _coerce_enum("not-a-type", PartyType, PartyType.OTHER) == PartyType.OTHER
    assert _coerce_enum(None, RoleType, None) is None


def test_parse_parties_skips_rows_without_identity():
    rows = {
        "parties": [
            {"party_id": "P1", "name": "Alice Example"},
            {"party_id": "", "name": "Missing id"},
            {"party_id": "P2"},
            "not-a-dict",
        ]
    }
    parties = _parse_parties(rows)
    assert [party.party_id for party in parties] == ["P1"]


def test_parse_edges_rejects_unknown_or_self_referencing_ids():
    rows = {
        "edges": [
            {"owner_id": "P1", "owned_id": "P2", "percentage": 60},
            {"owner_id": "P1", "owned_id": "PX", "percentage": 10},
            {"owner_id": "P1", "owned_id": "P1", "percentage": 100},
            {"owner_id": "P1", "owned_id": "P2", "percentage": "not-a-number"},
        ]
    }
    edges = _parse_edges(rows, {"P1", "P2"})
    assert [(edge.owner_id, edge.owned_id) for edge in edges] == [
        ("P1", "P2"),
        ("P1", "P2"),
    ]
    assert edges[0].percentage == 60
    assert edges[1].percentage is None


def test_parse_relationships_rejects_unknown_parties_and_roles():
    rows = {
        "relationships": [
            {"party_id": "P1", "role": "director"},
            {"party_id": "PX", "role": "director"},
            {"party_id": "P1", "role": "not-a-role"},
        ]
    }
    relationships = _parse_relationships(rows, {"P1"})
    assert len(relationships) == 1
    assert relationships[0].role == RoleType.DIRECTOR


def test_extract_structure_returns_empty_on_non_dict_llm_output():
    fake_index = MagicMock()
    fake_index.search.return_value = []
    with patch("onboarding_risk.extraction._invoke_llm_for_json", return_value=["not-a-dict"]):
        assert extract_structure(fake_index) == _empty_structure()
    with patch("onboarding_risk.extraction._invoke_llm_for_json", return_value=None):
        assert extract_structure(fake_index) == _empty_structure()


def test_extract_structure_maps_parties_edges_and_roles():
    llm_payload = {
        "parties": [
            {"party_id": "P1", "name": "Alice Example", "party_type": "individual"},
            {"party_id": "P2", "name": "Example Holdings", "party_type": "holding_company"},
        ],
        "edges": [{"owner_id": "P1", "owned_id": "P2", "percentage": 100}],
        "relationships": [{"party_id": "P1", "role": "shareholder"}],
        "notes": ["bearer shares mentioned"],
    }
    fake_index = MagicMock()
    fake_index.search.return_value = []
    with (
        patch("onboarding_risk.extraction._invoke_llm_for_json", return_value=llm_payload),
        patch(
            "onboarding_risk.extraction._score_structure_output",
            return_value=type("Report", (), {"passed": True})(),
        ),
    ):
        result = extract_structure(fake_index)
    assert [party.party_id for party in result["parties"]] == ["P1", "P2"]
    assert len(result["edges"]) == 1
    assert len(result["relationships"]) == 1
    assert result["notes"] == ["bearer shares mentioned"]


def test_extract_source_of_wealth_returns_empty_on_non_list():
    fake_index = MagicMock()
    fake_index.search.return_value = []
    with patch("onboarding_risk.extraction._invoke_llm_for_json", return_value={}):
        assert extract_source_of_wealth(fake_index, []) == []


def test_extract_identity_returns_empty_on_non_list():
    fake_index = MagicMock()
    fake_index.search.return_value = []
    with patch("onboarding_risk.extraction._invoke_llm_for_json", return_value=None):
        assert extract_identity(fake_index, []) == []


def test_detect_case_metadata_discards_unknown_applicant_id():
    parties = [Party(party_id="P1", name="Alice Example")]
    fake_index = MagicMock()
    fake_index.search.return_value = []
    with patch(
        "onboarding_risk.extraction._invoke_llm_for_json",
        return_value={"applicant_name": "Nobody", "applicant_party_id": "PX"},
    ):
        metadata = detect_case_metadata(fake_index, parties)
    assert metadata.applicant_party_id is None

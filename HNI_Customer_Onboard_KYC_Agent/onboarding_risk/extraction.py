"""
Grounded structured extraction for an onboarding pack.

The LLM does *language* work only: read the KYC form / trust deed / ownership chart,
name the parties, read the ownership percentages, classify each related-party role, and
summarize the declared source of wealth. It assigns a stable `party_id` to each party so
that ownership edges and roles refer to the same party consistently. It records the page
each fact came from. **No scoring, no ownership math, no list matching happens here** —
those are deterministic and live in `ownership.py`, `screening.py`, `scoring.py`.

Every extractor is grounded: it only sees retrieved page text and is told to use nothing
else.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage

from . import llm_validation as lv
from .config import get_llm
from .doc_index import PageIndex
from .schemas import (
    CaseMetadata,
    IdentityCheck,
    InterestType,
    OwnershipEdge,
    Party,
    PartyType,
    Relationship,
    RoleType,
    SourceOfWealth,
)

_log = logging.getLogger(__name__)

_STRUCTURE_QUERY = (
    "beneficial owner shareholder register ownership percentage trust deed settlor "
    "trustee protector beneficiary director signatory holding company SPV foundation"
)
_SOW_QUERY = (
    "source of wealth source of funds origin of assets inheritance sale of business "
    "salary dividends investment income net worth declaration"
)
_IDENTITY_QUERY = (
    "passport national identity card certificate of incorporation date of birth "
    "verification certified document address proof"
)
_META_QUERY = "application form account opening relationship purpose product mandate applicant name"

_STRUCTURE_TOP_K = 8
_SOW_TOP_K = 6
_IDENTITY_TOP_K = 6
_META_TOP_K = 4
_SNIPPET_PREVIEW_LENGTH = 300
_NARRATIVE_PREVIEW_LENGTH = 600

_STRUCTURE_SYSTEM_PROMPT = (
    "You map the legal structure of a private-banking onboarding case.\n"
    "From the provided page text only, identify every PARTY (natural persons AND "
    "legal entities), the OWNERSHIP edges between them, and each party's ROLE.\n"
    "Rules:\n"
    "1. Assign each distinct party a stable id: 'P1', 'P2', ... Reuse the same id "
    "everywhere that party appears (edges and roles refer to these ids).\n"
    "2. Use ONLY information printed in the page text. Never invent parties or numbers.\n"
    "3. For ownership, emit an edge {owner_id, owned_id, percentage} — percentage is the "
    "owner's stake in the owned entity; use null if only control (not a %) is stated.\n"
    "4. Capture trust/structure roles (settlor, trustee, protector, beneficiary, "
    "director, shareholder, signatory, spouse, nominee, partner, applicant).\n"
    "5. Record the `page` for each item. Flag nominee shareholders/directors and any "
    "bearer-share mention via party_type='nominee' and notes.\n"
    "Output ONLY a JSON object, no prose."
)

_SOW_SYSTEM_PROMPT = (
    "You extract SOURCE OF WEALTH / SOURCE OF FUNDS declarations from onboarding text.\n"
    "For each principal party with a declared source, summarize it, normalize the "
    "`declared_source` to a short phrase (e.g. 'sale of business', 'inheritance', "
    "'employment income', 'investment income', 'unknown'), and state whether "
    "corroborating evidence is referenced in the text.\n"
    "Use ONLY the provided text. Map names to the supplied party_id directory. "
    "Output ONLY a JSON array."
)

_IDENTITY_SYSTEM_PROMPT = (
    "You extract IDENTITY VERIFICATION evidence from onboarding text.\n"
    "For each party with an identity document referenced, record the document type, "
    "number (if printed), whether it appears verified/certified, and list any gaps "
    "(e.g. 'no certified copy', 'expired', 'missing proof of address').\n"
    "Use ONLY the provided text. Map names to the supplied party_id directory. "
    "Output ONLY a JSON array."
)

_META_SYSTEM_PROMPT = (
    "Identify the onboarding case metadata from the text. The applicant is the primary "
    "onboarding subject (often the legal structure being onboarded, e.g. the trust or "
    "holding company — not necessarily a person). Map the applicant to a party_id from "
    "the directory if possible.\n"
    "Output ONLY a JSON object: "
    '{"applicant_name": str|null, "applicant_party_id": str|null, '
    '"product": str|null, "relationship_purpose": str|null}'
)

EnumT = TypeVar("EnumT")


def _strip_json(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    content_lines = stripped.split("\n")[1:]
    if content_lines and content_lines[-1].strip() == "```":
        content_lines = content_lines[:-1]
    return "\n".join(content_lines).strip()


def _safe_json(text: str) -> Any | None:
    try:
        return json.loads(_strip_json(text))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def _evidence_from_pages(index: PageIndex, query: str, top_k: int) -> str:
    matching_pages = index.search(query, k=top_k)
    blocks = [
        f"--- PAGE {page.page_number} ({page.doc_name}) ---\n{page.text}" for page in matching_pages
    ]
    return "\n\n".join(blocks)


def _coerce_enum(value: Any, enum_cls: type[EnumT], default: EnumT | None) -> EnumT | None:  # noqa: UP047
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return default


def _invoke_llm_for_json(system_prompt: str, user_content: str) -> Any | None:
    try:
        raw_response = (
            get_llm()
            .invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_content)])
            .content
        )
    except Exception as llm_error:
        _log.warning("LLM extraction call failed: %s", llm_error)
        return None
    parsed = _safe_json(raw_response)
    if parsed is None:
        _log.warning("LLM extraction returned non-JSON output.")
    return parsed


def _party_directory(parties: list[Party]) -> list[dict[str, str]]:
    return [{"party_id": party.party_id, "name": party.name} for party in parties]


def _structure_schema_hint() -> str:
    party_types = [party_type.value for party_type in PartyType]
    roles = [role.value for role in RoleType]
    interests = [interest.value for interest in InterestType]
    return (
        "{\n"
        f'  "parties": [{{"party_id": str, "name": str, "party_type": one of {party_types}, '
        '"country": str|null, "nationality": str|null, '
        '"date_of_birth_or_incorporation": str|null, '
        '"identifiers": {str: str}, "declared_pep": bool|null, "page": int, '
        '"source_snippet": str}],\n'
        f'  "edges": [{{"owner_id": str, "owned_id": str, "percentage": number|null, '
        f'"interest_type": one of {interests}, "page": int}}],\n'
        f'  "relationships": [{{"party_id": str, "role": one of {roles}, '
        '"related_to": str|null, "page": int}],\n'
        '  "notes": [str]\n'
        "}"
    )


def _score_structure_output(data: dict[str, Any], evidence: str) -> lv.ConfidenceReport:
    party_rows = [party for party in (data.get("parties") or []) if isinstance(party, dict)]
    party_report = lv.score_party_classification(
        party_rows,
        allowed_types=[party_type.value for party_type in PartyType],
        evidence_text=evidence,
    )
    party_ids = [str(party.get("party_id")) for party in party_rows if isinstance(party, dict)]
    edge_rows = [edge for edge in (data.get("edges") or []) if isinstance(edge, dict)]
    edge_report = lv.score_ownership_extraction(edge_rows, valid_ids=party_ids)
    if not edge_rows:
        return party_report
    return lv.ConfidenceReport(
        confidence=min(party_report.confidence, edge_report.confidence),
        failures=party_report.failures + edge_report.failures,
        checks_run=party_report.checks_run + edge_report.checks_run,
        checks_passed=party_report.checks_passed + edge_report.checks_passed,
    )


def _extract_with_retry(
    evidence: str, schema_hint: str, system_prompt: str
) -> dict[str, Any] | None:
    user_content = f"Page evidence:\n{evidence}\n\nReturn JSON matching:\n{schema_hint}"
    data = _invoke_llm_for_json(system_prompt, user_content)
    if not isinstance(data, dict):
        return None
    if _score_structure_output(data, evidence).passed:
        return data
    baseline = _score_structure_output(data, evidence)
    retry_addendum = lv.build_retry_addendum(baseline.failures)
    retried = _invoke_llm_for_json(system_prompt, f"{user_content}\n\n{retry_addendum}")
    if (
        isinstance(retried, dict)
        and _score_structure_output(retried, evidence).confidence > baseline.confidence
    ):
        return retried
    return data


def _parse_parties(data: dict[str, Any]) -> list[Party]:
    parties: list[Party] = []
    for row in data.get("parties", []) or []:
        if not isinstance(row, dict) or not row.get("party_id") or not row.get("name"):
            continue
        identifiers = {
            str(key): str(value) for key, value in (row.get("identifiers") or {}).items()
        }
        snippet = (row.get("source_snippet") or "")[:_SNIPPET_PREVIEW_LENGTH] or None
        parties.append(
            Party(
                party_id=str(row["party_id"]).strip(),
                name=str(row["name"]).strip(),
                party_type=_coerce_enum(row.get("party_type"), PartyType, PartyType.OTHER),
                country=row.get("country"),
                nationality=row.get("nationality"),
                date_of_birth_or_incorporation=row.get("date_of_birth_or_incorporation"),
                identifiers=identifiers,
                declared_pep=row.get("declared_pep"),
                page=row.get("page"),
                source_snippet=snippet,
            )
        )
    return parties


def _parse_percentage(raw_percentage: Any) -> float | None:
    if raw_percentage is None:
        return None
    try:
        return float(raw_percentage)
    except (TypeError, ValueError):
        return None


def _parse_edges(data: dict[str, Any], valid_ids: set[str]) -> list[OwnershipEdge]:
    edges: list[OwnershipEdge] = []
    for row in data.get("edges", []) or []:
        if not isinstance(row, dict):
            continue
        owner_id = row.get("owner_id")
        owned_id = row.get("owned_id")
        if owner_id not in valid_ids or owned_id not in valid_ids or owner_id == owned_id:
            continue
        edges.append(
            OwnershipEdge(
                owner_id=str(owner_id),
                owned_id=str(owned_id),
                percentage=_parse_percentage(row.get("percentage")),
                interest_type=_coerce_enum(
                    row.get("interest_type"), InterestType, InterestType.EQUITY
                ),
                page=row.get("page"),
            )
        )
    return edges


def _parse_relationships(data: dict[str, Any], valid_ids: set[str]) -> list[Relationship]:
    relationships: list[Relationship] = []
    for row in data.get("relationships", []) or []:
        if not isinstance(row, dict) or row.get("party_id") not in valid_ids:
            continue
        role = _coerce_enum(row.get("role"), RoleType, None)
        if role is None:
            continue
        relationships.append(
            Relationship(
                party_id=str(row["party_id"]),
                role=role,
                related_to=row.get("related_to"),
                page=row.get("page"),
            )
        )
    return relationships


def _empty_structure() -> dict[str, Any]:
    return {"parties": [], "edges": [], "relationships": [], "notes": []}


# --------------------------------------------------------------------------- #
# Structure extraction: parties + ownership edges + roles (one grounded call)
# --------------------------------------------------------------------------- #
def extract_structure(index: PageIndex, top_k: int = _STRUCTURE_TOP_K) -> dict[str, Any]:
    """Extract parties, ownership edges and related-party roles in one pass.

    Returned as a dict with keys 'parties', 'edges', 'relationships'. They are
    extracted together so that `party_id`s stay consistent across all three.
    """
    evidence = _evidence_from_pages(index, _STRUCTURE_QUERY, top_k=top_k)
    data = _extract_with_retry(evidence, _structure_schema_hint(), _STRUCTURE_SYSTEM_PROMPT)
    if data is None:
        return _empty_structure()

    parties = _parse_parties(data)
    valid_ids = {party.party_id for party in parties}
    return {
        "parties": parties,
        "edges": _parse_edges(data, valid_ids),
        "relationships": _parse_relationships(data, valid_ids),
        "notes": [str(note) for note in (data.get("notes") or []) if note],
    }


# --------------------------------------------------------------------------- #
# Source of wealth
# --------------------------------------------------------------------------- #
def extract_source_of_wealth(
    index: PageIndex, parties: list[Party], top_k: int = _SOW_TOP_K
) -> list[SourceOfWealth]:
    """Extract declared source-of-wealth / source-of-funds per principal party."""
    evidence = _evidence_from_pages(index, _SOW_QUERY, top_k=top_k)
    schema_hint = (
        '[{"party_id": str, "declared_source": str, "narrative": str|null, '
        '"corroborating_evidence_present": bool|null, "page": int}]'
    )
    user_content = (
        f"Party directory:\n{json.dumps(_party_directory(parties))}\n\n"
        f"Page evidence:\n{evidence}\n\nReturn JSON matching:\n{schema_hint}"
    )
    data = _invoke_llm_for_json(_SOW_SYSTEM_PROMPT, user_content)
    if not isinstance(data, list):
        return []

    valid_ids = {party.party_id for party in parties}
    wealth_entries: list[SourceOfWealth] = []
    for row in data:
        if not isinstance(row, dict) or row.get("party_id") not in valid_ids:
            continue
        narrative = (row.get("narrative") or "")[:_NARRATIVE_PREVIEW_LENGTH] or None
        wealth_entries.append(
            SourceOfWealth(
                party_id=str(row["party_id"]),
                declared_source=str(row.get("declared_source") or "").strip(),
                narrative=narrative,
                corroborating_evidence_present=row.get("corroborating_evidence_present"),
                page=row.get("page"),
            )
        )
    return wealth_entries


# --------------------------------------------------------------------------- #
# Identity verification
# --------------------------------------------------------------------------- #
def extract_identity(
    index: PageIndex, parties: list[Party], top_k: int = _IDENTITY_TOP_K
) -> list[IdentityCheck]:
    """Extract identity-document evidence and any verification gaps per party."""
    evidence = _evidence_from_pages(index, _IDENTITY_QUERY, top_k=top_k)
    schema_hint = (
        '[{"party_id": str, "document_type": str|null, "document_number": str|null, '
        '"verified": bool|null, "gaps": [str], "page": int}]'
    )
    user_content = (
        f"Party directory:\n{json.dumps(_party_directory(parties))}\n\n"
        f"Page evidence:\n{evidence}\n\nReturn JSON matching:\n{schema_hint}"
    )
    data = _invoke_llm_for_json(_IDENTITY_SYSTEM_PROMPT, user_content)
    if not isinstance(data, list):
        return []

    valid_ids = {party.party_id for party in parties}
    identity_checks: list[IdentityCheck] = []
    for row in data:
        if not isinstance(row, dict) or row.get("party_id") not in valid_ids:
            continue
        identity_checks.append(
            IdentityCheck(
                party_id=str(row["party_id"]),
                document_type=row.get("document_type"),
                document_number=row.get("document_number"),
                verified=row.get("verified"),
                gaps=[str(gap) for gap in (row.get("gaps") or []) if gap],
                page=row.get("page"),
            )
        )
    return identity_checks


# --------------------------------------------------------------------------- #
# Case metadata
# --------------------------------------------------------------------------- #
def detect_case_metadata(index: PageIndex, parties: list[Party]) -> CaseMetadata:
    """Detect the applicant / product / relationship purpose, and resolve applicant id."""
    evidence = _evidence_from_pages(index, _META_QUERY, top_k=_META_TOP_K)
    user_content = f"Party directory:\n{json.dumps(_party_directory(parties))}\n\nText:\n{evidence}"
    data = _invoke_llm_for_json(_META_SYSTEM_PROMPT, user_content)
    if not isinstance(data, dict):
        return CaseMetadata()

    valid_ids = {party.party_id for party in parties}
    applicant_party_id = data.get("applicant_party_id")
    if applicant_party_id not in valid_ids:
        applicant_party_id = None
    return CaseMetadata(
        applicant_name=data.get("applicant_name"),
        applicant_party_id=applicant_party_id,
        product=data.get("product"),
        relationship_purpose=data.get("relationship_purpose"),
    )

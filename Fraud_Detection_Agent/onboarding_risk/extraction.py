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
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

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
_META_QUERY = (
    "application form account opening relationship purpose product mandate applicant name"
)


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _safe_json(text: str) -> Any:
    try:
        return json.loads(_strip_json(text))
    except Exception:
        return None


def _evidence_from_pages(index: PageIndex, query: str, k: int) -> str:
    hits = index.search(query, k=k)
    blocks = [
        f"--- PAGE {p.page_number} ({p.doc_name}) ---\n{p.text}" for p in hits
    ]
    return "\n\n".join(blocks)


def _enum(value: Any, enum_cls, default):
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return default


# --------------------------------------------------------------------------- #
# Structure extraction: parties + ownership edges + roles (one grounded call)
# --------------------------------------------------------------------------- #
def extract_structure(index: PageIndex, k: int = 8) -> Dict[str, Any]:
    """Extract parties, ownership edges and related-party roles in one pass.

    Returned as a dict with keys 'parties', 'edges', 'relationships'. They are
    extracted together so that `party_id`s stay consistent across all three.
    """
    evidence = _evidence_from_pages(index, _STRUCTURE_QUERY, k=k)
    party_types = [t.value for t in PartyType]
    roles = [r.value for r in RoleType]
    interests = [t.value for t in InterestType]

    system = SystemMessage(content=(
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
    ))
    schema_hint = (
        "{\n"
        '  "parties": [{"party_id": str, "name": str, "party_type": one of ' + str(party_types) + ', '
        '"country": str|null, "nationality": str|null, '
        '"date_of_birth_or_incorporation": str|null, '
        '"identifiers": {str: str}, "declared_pep": bool|null, "page": int, '
        '"source_snippet": str}],\n'
        '  "edges": [{"owner_id": str, "owned_id": str, "percentage": number|null, '
        '"interest_type": one of ' + str(interests) + ', "page": int}],\n'
        '  "relationships": [{"party_id": str, "role": one of ' + str(roles) + ', '
        '"related_to": str|null, "page": int}],\n'
        '  "notes": [str]\n'
        "}"
    )
    user = HumanMessage(content=(
        f"Page evidence:\n{evidence}\n\nReturn JSON matching:\n{schema_hint}"
    ))

    raw = get_llm().invoke([system, user]).content
    data = _safe_json(raw)
    if not isinstance(data, dict):
        return {"parties": [], "edges": [], "relationships": [], "notes": []}

    parties: List[Party] = []
    for row in data.get("parties", []) or []:
        if not isinstance(row, dict) or not row.get("party_id") or not row.get("name"):
            continue
        parties.append(Party(
            party_id=str(row["party_id"]).strip(),
            name=str(row["name"]).strip(),
            party_type=_enum(row.get("party_type"), PartyType, PartyType.OTHER),
            country=row.get("country"),
            nationality=row.get("nationality"),
            date_of_birth_or_incorporation=row.get("date_of_birth_or_incorporation"),
            identifiers={str(k2): str(v) for k2, v in (row.get("identifiers") or {}).items()},
            declared_pep=row.get("declared_pep"),
            page=row.get("page"),
            source_snippet=(row.get("source_snippet") or "")[:300] or None,
        ))

    valid_ids = {p.party_id for p in parties}
    edges: List[OwnershipEdge] = []
    for row in data.get("edges", []) or []:
        if not isinstance(row, dict):
            continue
        owner, owned = row.get("owner_id"), row.get("owned_id")
        if owner not in valid_ids or owned not in valid_ids or owner == owned:
            continue
        pct = row.get("percentage")
        try:
            pct = None if pct is None else float(pct)
        except (TypeError, ValueError):
            pct = None
        edges.append(OwnershipEdge(
            owner_id=str(owner), owned_id=str(owned), percentage=pct,
            interest_type=_enum(row.get("interest_type"), InterestType, InterestType.EQUITY),
            page=row.get("page"),
        ))

    relationships: List[Relationship] = []
    for row in data.get("relationships", []) or []:
        if not isinstance(row, dict) or row.get("party_id") not in valid_ids:
            continue
        role = _enum(row.get("role"), RoleType, None)
        if role is None:
            continue
        relationships.append(Relationship(
            party_id=str(row["party_id"]),
            role=role,
            related_to=row.get("related_to"),
            page=row.get("page"),
        ))

    return {
        "parties": parties,
        "edges": edges,
        "relationships": relationships,
        "notes": [str(n) for n in (data.get("notes") or []) if n],
    }


# --------------------------------------------------------------------------- #
# Source of wealth
# --------------------------------------------------------------------------- #
def extract_source_of_wealth(
    index: PageIndex, parties: List[Party], k: int = 6
) -> List[SourceOfWealth]:
    """Extract declared source-of-wealth / source-of-funds per principal party."""
    evidence = _evidence_from_pages(index, _SOW_QUERY, k=k)
    party_dir = [{"party_id": p.party_id, "name": p.name} for p in parties]

    system = SystemMessage(content=(
        "You extract SOURCE OF WEALTH / SOURCE OF FUNDS declarations from onboarding text.\n"
        "For each principal party with a declared source, summarize it, normalize the "
        "`declared_source` to a short phrase (e.g. 'sale of business', 'inheritance', "
        "'employment income', 'investment income', 'unknown'), and state whether "
        "corroborating evidence is referenced in the text.\n"
        "Use ONLY the provided text. Map names to the supplied party_id directory. "
        "Output ONLY a JSON array."
    ))
    schema_hint = (
        '[{"party_id": str, "declared_source": str, "narrative": str|null, '
        '"corroborating_evidence_present": bool|null, "page": int}]'
    )
    user = HumanMessage(content=(
        f"Party directory:\n{json.dumps(party_dir)}\n\n"
        f"Page evidence:\n{evidence}\n\nReturn JSON matching:\n{schema_hint}"
    ))

    raw = get_llm().invoke([system, user]).content
    data = _safe_json(raw)
    if not isinstance(data, list):
        return []

    valid_ids = {p.party_id for p in parties}
    out: List[SourceOfWealth] = []
    for row in data:
        if not isinstance(row, dict) or row.get("party_id") not in valid_ids:
            continue
        out.append(SourceOfWealth(
            party_id=str(row["party_id"]),
            declared_source=str(row.get("declared_source") or "").strip(),
            narrative=(row.get("narrative") or "")[:600] or None,
            corroborating_evidence_present=row.get("corroborating_evidence_present"),
            page=row.get("page"),
        ))
    return out


# --------------------------------------------------------------------------- #
# Identity verification
# --------------------------------------------------------------------------- #
def extract_identity(
    index: PageIndex, parties: List[Party], k: int = 6
) -> List[IdentityCheck]:
    """Extract identity-document evidence and any verification gaps per party."""
    evidence = _evidence_from_pages(index, _IDENTITY_QUERY, k=k)
    party_dir = [{"party_id": p.party_id, "name": p.name} for p in parties]

    system = SystemMessage(content=(
        "You extract IDENTITY VERIFICATION evidence from onboarding text.\n"
        "For each party with an identity document referenced, record the document type, "
        "number (if printed), whether it appears verified/certified, and list any gaps "
        "(e.g. 'no certified copy', 'expired', 'missing proof of address').\n"
        "Use ONLY the provided text. Map names to the supplied party_id directory. "
        "Output ONLY a JSON array."
    ))
    schema_hint = (
        '[{"party_id": str, "document_type": str|null, "document_number": str|null, '
        '"verified": bool|null, "gaps": [str], "page": int}]'
    )
    user = HumanMessage(content=(
        f"Party directory:\n{json.dumps(party_dir)}\n\n"
        f"Page evidence:\n{evidence}\n\nReturn JSON matching:\n{schema_hint}"
    ))

    raw = get_llm().invoke([system, user]).content
    data = _safe_json(raw)
    if not isinstance(data, list):
        return []

    valid_ids = {p.party_id for p in parties}
    out: List[IdentityCheck] = []
    for row in data:
        if not isinstance(row, dict) or row.get("party_id") not in valid_ids:
            continue
        out.append(IdentityCheck(
            party_id=str(row["party_id"]),
            document_type=row.get("document_type"),
            document_number=row.get("document_number"),
            verified=row.get("verified"),
            gaps=[str(g) for g in (row.get("gaps") or []) if g],
            page=row.get("page"),
        ))
    return out


# --------------------------------------------------------------------------- #
# Case metadata
# --------------------------------------------------------------------------- #
def detect_case_metadata(
    index: PageIndex, parties: List[Party]
) -> CaseMetadata:
    """Detect the applicant / product / relationship purpose, and resolve applicant id."""
    evidence = _evidence_from_pages(index, _META_QUERY, k=4)
    party_dir = [{"party_id": p.party_id, "name": p.name} for p in parties]

    system = SystemMessage(content=(
        "Identify the onboarding case metadata from the text. The applicant is the primary "
        "onboarding subject (often the legal structure being onboarded, e.g. the trust or "
        "holding company — not necessarily a person). Map the applicant to a party_id from "
        "the directory if possible.\n"
        "Output ONLY a JSON object: "
        '{"applicant_name": str|null, "applicant_party_id": str|null, '
        '"product": str|null, "relationship_purpose": str|null}'
    ))
    user = HumanMessage(content=(
        f"Party directory:\n{json.dumps(party_dir)}\n\nText:\n{evidence}"
    ))
    raw = get_llm().invoke([system, user]).content
    data = _safe_json(raw)
    if not isinstance(data, dict):
        return CaseMetadata()

    valid_ids = {p.party_id for p in parties}
    pid = data.get("applicant_party_id")
    if pid not in valid_ids:
        pid = None
    return CaseMetadata(
        applicant_name=data.get("applicant_name"),
        applicant_party_id=pid,
        product=data.get("product"),
        relationship_purpose=data.get("relationship_purpose"),
    )

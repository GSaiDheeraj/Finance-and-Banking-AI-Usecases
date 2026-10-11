"""
Deterministic beneficial-ownership engine.

This module contains NO LLM calls. It takes the extracted parties + ownership edges +
roles and resolves, in plain Python:

* **effective ownership %** of the applicant entity for every natural person — the sum
  over all ownership paths of the product of the edge percentages along each path;
* the set of **UBOs** — persons at/above the ownership threshold, or holding a control
  role, with a senior-managing-official fallback when no one qualifies on ownership;
* structure **opacity metrics** — layering depth, entity/layer counts, nominee
  arrangements, bearer shares, circular ownership.

Because it is deterministic, the same structure always resolves to the same UBOs and
the same opacity metrics — the foundation of a reproducible risk rating.
"""

from __future__ import annotations

from .reference import ubo_threshold_pct
from .schemas import (
    CONTROL_ROLES,
    UBO,
    OwnershipEdge,
    OwnershipGraphResult,
    Party,
    PartyType,
    Relationship,
    RoleType,
)

_FULL_OWNERSHIP_PCT = 100.0
_UNKNOWN_OWNERSHIP_SORT_KEY = -1.0
_CHAIN_SEPARATOR = " -> "


def _resolve_applicant(
    parties: list[Party],
    edges: list[OwnershipEdge],
    relationships: list[Relationship],
    applicant_party_id: str | None,
) -> str | None:
    """Pick the applicant (root) entity id.

    Preference order: explicit metadata id -> party with role APPLICANT -> the entity
    that is owned by others but owns no one (the sink at the bottom of the structure).
    """
    party_ids = {party.party_id for party in parties}
    if applicant_party_id in party_ids:
        return applicant_party_id

    for relationship in relationships:
        if relationship.role == RoleType.APPLICANT and relationship.party_id in party_ids:
            return relationship.party_id

    return _resolve_sink_applicant(parties, edges)


def _resolve_sink_applicant(parties: list[Party], edges: list[OwnershipEdge]) -> str | None:
    owner_ids = {edge.owner_id for edge in edges}
    owned_ids = {edge.owned_id for edge in edges}
    sinks = owned_ids - owner_ids
    if len(sinks) == 1:
        return next(iter(sinks))
    for sink_id in sinks:
        sink_party = next((party for party in parties if party.party_id == sink_id), None)
        if sink_party is not None and sink_party.party_type != PartyType.INDIVIDUAL:
            return sink_id
    if sinks:
        return next(iter(sinks))
    if parties:
        return parties[0].party_id
    return None


def _collect_ownership_paths(
    current_id: str,
    applicant_id: str,
    out_edges: dict[str, list[OwnershipEdge]],
    party_names: dict[str, str],
    ownership_fraction: float,
    chain: list[str],
    visiting: frozenset[str],
    results: list[tuple[float, str]],
) -> bool:
    if current_id == applicant_id and chain:
        applicant_label = party_names.get(applicant_id, applicant_id)
        results.append((ownership_fraction, _CHAIN_SEPARATOR.join([*chain, applicant_label])))
        return False
    saw_cycle = False
    for edge in out_edges.get(current_id, []):
        if edge.owned_id in visiting:
            saw_cycle = True
            continue
        step_fraction = (edge.percentage or 0.0) / _FULL_OWNERSHIP_PCT
        next_fraction = ownership_fraction * step_fraction if edge.percentage is not None else 0.0
        child_cycle = _collect_ownership_paths(
            edge.owned_id,
            applicant_id,
            out_edges,
            party_names,
            next_fraction,
            [*chain, party_names.get(current_id, current_id)],
            visiting | {edge.owned_id},
            results,
        )
        saw_cycle = saw_cycle or child_cycle
    return saw_cycle


def _paths_to_applicant(
    start: str,
    applicant: str,
    out_edges: dict[str, list[OwnershipEdge]],
    party_name: dict[str, str],
) -> tuple[list[tuple[float, str]], bool]:
    """All ownership paths from `start` down to `applicant`.

    Returns (paths, saw_cycle). Each path is (effective_fraction, human_readable_chain),
    where effective_fraction is the product of edge fractions (percentage/100). Edges
    with an unknown percentage are treated as 100% control for path-existence but the
    fraction is left at 0 so they don't inflate an ownership number.
    """
    results: list[tuple[float, str]] = []
    saw_cycle = _collect_ownership_paths(
        start, applicant, out_edges, party_name, 1.0, [], frozenset({start}), results
    )
    return results, saw_cycle


def _build_out_edges(
    edges: list[OwnershipEdge],
) -> tuple[dict[str, list[OwnershipEdge]], dict[tuple[str, str], int | None]]:
    out_edges: dict[str, list[OwnershipEdge]] = {}
    for edge in edges:
        out_edges.setdefault(edge.owner_id, []).append(edge)
    edge_pages = {(edge.owner_id, edge.owned_id): edge.page for edge in edges}
    return out_edges, edge_pages


def _group_roles_by_party(
    relationships: list[Relationship],
) -> dict[str, list[RoleType]]:
    roles_by_party: dict[str, list[RoleType]] = {}
    for relationship in relationships:
        roles_by_party.setdefault(relationship.party_id, []).append(relationship.role)
    return roles_by_party


def _effective_ownership_for(
    party: Party,
    applicant: str,
    out_edges: dict[str, list[OwnershipEdge]],
    party_names: dict[str, str],
    edge_pages: dict[tuple[str, str], int | None],
) -> tuple[float | None, list[str], list[int], int, bool]:
    if party.party_id == applicant:
        return _FULL_OWNERSHIP_PCT, [], [], 0, False
    paths, saw_cycle = _paths_to_applicant(party.party_id, applicant, out_edges, party_names)
    if not paths:
        return None, [], [], 0, saw_cycle
    effective_pct = round(sum(fraction for fraction, _ in paths) * _FULL_OWNERSHIP_PCT, 4)
    path_texts = [chain for _, chain in paths]
    max_depth = max(chain.count("->") for chain in path_texts)
    cited_pages = [
        page
        for edge in out_edges.get(party.party_id, [])
        if (page := edge_pages.get((edge.owner_id, edge.owned_id))) is not None
    ]
    return effective_pct, path_texts, cited_pages, max_depth, saw_cycle


def _build_individual_ubo(
    party: Party,
    effective_pct: float | None,
    path_texts: list[str],
    cited_pages: list[int],
    control_roles: list[RoleType],
    threshold: float,
) -> UBO | None:
    is_owner_ubo = effective_pct is not None and effective_pct >= threshold
    is_control_ubo = len(control_roles) > 0
    if not (is_owner_ubo or is_control_ubo):
        return None
    fallback_pages = [party.page] if party.page else []
    return UBO(
        party_id=party.party_id,
        name=party.name,
        effective_ownership_pct=effective_pct,
        basis="ownership" if is_owner_ubo else "control",
        control_roles=[role.value for role in control_roles],
        paths=path_texts,
        pages=sorted(set(cited_pages)) if cited_pages else fallback_pages,
    )


def _senior_official_fallback(
    parties: list[Party], roles_by_party: dict[str, list[RoleType]]
) -> UBO | None:
    director = next(
        (
            party
            for party in parties
            if RoleType.DIRECTOR in roles_by_party.get(party.party_id, [])
            and party.party_type == PartyType.INDIVIDUAL
        ),
        None,
    )
    if director is None:
        return None
    return UBO(
        party_id=director.party_id,
        name=director.name,
        effective_ownership_pct=None,
        basis="senior_managing_official",
        control_roles=[RoleType.DIRECTOR.value],
        pages=[director.page] if director.page else [],
    )


def resolve_ubos(
    parties: list[Party],
    edges: list[OwnershipEdge],
    relationships: list[Relationship],
    applicant_party_id: str | None = None,
    structure_notes: list[str] | None = None,
) -> OwnershipGraphResult:
    """Resolve UBOs and structure opacity metrics from the extracted graph."""
    threshold = ubo_threshold_pct()
    party_names = {party.party_id: party.name for party in parties}
    notes_blob = " ".join(structure_notes or []).lower()

    out_edges, edge_pages = _build_out_edges(edges)
    applicant = _resolve_applicant(parties, edges, relationships, applicant_party_id)
    result = OwnershipGraphResult(applicant_party_id=applicant)
    result.num_parties = len(parties)
    result.num_entities = sum(1 for party in parties if party.party_type != PartyType.INDIVIDUAL)

    roles_by_party = _group_roles_by_party(relationships)
    if applicant is None:
        return result

    ubos: list[UBO] = []
    saw_cycle_any = False
    max_depth = 0
    for party in parties:
        if party.party_type != PartyType.INDIVIDUAL:
            continue
        effective_pct, path_texts, cited_pages, depth, saw_cycle = _effective_ownership_for(
            party, applicant, out_edges, party_names, edge_pages
        )
        saw_cycle_any = saw_cycle_any or saw_cycle
        max_depth = max(max_depth, depth)
        control_roles = [
            role for role in roles_by_party.get(party.party_id, []) if role in CONTROL_ROLES
        ]
        ubo = _build_individual_ubo(
            party, effective_pct, path_texts, cited_pages, control_roles, threshold
        )
        if ubo is not None:
            ubos.append(ubo)

    # TODO: this fallback looks unreachable — DIRECTOR is in CONTROL_ROLES, so any
    # individual director already qualifies as a control-basis UBO in the loop above.
    # Kept as a defensive backstop; revisit if CONTROL_ROLES ever drops DIRECTOR.
    if not ubos:
        fallback_ubo = _senior_official_fallback(parties, roles_by_party)
        if fallback_ubo is not None:
            result.notes.append(
                "No party meets the ownership threshold or holds a control role; "
                "senior managing official recorded as UBO (control basis)."
            )
            ubos.append(fallback_ubo)

    result.ubos = sorted(
        ubos,
        key=lambda ubo: (ubo.effective_ownership_pct or _UNKNOWN_OWNERSHIP_SORT_KEY, ubo.basis),
        reverse=True,
    )
    result.max_layering_depth = max_depth
    result.has_nominee = (
        any(party.party_type == PartyType.NOMINEE for party in parties)
        or any(RoleType.NOMINEE in roles for roles in roles_by_party.values())
        or "nominee" in notes_blob
    )
    result.has_bearer_shares = "bearer" in notes_blob
    result.has_circular_ownership = saw_cycle_any
    if saw_cycle_any:
        result.notes.append("Circular ownership detected in the structure.")
    return result

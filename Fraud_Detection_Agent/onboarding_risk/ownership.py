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

from typing import Dict, List, Optional, Tuple

from .reference import ubo_threshold_pct
from .schemas import (
    CONTROL_ROLES,
    OwnershipEdge,
    OwnershipGraphResult,
    Party,
    PartyType,
    Relationship,
    RoleType,
    UBO,
)


def _resolve_applicant(
    parties: List[Party],
    edges: List[OwnershipEdge],
    relationships: List[Relationship],
    applicant_party_id: Optional[str],
) -> Optional[str]:
    """Pick the applicant (root) entity id.

    Preference order: explicit metadata id -> party with role APPLICANT -> the entity
    that is owned by others but owns no one (the sink at the bottom of the structure).
    """
    ids = {p.party_id for p in parties}
    if applicant_party_id in ids:
        return applicant_party_id

    for r in relationships:
        if r.role == RoleType.APPLICANT and r.party_id in ids:
            return r.party_id

    owners = {e.owner_id for e in edges}
    owned = {e.owned_id for e in edges}
    sinks = owned - owners                       # owned but never an owner
    if len(sinks) == 1:
        return next(iter(sinks))
    # ambiguous: prefer a non-individual sink, else any party
    for pid in sinks:
        p = next((x for x in parties if x.party_id == pid), None)
        if p and p.party_type != PartyType.INDIVIDUAL:
            return pid
    return next(iter(sinks)) if sinks else (parties[0].party_id if parties else None)


def _paths_to_applicant(
    start: str,
    applicant: str,
    out_edges: Dict[str, List[OwnershipEdge]],
    party_name: Dict[str, str],
) -> Tuple[List[Tuple[float, str]], bool]:
    """All ownership paths from `start` down to `applicant`.

    Returns (paths, saw_cycle). Each path is (effective_fraction, human_readable_chain),
    where effective_fraction is the product of edge fractions (percentage/100). Edges
    with an unknown percentage are treated as 100% control for path-existence but the
    fraction is left at 0 so they don't inflate an ownership number.
    """
    results: List[Tuple[float, str]] = []
    saw_cycle = False

    def dfs(node: str, frac: float, chain: List[str], visiting: set):
        nonlocal saw_cycle
        if node == applicant and chain:
            results.append((frac, " -> ".join(chain + [party_name.get(applicant, applicant)])))
            return
        for e in out_edges.get(node, []):
            if e.owned_id in visiting:
                saw_cycle = True
                continue
            step = (e.percentage or 0.0) / 100.0
            dfs(
                e.owned_id,
                frac * step if e.percentage is not None else 0.0,
                chain + [party_name.get(node, node)],
                visiting | {e.owned_id},
            )

    dfs(start, 1.0, [], {start})
    return results, saw_cycle


def resolve_ubos(
    parties: List[Party],
    edges: List[OwnershipEdge],
    relationships: List[Relationship],
    applicant_party_id: Optional[str] = None,
    structure_notes: Optional[List[str]] = None,
) -> OwnershipGraphResult:
    """Resolve UBOs and structure opacity metrics from the extracted graph."""
    threshold = ubo_threshold_pct()
    by_id: Dict[str, Party] = {p.party_id: p for p in parties}
    name = {p.party_id: p.name for p in parties}
    notes_blob = " ".join(structure_notes or []).lower()

    out_edges: Dict[str, List[OwnershipEdge]] = {}
    for e in edges:
        out_edges.setdefault(e.owner_id, []).append(e)
    edge_page: Dict[Tuple[str, str], Optional[int]] = {
        (e.owner_id, e.owned_id): e.page for e in edges
    }

    applicant = _resolve_applicant(parties, edges, relationships, applicant_party_id)
    result = OwnershipGraphResult(applicant_party_id=applicant)
    result.num_parties = len(parties)
    result.num_entities = sum(
        1 for p in parties if p.party_type != PartyType.INDIVIDUAL
    )

    # control roles per party
    roles_by_party: Dict[str, List[RoleType]] = {}
    for r in relationships:
        roles_by_party.setdefault(r.party_id, []).append(r.role)

    saw_cycle_any = False
    max_depth = 0
    ubos: List[UBO] = []

    if applicant is None:
        return result

    for p in parties:
        if p.party_type != PartyType.INDIVIDUAL:
            continue
        eff_pct: Optional[float] = None
        paths_text: List[str] = []
        pages: List[int] = []

        if p.party_id == applicant:
            eff_pct = 100.0
        else:
            paths, saw_cycle = _paths_to_applicant(p.party_id, applicant, out_edges, name)
            saw_cycle_any = saw_cycle_any or saw_cycle
            if paths:
                eff_pct = round(sum(f for f, _ in paths) * 100.0, 4)
                paths_text = [t for _, t in paths]
                # depth = legal entities in the longest chain (chain nodes minus the person)
                for _, t in paths:
                    depth = t.count("->")        # number of hops == entities below the person
                    max_depth = max(max_depth, depth)
                # collect pages for the edges along the person's direct first hop
                for e in out_edges.get(p.party_id, []):
                    pg = edge_page.get((e.owner_id, e.owned_id))
                    if pg is not None:
                        pages.append(pg)

        control_roles = [r for r in roles_by_party.get(p.party_id, []) if r in CONTROL_ROLES]
        is_owner_ubo = eff_pct is not None and eff_pct >= threshold
        is_control_ubo = len(control_roles) > 0

        if is_owner_ubo or is_control_ubo:
            ubos.append(UBO(
                party_id=p.party_id,
                name=p.name,
                effective_ownership_pct=eff_pct,
                basis="ownership" if is_owner_ubo else "control",
                control_roles=[r.value for r in control_roles],
                paths=paths_text,
                pages=sorted(set(pages)) if pages else ([p.page] if p.page else []),
            ))

    # senior-managing-official fallback: no UBO on ownership or control
    if not ubos:
        director = next(
            (p for p in parties
             if RoleType.DIRECTOR in roles_by_party.get(p.party_id, [])
             and p.party_type == PartyType.INDIVIDUAL),
            None,
        )
        if director is not None:
            result.notes.append(
                "No party meets the ownership threshold or holds a control role; "
                "senior managing official recorded as UBO (control basis)."
            )
            ubos.append(UBO(
                party_id=director.party_id, name=director.name,
                effective_ownership_pct=None, basis="senior_managing_official",
                control_roles=[RoleType.DIRECTOR.value],
                pages=[director.page] if director.page else [],
            ))

    # opacity signals
    has_nominee = (
        any(p.party_type == PartyType.NOMINEE for p in parties)
        or any(RoleType.NOMINEE in roles for roles in roles_by_party.values())
        or "nominee" in notes_blob
    )
    has_bearer = "bearer" in notes_blob

    ubos.sort(
        key=lambda u: (u.effective_ownership_pct or -1.0, u.basis),
        reverse=True,
    )
    result.ubos = ubos
    result.max_layering_depth = max_depth
    result.has_nominee = has_nominee
    result.has_bearer_shares = has_bearer
    result.has_circular_ownership = saw_cycle_any
    if saw_cycle_any:
        result.notes.append("Circular ownership detected in the structure.")
    return result

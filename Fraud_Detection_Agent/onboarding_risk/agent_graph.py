"""
Orchestration for HNI/UHNI customer-onboarding risk scoring.

The onboarding pack is indexed once, then grounded extraction reads the parties,
ownership edges, roles, source-of-wealth and identity evidence. The deterministic
engine resolves UBOs, screens every party, scores the case and decides the onboarding
path. The LLM is used only for language tasks (extraction, EDD narrative, Q&A); every
decision-bearing number is computed in deterministic Python.

    pack PDFs
      -> doc_index (pages + embeddings)
      -> extract_structure / source_of_wealth / identity / metadata     (LLM, grounded)
      -> resolve_ubos                                                    (deterministic)
      -> screen_parties                                                  (deterministic)
      -> score_case + decide                                            (deterministic)
      -> synthesize_edd_rationale + Q&A                                  (LLM, grounded)
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_llm
from .doc_index import PageIndex, load_pack_as_pages
from .edd import synthesize_edd_rationale
from .extraction import (
    detect_case_metadata,
    extract_identity,
    extract_source_of_wealth,
    extract_structure,
)
from .ownership import resolve_ubos
from .schemas import OnboardingAssessment
from .scoring import score_case
from .screening import screen_parties
from .tools import set_page_index


def assess_pack(pdf_paths: List[str]) -> OnboardingAssessment:
    """Run the full onboarding assessment on a pack of PDFs and return the result."""
    pages = load_pack_as_pages(pdf_paths)
    index = PageIndex(pages)
    set_page_index(index)

    # 1. grounded extraction (LLM, language only)
    structure = extract_structure(index)
    parties = structure["parties"]
    edges = structure["edges"]
    relationships = structure["relationships"]
    sow = extract_source_of_wealth(index, parties)
    identity = extract_identity(index, parties)
    meta = detect_case_metadata(index, parties)
    if not meta.applicant_name and meta.applicant_party_id:
        match = next((p for p in parties if p.party_id == meta.applicant_party_id), None)
        if match:
            meta.applicant_name = match.name

    # 2. deterministic engine (no LLM)
    ownership = resolve_ubos(
        parties, edges, relationships,
        applicant_party_id=meta.applicant_party_id,
        structure_notes=structure.get("notes"),
    )
    watchlist_hits, geography_hits = screen_parties(parties)
    scorecard = score_case(ownership, watchlist_hits, geography_hits, sow, identity)

    assessment = OnboardingAssessment(
        case=meta, parties=parties, edges=edges, relationships=relationships,
        source_of_wealth=sow, identity_checks=identity, ownership=ownership,
        watchlist_hits=watchlist_hits, geography_hits=geography_hits, scorecard=scorecard,
    )

    # 3. grounded EDD narrative (LLM, grounded in the deterministic scorecard)
    assessment.edd_rationale = synthesize_edd_rationale(assessment)
    return assessment


def _answer_over_case(assessment: OnboardingAssessment, question: str) -> str:
    sc = assessment.scorecard
    compact = {
        "applicant": assessment.case.applicant_name,
        "decision": sc.decision.value,
        "band": sc.band.value,
        "normalized_score": sc.normalized_score,
        "triggers": [t.value for t in sc.triggers],
        "ubos": [
            {"name": u.name, "effective_ownership_pct": u.effective_ownership_pct,
             "basis": u.basis, "control_roles": u.control_roles}
            for u in assessment.ownership.ubos
        ],
        "active_factors": [
            {"factor": f.factor.value, "severity": f.severity, "evidence": f.evidence}
            for f in sc.factors if f.present
        ],
        "watchlist_hits": [
            {"party": h.party_name, "type": h.list_type, "details": h.details}
            for h in assessment.watchlist_hits
        ],
    }
    system = SystemMessage(content=(
        "You answer questions about a completed onboarding risk assessment. Use ONLY the "
        "supplied figures (already correct and final). If the assessment does not contain "
        "the answer, say so. Never invent parties, percentages, or a different rating."
    ))
    user = HumanMessage(content=(
        f"Question:\n{question}\n\nAssessment:\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Answer concisely."
    ))
    return get_llm().invoke([system, user]).content


def run_onboarding_assessment(
    pdf_paths: List[str], question: str = ""
) -> Dict[str, Any]:
    """Assess an onboarding pack end-to-end and answer an optional question.

    Returns the assessment, a human-readable reasoning trace, an optional answer, and
    run metrics (latency, approximate LLM-call count).
    """
    start = time.time()
    reasoning: List[str] = []
    metrics: Dict[str, Any] = {"documents": len(pdf_paths)}

    assessment = assess_pack(pdf_paths)
    sc = assessment.scorecard
    og = assessment.ownership

    # ~ structure + sow + identity + metadata + edd narrative
    metrics["llm_calls"] = 5

    reasoning.append(
        f"**Extraction** → parties={len(assessment.parties)}, "
        f"ownership_edges={len(assessment.edges)}, roles={len(assessment.relationships)}, "
        f"source_of_wealth={len(assessment.source_of_wealth)}, "
        f"identity_checks={len(assessment.identity_checks)}"
    )
    reasoning.append(
        f"**Ownership (deterministic)** → applicant=`{assessment.case.applicant_name or '—'}`, "
        f"entities={og.num_entities}, layering_depth={og.max_layering_depth}, "
        f"UBOs={', '.join(f'{u.name}({u.effective_ownership_pct}% / {u.basis})' for u in og.ubos) or '—'}, "
        f"nominee={og.has_nominee}, bearer={og.has_bearer_shares}, circular={og.has_circular_ownership}"
    )
    reasoning.append(
        f"**Screening (deterministic)** → watchlist_hits="
        f"{', '.join(f'{h.party_name}[{h.list_type}]' for h in assessment.watchlist_hits) or '—'}; "
        f"geography={', '.join(f'{g.party_name}:{g.country}({g.tier})' for g in assessment.geography_hits) or '—'}"
    )
    reasoning.append(
        f"**Scorecard (deterministic)** → score={sc.normalized_score}/100, band=`{sc.band.value}`, "
        f"EDD_required={sc.edd_required}, triggers={[t.value for t in sc.triggers]}, "
        f"decision=`{sc.decision.value}`"
        + (f", HARD STOP: {sc.hard_stop_reason}" if sc.hard_stop_reason else "")
    )

    answer = ""
    if question:
        answer = _answer_over_case(assessment, question)
        metrics["llm_calls"] += 1

    metrics["parties"] = len(assessment.parties)
    metrics["ubos"] = len(og.ubos)
    metrics["band"] = sc.band.value
    metrics["decision"] = sc.decision.value
    metrics["latency_ms"] = round((time.time() - start) * 1000.0, 2)

    return {
        "assessment": assessment,
        "answer": answer,
        "reasoning": reasoning,
        "metrics": metrics,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m onboarding_risk.agent_graph <pdf> [<pdf> ...] [-- question]")
        sys.exit(1)

    rest = sys.argv[1:]
    q = ""
    if "--" in rest:
        i = rest.index("--")
        q = " ".join(rest[i + 1:])
        rest = rest[:i]

    out = run_onboarding_assessment(rest, q)
    a = out["assessment"]
    sc = a.scorecard
    print("\n=== DECISION ===")
    print(f"{a.case.applicant_name or '—'}: band={sc.band.value}, "
          f"decision={sc.decision.value}, score={sc.normalized_score}/100")
    if sc.hard_stop_reason:
        print("HARD STOP:", sc.hard_stop_reason)
    print("\n=== UBOs ===")
    for u in a.ownership.ubos:
        print(f"  {u.name}: {u.effective_ownership_pct}% ({u.basis}) "
              f"roles={u.control_roles}")
    print("\n=== EDD TRIGGERS ===", [t.value for t in sc.triggers])
    print("\n=== EDD RATIONALE ===\n", a.edd_rationale.risk_summary)
    if out["answer"]:
        print("\n=== ANSWER ===\n", out["answer"])
    print("\n=== METRICS ===\n", out["metrics"])

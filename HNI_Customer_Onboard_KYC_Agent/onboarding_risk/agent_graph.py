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
import sys
import time
from typing import Any

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

_MS_PER_SECOND = 1000.0
_BASE_LLM_CALL_COUNT = 5  # structure + sow + identity + metadata + edd narrative
_MISSING_VALUE_PLACEHOLDER = "—"


def _resolve_applicant_name(assessment: OnboardingAssessment) -> None:
    case = assessment.case
    if case.applicant_name or not case.applicant_party_id:
        return
    matching_party = next(
        (party for party in assessment.parties if party.party_id == case.applicant_party_id),
        None,
    )
    if matching_party is not None:
        case.applicant_name = matching_party.name


def assess_pack(pdf_paths: list[str]) -> OnboardingAssessment:
    """Run the full onboarding assessment on a pack of PDFs and return the result."""
    pages = load_pack_as_pages(pdf_paths)
    index = PageIndex(pages)
    set_page_index(index)

    structure = extract_structure(index)
    parties = structure["parties"]
    edges = structure["edges"]
    relationships = structure["relationships"]
    wealth_entries = extract_source_of_wealth(index, parties)
    identity_checks = extract_identity(index, parties)
    case_metadata = detect_case_metadata(index, parties)

    ownership = resolve_ubos(
        parties,
        edges,
        relationships,
        applicant_party_id=case_metadata.applicant_party_id,
        structure_notes=structure.get("notes"),
    )
    watchlist_hits, geography_hits = screen_parties(parties)
    scorecard = score_case(
        ownership, watchlist_hits, geography_hits, wealth_entries, identity_checks
    )

    assessment = OnboardingAssessment(
        case=case_metadata,
        parties=parties,
        edges=edges,
        relationships=relationships,
        source_of_wealth=wealth_entries,
        identity_checks=identity_checks,
        ownership=ownership,
        watchlist_hits=watchlist_hits,
        geography_hits=geography_hits,
        scorecard=scorecard,
    )
    _resolve_applicant_name(assessment)

    assessment.edd_rationale = synthesize_edd_rationale(assessment)
    return assessment


def _compact_assessment(assessment: OnboardingAssessment) -> dict[str, Any]:
    scorecard = assessment.scorecard
    return {
        "applicant": assessment.case.applicant_name,
        "decision": scorecard.decision.value,
        "band": scorecard.band.value,
        "normalized_score": scorecard.normalized_score,
        "triggers": [trigger.value for trigger in scorecard.triggers],
        "ubos": [
            {
                "name": ubo.name,
                "effective_ownership_pct": ubo.effective_ownership_pct,
                "basis": ubo.basis,
                "control_roles": ubo.control_roles,
            }
            for ubo in assessment.ownership.ubos
        ],
        "active_factors": [
            {
                "factor": factor.factor.value,
                "severity": factor.severity,
                "evidence": factor.evidence,
            }
            for factor in scorecard.factors
            if factor.present
        ],
        "watchlist_hits": [
            {"party": hit.party_name, "type": hit.list_type, "details": hit.details}
            for hit in assessment.watchlist_hits
        ],
    }


def _answer_over_case(assessment: OnboardingAssessment, question: str) -> str:
    system = SystemMessage(
        content=(
            "You answer questions about a completed onboarding risk assessment. Use ONLY the "
            "supplied figures (already correct and final). If the assessment does not contain "
            "the answer, say so. Never invent parties, percentages, or a different rating."
        )
    )
    user = HumanMessage(
        content=(
            f"Question:\n{question}\n\nAssessment:\n"
            f"{json.dumps(_compact_assessment(assessment), indent=2, default=str)}\n\n"
            "Answer concisely."
        )
    )
    return get_llm().invoke([system, user]).content


def _extraction_trace(assessment: OnboardingAssessment) -> str:
    return (
        f"**Extraction** → parties={len(assessment.parties)}, "
        f"ownership_edges={len(assessment.edges)}, roles={len(assessment.relationships)}, "
        f"source_of_wealth={len(assessment.source_of_wealth)}, "
        f"identity_checks={len(assessment.identity_checks)}"
    )


def _ownership_trace(assessment: OnboardingAssessment) -> str:
    ownership = assessment.ownership
    ubo_summary = ", ".join(
        f"{ubo.name}({ubo.effective_ownership_pct}% / {ubo.basis})" for ubo in ownership.ubos
    )
    return (
        "**Ownership (deterministic)** → "
        f"applicant=`{assessment.case.applicant_name or _MISSING_VALUE_PLACEHOLDER}`, "
        f"entities={ownership.num_entities}, layering_depth={ownership.max_layering_depth}, "
        f"UBOs={ubo_summary or _MISSING_VALUE_PLACEHOLDER}, "
        f"nominee={ownership.has_nominee}, bearer={ownership.has_bearer_shares}, "
        f"circular={ownership.has_circular_ownership}"
    )


def _screening_trace(assessment: OnboardingAssessment) -> str:
    watchlist_summary = ", ".join(
        f"{hit.party_name}[{hit.list_type}]" for hit in assessment.watchlist_hits
    )
    geography_summary = ", ".join(
        f"{hit.party_name}:{hit.country}({hit.tier})" for hit in assessment.geography_hits
    )
    return (
        "**Screening (deterministic)** → "
        f"watchlist_hits={watchlist_summary or _MISSING_VALUE_PLACEHOLDER}; "
        f"geography={geography_summary or _MISSING_VALUE_PLACEHOLDER}"
    )


def _scorecard_trace(assessment: OnboardingAssessment) -> str:
    scorecard = assessment.scorecard
    hard_stop_suffix = (
        f", HARD STOP: {scorecard.hard_stop_reason}" if scorecard.hard_stop_reason else ""
    )
    return (
        "**Scorecard (deterministic)** → "
        f"score={scorecard.normalized_score}/100, band=`{scorecard.band.value}`, "
        f"EDD_required={scorecard.edd_required}, "
        f"triggers={[trigger.value for trigger in scorecard.triggers]}, "
        f"decision=`{scorecard.decision.value}`{hard_stop_suffix}"
    )


def run_onboarding_assessment(pdf_paths: list[str], question: str = "") -> dict[str, Any]:
    """Assess an onboarding pack end-to-end and answer an optional question.

    Returns the assessment, a human-readable reasoning trace, an optional answer, and
    run metrics (latency, approximate LLM-call count).
    """
    start = time.time()
    assessment = assess_pack(pdf_paths)
    scorecard = assessment.scorecard
    ownership = assessment.ownership

    metrics: dict[str, Any] = {
        "documents": len(pdf_paths),
        "llm_calls": _BASE_LLM_CALL_COUNT,
    }
    reasoning = [
        _extraction_trace(assessment),
        _ownership_trace(assessment),
        _screening_trace(assessment),
        _scorecard_trace(assessment),
    ]

    answer = ""
    if question:
        answer = _answer_over_case(assessment, question)
        metrics["llm_calls"] += 1

    metrics["parties"] = len(assessment.parties)
    metrics["ubos"] = len(ownership.ubos)
    metrics["band"] = scorecard.band.value
    metrics["decision"] = scorecard.decision.value
    metrics["latency_ms"] = round((time.time() - start) * _MS_PER_SECOND, 2)

    return {
        "assessment": assessment,
        "answer": answer,
        "reasoning": reasoning,
        "metrics": metrics,
    }


def _parse_pack_cli_args(args: list[str]) -> tuple[list[str], str]:
    if "--" in args:
        separator = args.index("--")
        return args[:separator], " ".join(args[separator + 1 :])
    return args, ""


def _print_pack_result(result: dict[str, Any]) -> None:
    assessment = result["assessment"]
    scorecard = assessment.scorecard
    print("\n=== DECISION ===")
    print(
        f"{assessment.case.applicant_name or _MISSING_VALUE_PLACEHOLDER}: "
        f"band={scorecard.band.value}, "
        f"decision={scorecard.decision.value}, score={scorecard.normalized_score}/100"
    )
    if scorecard.hard_stop_reason:
        print("HARD STOP:", scorecard.hard_stop_reason)
    print("\n=== UBOs ===")
    for ubo in assessment.ownership.ubos:
        print(
            f"  {ubo.name}: {ubo.effective_ownership_pct}% ({ubo.basis}) roles={ubo.control_roles}"
        )
    print("\n=== EDD TRIGGERS ===", [trigger.value for trigger in scorecard.triggers])
    print("\n=== EDD RATIONALE ===\n", assessment.edd_rationale.risk_summary)
    if result["answer"]:
        print("\n=== ANSWER ===\n", result["answer"])
    print("\n=== METRICS ===\n", result["metrics"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m onboarding_risk.agent_graph <pdf> [<pdf> ...] [-- question]")
        sys.exit(1)

    pack_paths, cli_question = _parse_pack_cli_args(sys.argv[1:])
    _print_pack_result(run_onboarding_assessment(pack_paths, cli_question))

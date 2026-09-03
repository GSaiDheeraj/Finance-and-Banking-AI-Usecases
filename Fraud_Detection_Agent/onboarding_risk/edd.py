"""
Grounded EDD-rationale synthesis.

The deterministic layer (ownership graph, screening hits, scorecard, decision) produces
the numbers and the rating; the LLM only writes the narrative — a risk summary, the key
drivers, the EDD measures to apply, and the information still required. It is instructed
to quote the supplied figures and the deterministic decision verbatim and to **never
re-rate** the case. This keeps the rating reproducible while making the file readable.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_llm
from .schemas import (
    EDDRationale,
    GeographyHit,
    OnboardingAssessment,
    OwnershipGraphResult,
    RiskScorecard,
    WatchlistHit,
)


def _compact(assessment: OnboardingAssessment) -> Dict[str, Any]:
    sc: RiskScorecard = assessment.scorecard
    og: OwnershipGraphResult = assessment.ownership
    return {
        "applicant": assessment.case.applicant_name,
        "product": assessment.case.product,
        "relationship_purpose": assessment.case.relationship_purpose,
        "decision": sc.decision.value,
        "band": sc.band.value,
        "normalized_score": sc.normalized_score,
        "edd_required": sc.edd_required,
        "hard_stop_reason": sc.hard_stop_reason,
        "triggers": [t.value for t in sc.triggers],
        "active_factors": [
            {"factor": f.factor.value, "severity": f.severity,
             "contribution": f.contribution, "evidence": f.evidence}
            for f in sc.factors if f.present
        ],
        "ubos": [
            {"name": u.name, "effective_ownership_pct": u.effective_ownership_pct,
             "basis": u.basis, "control_roles": u.control_roles}
            for u in og.ubos
        ],
        "structure": {
            "entities": og.num_entities, "parties": og.num_parties,
            "layering_depth": og.max_layering_depth,
            "nominee": og.has_nominee, "bearer_shares": og.has_bearer_shares,
            "circular": og.has_circular_ownership,
        },
        "watchlist_hits": [
            {"party": h.party_name, "type": h.list_type, "matched": h.matched_name,
             "details": h.details} for h in assessment.watchlist_hits
        ],
        "geography_hits": [
            {"party": g.party_name, "country": g.country, "tier": g.tier}
            for g in assessment.geography_hits
        ],
    }


def synthesize_edd_rationale(assessment: OnboardingAssessment) -> EDDRationale:
    """Write the grounded EDD narrative for a scored onboarding case."""
    compact = _compact(assessment)

    system = SystemMessage(content=(
        "You are a financial-crime / EDD analyst writing the rationale for an HNI/UHNI "
        "onboarding case. You are given a DETERMINISTICALLY computed risk scorecard, "
        "ownership graph and decision — these are AUTHORITATIVE. Do NOT recompute the "
        "score, change the band, or override the decision; explain them.\n"
        "Write the rationale grounded strictly in the supplied facts, quoting the UBOs, "
        "effective ownership %, triggers and hits. Recommend concrete EDD measures "
        "(e.g. source-of-wealth corroboration, senior sign-off, ongoing monitoring) and "
        "list the specific information still required to clear the case.\n"
        "Output ONLY a JSON object:\n"
        "{\n"
        '  "risk_summary": str,\n'
        '  "key_drivers": [str],\n'
        '  "edd_measures": [str],\n'
        '  "information_required": [str],\n'
        '  "watch_items": [str]\n'
        "}"
    ))
    user = HumanMessage(content=(
        f"Computed assessment (authoritative):\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Write the EDD-rationale JSON now."
    ))

    raw = get_llm().invoke([system, user]).content
    parsed: Dict[str, Any] = {}
    try:
        txt = raw.strip()
        if txt.startswith("```"):
            txt = "\n".join(txt.split("\n")[1:])
            if txt.rstrip().endswith("```"):
                txt = txt.rstrip()[:-3]
        parsed = json.loads(txt)
    except Exception:
        parsed = {}

    def _list(key: str) -> List[str]:
        return [str(x) for x in (parsed.get(key) or []) if x]

    return EDDRationale(
        risk_summary=parsed.get("risk_summary", "") or "",
        key_drivers=_list("key_drivers"),
        edd_measures=_list("edd_measures"),
        information_required=_list("information_required"),
        watch_items=_list("watch_items"),
    )

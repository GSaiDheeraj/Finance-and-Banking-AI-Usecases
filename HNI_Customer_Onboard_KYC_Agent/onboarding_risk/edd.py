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
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_llm
from .schemas import (
    EDDRationale,
    OnboardingAssessment,
)

_log = logging.getLogger(__name__)

_EDD_SYSTEM_PROMPT = (
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
)


def _compact_factors(assessment: OnboardingAssessment) -> list[dict[str, Any]]:
    return [
        {
            "factor": factor.factor.value,
            "severity": factor.severity,
            "contribution": factor.contribution,
            "evidence": factor.evidence,
        }
        for factor in assessment.scorecard.factors
        if factor.present
    ]


def _compact_ubos(assessment: OnboardingAssessment) -> list[dict[str, Any]]:
    return [
        {
            "name": ubo.name,
            "effective_ownership_pct": ubo.effective_ownership_pct,
            "basis": ubo.basis,
            "control_roles": ubo.control_roles,
        }
        for ubo in assessment.ownership.ubos
    ]


def _compact(assessment: OnboardingAssessment) -> dict[str, Any]:
    scorecard = assessment.scorecard
    ownership = assessment.ownership
    return {
        "applicant": assessment.case.applicant_name,
        "product": assessment.case.product,
        "relationship_purpose": assessment.case.relationship_purpose,
        "decision": scorecard.decision.value,
        "band": scorecard.band.value,
        "normalized_score": scorecard.normalized_score,
        "edd_required": scorecard.edd_required,
        "hard_stop_reason": scorecard.hard_stop_reason,
        "triggers": [trigger.value for trigger in scorecard.triggers],
        "active_factors": _compact_factors(assessment),
        "ubos": _compact_ubos(assessment),
        "structure": {
            "entities": ownership.num_entities,
            "parties": ownership.num_parties,
            "layering_depth": ownership.max_layering_depth,
            "nominee": ownership.has_nominee,
            "bearer_shares": ownership.has_bearer_shares,
            "circular": ownership.has_circular_ownership,
        },
        "watchlist_hits": [
            {
                "party": hit.party_name,
                "type": hit.list_type,
                "matched": hit.matched_name,
                "details": hit.details,
            }
            for hit in assessment.watchlist_hits
        ],
        "geography_hits": [
            {"party": hit.party_name, "country": hit.country, "tier": hit.tier}
            for hit in assessment.geography_hits
        ],
    }


def _parse_edd_json(raw_response: str) -> dict[str, Any]:
    response_text = raw_response.strip()
    if response_text.startswith("```"):
        response_lines = response_text.split("\n")[1:]
        response_text = "\n".join(response_lines)
        if response_text.rstrip().endswith("```"):
            response_text = response_text.rstrip()[:-3]
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        _log.warning("EDD rationale was not valid JSON: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_list(parsed: dict[str, Any], key: str) -> list[str]:
    return [str(item) for item in (parsed.get(key) or []) if item]


def synthesize_edd_rationale(assessment: OnboardingAssessment) -> EDDRationale:
    """Write the grounded EDD narrative for a scored onboarding case."""
    compact = _compact(assessment)
    system = SystemMessage(content=_EDD_SYSTEM_PROMPT)
    user = HumanMessage(
        content=(
            f"Computed assessment (authoritative):\n{json.dumps(compact, indent=2, default=str)}\n\n"
            "Write the EDD-rationale JSON now."
        )
    )

    raw_response = get_llm().invoke([system, user]).content
    parsed = _parse_edd_json(raw_response)
    return EDDRationale(
        risk_summary=parsed.get("risk_summary", "") or "",
        key_drivers=_string_list(parsed, "key_drivers"),
        edd_measures=_string_list(parsed, "edd_measures"),
        information_required=_string_list(parsed, "information_required"),
        watch_items=_string_list(parsed, "watch_items"),
    )

"""
Grounded credit-memo synthesis.

The deterministic layer (ratios, benchmark, trend, scorecard, decision) produces the
numbers and the rating; the LLM only writes the narrative — an executive summary, a
financial-analysis paragraph, the strengths/risks/mitigants, recommended covenants and
monitoring triggers. It is instructed to quote the supplied figures and the
deterministic grade/decision verbatim and to **never re-rate** the case. This keeps the
rating reproducible while making the memo readable.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_llm
from .llm_json import safe_json_loads
from .schemas import CreditAssessment, CreditMemo


def _compact(assessment: CreditAssessment) -> Dict[str, Any]:
    sc = assessment.scorecard
    latest = assessment.ratios_by_period[-1] if assessment.ratios_by_period else None
    latest_ratios = (
        {name: rv.value for name, rv in latest.ratios.items() if rv.value is not None}
        if latest else {}
    )
    return {
        "company": assessment.company.company_name,
        "industry": assessment.company.industry,
        "country": assessment.company.country,
        "audit_opinion": assessment.company.audit_opinion.value,
        "facility": {
            "type": assessment.facility.facility_type,
            "amount": assessment.facility.amount,
            "currency": assessment.facility.currency,
            "purpose": assessment.facility.purpose,
        },
        "periods": assessment.periods,
        "rating_grade": sc.grade.value,
        "risk_band": sc.band.value,
        "normalized_score": sc.normalized_score,
        "probability_of_default": sc.probability_of_default,
        "loss_given_default": sc.loss_given_default,
        "expected_loss_pct": sc.expected_loss_pct,
        "decision": sc.decision.value,
        "suggested_pricing": sc.suggested_pricing,
        "hard_stop_reason": sc.hard_stop_reason,
        "deterministic_conditions": sc.conditions,
        "latest_ratios": latest_ratios,
        "active_factors": [
            {"factor": f.factor.value, "severity": f.severity,
             "contribution": f.contribution, "evidence": f.evidence}
            for f in sc.factors if f.present
        ],
        "sector_benchmark": {
            "industry_used": assessment.benchmark.industry_used,
            "overall_position": assessment.benchmark.overall_position,
            "material_gaps": assessment.benchmark.material_gaps,
        },
        "trend": {
            "overall_trajectory": assessment.trend.overall_trajectory,
            "commentary_signal": assessment.trend.commentary_signal,
            "ratio_trends": [
                {"ratio": t.ratio_name, "direction": t.direction, "magnitude": t.magnitude}
                for t in assessment.trend.trends
            ],
        },
        "off_balance_sheet": [
            {"category": n.category, "description": n.description, "amount": n.amount}
            for n in assessment.notes
        ],
        "web_sentiment": (
            {
                "overall_sentiment": assessment.web_sentiment.overall_sentiment,
                "disambiguation_confidence": assessment.web_sentiment.disambiguation_confidence,
                "summary": assessment.web_sentiment.sentiment_summary,
                "adverse_findings": [
                    {"category": f.category, "summary": f.summary, "severity": f.severity}
                    for f in assessment.web_sentiment.adverse_findings
                ],
                "positive_highlights": assessment.web_sentiment.positive_highlights,
            }
            if assessment.web_sentiment else None
        ),
    }


def synthesize_credit_memo(assessment: CreditAssessment) -> CreditMemo:
    """Write the grounded credit memo for a scored credit case."""
    compact = _compact(assessment)

    system = SystemMessage(content=(
        "You are a senior credit analyst writing the credit memo for a corporate lending "
        "case. You are given a DETERMINISTICALLY computed ratio set, sector benchmark, "
        "trend analysis, credit scorecard, internal rating grade, PD/LGD and lending "
        "decision — these are AUTHORITATIVE. Do NOT recompute any ratio, change the grade, "
        "alter the PD/LGD, or override the decision; explain them.\n"
        "Write the memo grounded strictly in the supplied facts, quoting the actual ratio "
        "values, the rating grade, the trajectory and the sector position. Identify genuine "
        "strengths and risks from the active factors, propose concrete mitigants, and "
        "recommend covenants and monitoring triggers consistent with the deterministic "
        "conditions. Be balanced and specific — no boilerplate.\n"
        "If `web_sentiment` is present, treat it as CORROBORATING qualitative colour only "
        "(it is public web/news, weaker than audited accounts); reference it where it "
        "supports or qualifies the financial picture, but never let it override the "
        "fundamentals-driven rating, and flag low disambiguation confidence if noted.\n"
        "Output ONLY a JSON object:\n"
        "{\n"
        '  "executive_summary": str,\n'
        '  "financial_analysis": str,\n'
        '  "key_strengths": [str],\n'
        '  "key_risks": [str],\n'
        '  "mitigants": [str],\n'
        '  "recommended_covenants": [str],\n'
        '  "monitoring_triggers": [str]\n'
        "}"
    ))
    user = HumanMessage(content=(
        f"Computed assessment (authoritative):\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Write the credit-memo JSON now."
    ))

    raw = get_llm().invoke([system, user]).content
    parsed = safe_json_loads(raw) or {}

    def _list(key: str) -> List[str]:
        return [str(x) for x in (parsed.get(key) or []) if x]

    return CreditMemo(
        executive_summary=parsed.get("executive_summary", "") or "",
        financial_analysis=parsed.get("financial_analysis", "") or "",
        key_strengths=_list("key_strengths"),
        key_risks=_list("key_risks"),
        mitigants=_list("mitigants"),
        recommended_covenants=_list("recommended_covenants"),
        monitoring_triggers=_list("monitoring_triggers"),
    )

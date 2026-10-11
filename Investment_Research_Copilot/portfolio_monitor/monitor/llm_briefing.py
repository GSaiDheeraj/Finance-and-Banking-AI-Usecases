"""
Grounded LLM monitoring briefing + case Q&A.

The deterministic layer produces every number, alert and the health score; the LLM only
writes the narrative — grounded strictly in those facts, quoting the actual weights, metrics
and alert titles, and never recomputing or overriding them. Q&A is answered ONLY from the
computed result. If the LLM is unavailable, callers fall back to the deterministic briefing.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_llm, llm_available
from ..logconf import get_logger
from ..schemas import MonitoringBriefing, MonitoringResult

_log = get_logger()


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _compact(result: MonitoringResult) -> dict[str, Any]:
    allocation = result.allocation
    drift = result.drift
    risk_metrics = result.risk_metrics
    score = result.score
    return {
        "portfolio": result.portfolio.name,
        "mandate": result.portfolio.mandate,
        "market_value": allocation.total_market_value,
        "base_currency": allocation.base_currency,
        "health_band": score.health_band.value,
        "risk_score": score.normalized_score,
        "asset_class_mix": {b.name: round(b.weight, 4) for b in allocation.by_asset_class},
        "drift_breaches": [
            {
                "sleeve": line.bucket,
                "actual": line.actual_weight,
                "target": line.target_weight,
                "status": line.status.value,
            }
            for line in drift.lines
            if line.status.value.startswith("breach")
        ],
        "rebalance_summary": result.rebalance.summary,
        "risk_metrics": {
            "volatility": risk_metrics.volatility_annual,
            "max_drawdown": risk_metrics.max_drawdown,
            "var_95_1d": risk_metrics.var_95_1d_pct,
            "beta": risk_metrics.beta,
            "hhi": risk_metrics.concentration_hhi,
            "effective_n": risk_metrics.effective_n,
            "top1": risk_metrics.top1_weight,
            "largest_position": risk_metrics.largest_position,
            "data_coverage": risk_metrics.data_coverage,
        },
        "limit_breaches": [
            {
                "name": check.name,
                "observed": check.observed,
                "limit": check.limit,
                "status": check.status,
            }
            for check in result.limit_checks
            if check.status in ("breach", "warn")
        ],
        "cashflow": {
            "expected_income": result.cashflow.total_expected_income,
            "actual_income": result.cashflow.total_actual_income,
            "net_variance": result.cashflow.net_variance,
            "flags": [
                {"symbol": v.symbol, "status": v.status, "variance": v.variance}
                for v in result.cashflow.variances
                if v.status != "on_track"
            ],
        },
        "alerts": [
            {
                "type": al.type.value,
                "severity": al.severity.value,
                "title": al.title,
                "action": al.recommended_action,
            }
            for al in result.alerts
        ],
        "emerging_risk": (
            {
                "tone": result.risk_scan.overall_risk_tone,
                "findings": [
                    {
                        "subject": finding.subject,
                        "category": finding.category.value,
                        "severity": finding.severity,
                        "summary": finding.summary,
                    }
                    for finding in result.risk_scan.findings
                ],
            }
            if result.risk_scan
            else None
        ),
    }


def build_briefing_llm(result: MonitoringResult) -> MonitoringBriefing:
    """Write the grounded monitoring briefing from the deterministic facts."""
    compact = _compact(result)
    system = SystemMessage(
        content=(
            "You are an investment-risk officer writing a monitoring briefing. You are given a "
            "DETERMINISTICALLY computed snapshot: allocation, drift vs policy, a rebalancing "
            "summary, risk metrics, limit breaches, cash-flow variances, prioritised alerts and a "
            "health score — these are AUTHORITATIVE. Do NOT recompute any number, change a weight, "
            "alter the health band, or invent an alert; explain them.\n"
            "Write grounded prose quoting the actual figures and alert titles. Treat the "
            "emerging-risk (public-news) signals as corroborating colour only — never let them "
            "override a hard limit breach. Output ONLY JSON:\n"
            '{"executive_summary": str, "allocation_commentary": str, "risk_commentary": str, '
            '"cashflow_commentary": str, "recommended_actions": [str], "watch_items": [str]}'
        )
    )
    user = HumanMessage(
        content=(
            f"Computed snapshot (authoritative):\n{json.dumps(compact, indent=2, default=str)}\n\n"
            "Write the briefing JSON now."
        )
    )

    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("briefing[llm]: failed (%s) — caller should use deterministic.", e)
        return MonitoringBriefing(method="llm", executive_summary="(LLM briefing unavailable.)")

    def _list(key: str) -> list[str]:
        return [str(x) for x in (parsed.get(key) or []) if x]

    return MonitoringBriefing(
        method="llm",
        executive_summary=str(parsed.get("executive_summary", "")),
        allocation_commentary=str(parsed.get("allocation_commentary", "")),
        risk_commentary=str(parsed.get("risk_commentary", "")),
        cashflow_commentary=str(parsed.get("cashflow_commentary", "")),
        recommended_actions=_list("recommended_actions"),
        watch_items=_list("watch_items"),
    )


def answer_question(result: MonitoringResult, question: str) -> str:
    """Answer a question ONLY from the computed monitoring result."""
    if not question.strip():
        return ""
    if not llm_available():
        return "(LLM not configured — set LLM_ENDPOINT / LLM_API_KEY to enable Q&A.)"
    compact = _compact(result)
    system = SystemMessage(
        content=(
            "You answer questions about a completed portfolio monitoring run. "
            "Use ONLY the supplied figures (already correct and final). "
            "If the snapshot does not contain the answer, say "
            "so. Never invent a metric, a weight, an alert, or a different health band."
        )
    )
    user = HumanMessage(
        content=(
            f"Question:\n{question}\n\nSnapshot:\n{json.dumps(compact, indent=2, default=str)}\n\n"
            "Answer concisely."
        )
    )
    return get_llm().invoke([system, user]).content

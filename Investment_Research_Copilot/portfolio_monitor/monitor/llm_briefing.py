"""
Grounded LLM monitoring briefing + case Q&A.

The deterministic layer produces every number, alert and the health score; the LLM only
writes the narrative — grounded strictly in those facts, quoting the actual weights, metrics
and alert titles, and never recomputing or overriding them. Q&A is answered ONLY from the
computed result. If the LLM is unavailable, callers fall back to the deterministic briefing.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

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


def _compact(result: MonitoringResult) -> Dict[str, Any]:
    a, d, m, sc = result.allocation, result.drift, result.risk_metrics, result.score
    return {
        "portfolio": result.portfolio.name,
        "mandate": result.portfolio.mandate,
        "market_value": a.total_market_value,
        "base_currency": a.base_currency,
        "health_band": sc.health_band.value,
        "risk_score": sc.normalized_score,
        "asset_class_mix": {b.name: round(b.weight, 4) for b in a.by_asset_class},
        "drift_breaches": [
            {"sleeve": l.bucket, "actual": l.actual_weight, "target": l.target_weight,
             "status": l.status.value}
            for l in d.lines if l.status.value.startswith("breach")
        ],
        "rebalance_summary": result.rebalance.summary,
        "risk_metrics": {
            "volatility": m.volatility_annual, "max_drawdown": m.max_drawdown,
            "var_95_1d": m.var_95_1d_pct, "beta": m.beta, "hhi": m.concentration_hhi,
            "effective_n": m.effective_n, "top1": m.top1_weight,
            "largest_position": m.largest_position, "data_coverage": m.data_coverage,
        },
        "limit_breaches": [
            {"name": c.name, "observed": c.observed, "limit": c.limit, "status": c.status}
            for c in result.limit_checks if c.status in ("breach", "warn")
        ],
        "cashflow": {
            "expected_income": result.cashflow.total_expected_income,
            "actual_income": result.cashflow.total_actual_income,
            "net_variance": result.cashflow.net_variance,
            "flags": [
                {"symbol": v.symbol, "status": v.status, "variance": v.variance}
                for v in result.cashflow.variances if v.status != "on_track"
            ],
        },
        "alerts": [
            {"type": al.type.value, "severity": al.severity.value, "title": al.title,
             "action": al.recommended_action}
            for al in result.alerts
        ],
        "emerging_risk": (
            {"tone": result.risk_scan.overall_risk_tone,
             "findings": [{"subject": f.subject, "category": f.category.value,
                           "severity": f.severity, "summary": f.summary}
                          for f in result.risk_scan.findings]}
            if result.risk_scan else None
        ),
    }


def build_briefing_llm(result: MonitoringResult) -> MonitoringBriefing:
    """Write the grounded monitoring briefing from the deterministic facts."""
    from langchain_core.messages import HumanMessage, SystemMessage

    compact = _compact(result)
    system = SystemMessage(content=(
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
    ))
    user = HumanMessage(content=(
        f"Computed snapshot (authoritative):\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Write the briefing JSON now."
    ))

    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("briefing[llm]: failed (%s) — caller should use deterministic.", e)
        return MonitoringBriefing(method="llm", executive_summary="(LLM briefing unavailable.)")

    def _list(key: str) -> List[str]:
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
    from langchain_core.messages import HumanMessage, SystemMessage

    compact = _compact(result)
    system = SystemMessage(content=(
        "You answer questions about a completed portfolio monitoring run. Use ONLY the supplied "
        "figures (already correct and final). If the snapshot does not contain the answer, say "
        "so. Never invent a metric, a weight, an alert, or a different health band."
    ))
    user = HumanMessage(content=(
        f"Question:\n{question}\n\nSnapshot:\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Answer concisely."
    ))
    return get_llm().invoke([system, user]).content

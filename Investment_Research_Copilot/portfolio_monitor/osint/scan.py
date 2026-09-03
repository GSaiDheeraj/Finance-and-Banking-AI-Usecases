"""
Grounded emerging-risk synthesis.

The LLM does *language* work only: read the collected public-news snippets and classify them
into `EmergingRiskFinding`s (category + severity + one-line summary) and an overall risk tone.
It is told to use only the supplied evidence and not to invent events. The downstream alert
and scoring layers apply a credibility discount, so this scan colours the picture but never
overrides a hard limit breach.

If the LLM is unavailable, a deterministic keyword fallback classifies the evidence so the
scan still returns something useful offline-of-the-gateway.
"""
from __future__ import annotations

import json
from typing import List, Optional

from ..config import get_llm, llm_available
from ..logconf import get_logger
from typing import Dict  # noqa: F811  (re-imported to keep this module self-contained)

from ..schemas import (
    AllocationBreakdown,
    EmergingRiskFinding,
    MarketRiskScan,
    NewsEvidenceItem,
    RiskDimension,
)
from .collect import collect_evidence

_log = get_logger()


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def run_risk_scan(
    allocation: AllocationBreakdown,
    symbol_to_name: Optional[Dict[str, str]] = None,
    region: Optional[str] = None,
) -> MarketRiskScan:
    """Collect public news around the book and synthesise a grounded emerging-risk scan.

    Pass `symbol_to_name` (e.g. {'RELIANCE.NS': 'Reliance Industries'}) so the news
    queries use company names rather than exchange-suffixed tickers, which return far
    better results from DuckDuckGo.
    """
    evidence = collect_evidence(allocation, symbol_to_name=symbol_to_name, region=region)
    if not evidence:
        return MarketRiskScan(
            overall_risk_tone="normal", summary="No public-news evidence collected.",
            data_gaps=["News search unavailable or returned nothing."])

    if llm_available():
        scan = _synthesise_llm(evidence)
        if scan is not None:
            scan.evidence = evidence
            return scan

    return _synthesise_keyword(evidence)


# --------------------------------------------------------------------------- #
def _synthesise_llm(evidence: List[NewsEvidenceItem]) -> Optional[MarketRiskScan]:
    from langchain_core.messages import HumanMessage, SystemMessage

    compact = [{
        "dimension": e.dimension.value, "related_symbol": e.related_symbol,
        "title": e.title, "snippet": (e.snippet or "")[:300], "url": e.url,
    } for e in evidence]
    dims = [d.value for d in RiskDimension]

    system = SystemMessage(content=(
        "You are a risk analyst reading recent public-news snippets about a portfolio's "
        "holdings, sectors and the macro backdrop. From the SNIPPETS ONLY, identify genuine "
        "emerging risks. For each, give: scope ('portfolio'|'position'|'sector'), subject "
        "(ticker / sector / 'market'), category (one of " + str(dims) + "), severity "
        "('low'|'medium'|'high'), a one-line summary, and the source url. Then give an overall "
        "risk tone: 'calm'|'normal'|'elevated'|'stressed'. Do NOT invent events not in the "
        "snippets; if the news is benign, return few/no findings and a 'normal' tone. Output "
        "ONLY JSON:\n"
        '{"overall_risk_tone": str, "summary": str, "findings": [{"scope": str, "subject": str, '
        '"category": str, "severity": str, "summary": str, "url": str}], '
        '"watchlist_symbols": [str]}'
    ))
    user = HumanMessage(content=f"News snippets:\n{json.dumps(compact, indent=2)}\n\nReturn JSON.")

    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("news: LLM synthesis failed (%s) — keyword fallback.", e)
        return None

    findings: List[EmergingRiskFinding] = []
    for f in (parsed.get("findings") or []):
        try:
            findings.append(EmergingRiskFinding(
                scope=str(f.get("scope", "portfolio")),
                subject=str(f.get("subject", "market")),
                category=_dim(f.get("category")),
                severity=str(f.get("severity", "medium")).lower(),
                summary=str(f.get("summary", ""))[:240],
                url=f.get("url"),
            ))
        except Exception:
            continue
    return MarketRiskScan(
        overall_risk_tone=str(parsed.get("overall_risk_tone", "normal")).lower(),
        summary=str(parsed.get("summary", ""))[:600],
        findings=findings,
        watchlist_symbols=[str(s).upper() for s in (parsed.get("watchlist_symbols") or [])],
    )


_RISK_WORDS = ("lawsuit", "probe", "investigation", "fine", "downgrade", "cut", "selloff",
               "plunge", "warning", "recession", "default", "sanction", "tariff", "conflict",
               "crash", "fraud", "bankrupt", "layoff")


def _synthesise_keyword(evidence: List[NewsEvidenceItem]) -> MarketRiskScan:
    """Deterministic fallback: flag snippets containing risk keywords."""
    findings: List[EmergingRiskFinding] = []
    hits = 0
    for e in evidence:
        text = f"{e.title or ''} {e.snippet or ''}".lower()
        n = sum(1 for w in _RISK_WORDS if w in text)
        if n >= 1:
            hits += 1
            findings.append(EmergingRiskFinding(
                scope="position" if e.related_symbol else "sector" if e.dimension == RiskDimension.SECTOR else "portfolio",
                subject=e.related_symbol or "market",
                category=e.dimension,
                severity="high" if n >= 2 else "medium",
                summary=(e.title or e.snippet or "")[:200],
                url=e.url,
            ))
    tone = "elevated" if hits >= 3 else "normal"
    return MarketRiskScan(
        overall_risk_tone=tone,
        summary=f"Keyword-based scan (LLM unavailable): {hits} item(s) flagged risk language.",
        findings=findings[:12], evidence=evidence,
        data_gaps=["Classified by keywords, not the LLM."],
    )


def _dim(value) -> RiskDimension:
    try:
        return RiskDimension(str(value))
    except (ValueError, TypeError):
        return RiskDimension.MARKET

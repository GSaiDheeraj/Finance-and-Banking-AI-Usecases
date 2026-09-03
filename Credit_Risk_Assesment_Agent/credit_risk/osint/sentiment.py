"""
Grounded web-sentiment synthesis.

The collector gathers public web/news results; the LLM does *language* work only — it
reads the collected snippets, judges whether they are actually about THIS company
(disambiguation), classifies each negative item, and writes an overall sentiment read.
It is told to use ONLY the supplied evidence and to list what it could not find as a
gap rather than guessing.

The result feeds the SAME deterministic scorecard via the ADVERSE_MEDIA and
MARKET_SENTIMENT factors — the *severity* and *score contribution* of these findings is
decided by Python (`scoring.py`), never by the model.
"""
from __future__ import annotations

from typing import List

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_llm
from ..llm_json import safe_json_loads
from ..logconf import get_logger
from ..schemas import (
    CompanySeed,
    WebAdverseFinding,
    WebEvidenceItem,
    WebSentimentResult,
)
from .collect import collect_evidence

_log = get_logger()


def _empty_result(seed: CompanySeed, evidence: List[WebEvidenceItem], reason: str) -> WebSentimentResult:
    """The neutral fallback used both when there's no evidence and when synthesis fails."""
    return WebSentimentResult(
        seed=seed, disambiguation_confidence="low", overall_sentiment="neutral",
        sentiment_summary=reason, evidence=evidence, data_gaps=[reason],
    )


def _evidence_block(evidence: List[WebEvidenceItem]) -> str:
    blocks = []
    for i, e in enumerate(evidence, 1):
        blocks.append(
            f"[{i}] ({e.dimension.value}/{e.source}) {e.title or ''}\n"
            f"    url: {e.url or '—'}\n"
            f"    {e.snippet or ''}"
        )
    return "\n".join(blocks)


def synthesize_sentiment(seed: CompanySeed, evidence: List[WebEvidenceItem]) -> WebSentimentResult:
    """Turn collected web evidence into a grounded WebSentimentResult."""
    if not evidence:
        return _empty_result(
            seed, evidence, "No public web/news results were retrieved for this company."
        )

    block = _evidence_block(evidence)
    system = SystemMessage(content=(
        "You are a credit analyst reviewing the PUBLIC web/news footprint of a company that "
        "has applied for credit. Using ONLY the numbered evidence provided, do four things:\n"
        "1. Judge `disambiguation_confidence` (low/medium/high) that the results are about "
        "THIS company (watch for same-name companies in other sectors/countries).\n"
        "2. Give an `overall_sentiment` (positive|neutral|mixed|negative) and a short "
        "`sentiment_summary` (<= 3 sentences) of how the company is presented publicly.\n"
        "3. Extract `adverse_findings` — concrete negatives only (fraud, litigation, "
        "regulatory action, default/restructuring, distress, governance). For each give a "
        "category, a one-line summary, a severity (low/medium/high) and the source url.\n"
        "4. List a few `positive_highlights` and any `data_gaps` (what you could not find).\n"
        "Do NOT invent facts, financial figures, or a credit rating. Quote only what the "
        "evidence supports. Output ONLY a JSON object."
    ))
    schema_hint = (
        "{\n"
        '  "disambiguation_confidence": "low"|"medium"|"high",\n'
        '  "overall_sentiment": "positive"|"neutral"|"mixed"|"negative",\n'
        '  "sentiment_summary": str,\n'
        '  "adverse_findings": [{"category": str, "summary": str, '
        '"severity": "low"|"medium"|"high", "url": str|null}],\n'
        '  "positive_highlights": [str],\n'
        '  "data_gaps": [str]\n'
        "}"
    )
    user = HumanMessage(content=(
        f"Company: {seed.company_name}"
        + (f" | country: {seed.country}" if seed.country else "")
        + (f" | industry: {seed.industry}" if seed.industry else "")
        + f"\n\nEvidence:\n{block}\n\nReturn JSON matching:\n{schema_hint}"
    ))

    try:
        raw = get_llm().invoke([system, user]).content
    except Exception as exc:
        _log.warning(
            "web-sentiment synthesis failed (LLM gateway error): %s: %s — "
            "proceeding without it", type(exc).__name__, exc,
        )
        return _empty_result(
            seed, evidence,
            "Web-sentiment synthesis failed (LLM gateway error); proceeding without it.",
        )

    data = safe_json_loads(raw)
    if not isinstance(data, dict):
        data = {}

    findings: List[WebAdverseFinding] = []
    for row in data.get("adverse_findings") or []:
        if not isinstance(row, dict) or not row.get("summary"):
            continue
        sev = str(row.get("severity") or "medium").lower()
        if sev not in ("low", "medium", "high"):
            sev = "medium"
        findings.append(WebAdverseFinding(
            category=str(row.get("category") or "other").strip(),
            summary=str(row["summary"])[:300],
            severity=sev,
            url=row.get("url"),
        ))

    citations = [e.url for e in evidence if e.url][:25]
    return WebSentimentResult(
        seed=seed,
        disambiguation_confidence=str(data.get("disambiguation_confidence") or "low").lower(),
        overall_sentiment=str(data.get("overall_sentiment") or "neutral").lower(),
        sentiment_summary=str(data.get("sentiment_summary") or ""),
        adverse_findings=findings,
        positive_highlights=[str(x) for x in (data.get("positive_highlights") or []) if x],
        evidence=evidence,
        citations=citations,
        data_gaps=[str(x) for x in (data.get("data_gaps") or []) if x],
    )


def run_web_sentiment(seed: CompanySeed) -> WebSentimentResult:
    """Collect free public web evidence for the company and synthesize a grounded read."""
    evidence = collect_evidence(seed)
    return synthesize_sentiment(seed, evidence)

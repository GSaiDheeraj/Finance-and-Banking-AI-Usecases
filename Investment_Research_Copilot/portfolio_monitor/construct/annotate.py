"""
Per-stock pros & cons for a constructed portfolio.

For each SUGGESTED security (e.g. AAPL) this produces the pros and cons of holding *that*
name — incorporating its own stats (expected return, volatility, dividend, market cap,
country) AND the **geopolitical / sector sentiment** surfaced by the emerging-risk news scan
for its symbol or sector. This is genuine qualitative judgement, so the LLM does it (language
only, grounded in the supplied stats + news snippets); a deterministic fallback runs when the
LLM is unavailable, so every position still carries pros/cons.

The pros/cons are *annotations* — they never change the selection or the weights (those are
deterministic / guardrailed). They explain the holdings.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from ..config import get_llm, llm_available
from ..logconf import get_logger
from ..schemas import (
    AssetClass,
    EmergingRiskFinding,
    MarketRiskScan,
    SelectedPosition,
)

_log = get_logger()


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _news_for(position: SelectedPosition, scan: Optional[MarketRiskScan]) -> List[EmergingRiskFinding]:
    if not scan:
        return []
    sym = position.symbol.upper()
    sec = (position.sector or "").lower()
    out = []
    for f in scan.findings:
        subj = (f.subject or "").lower()
        if f.subject and f.subject.upper() == sym:
            out.append(f)
        elif sec and (subj in sec or sec in subj):
            out.append(f)
    return out


def annotate_positions(
    positions: List[SelectedPosition],
    scan: Optional[MarketRiskScan] = None,
    use_llm: bool = True,
) -> List[SelectedPosition]:
    """Fill pros/cons on each position (in place).

    With `use_llm` (and a configured LLM) the analysis is the grounded LLM read; otherwise a
    deterministic fallback runs, so every position carries pros/cons in any mode.
    """
    if not positions:
        return positions
    news_map = {p.symbol: _news_for(p, scan) for p in positions}
    if use_llm and llm_available():
        if _annotate_llm(positions, news_map):
            return positions
    _annotate_deterministic(positions, news_map)
    return positions


# --------------------------------------------------------------------------- #
def _annotate_llm(positions: List[SelectedPosition], news_map: Dict[str, list]) -> bool:
    from langchain_core.messages import HumanMessage, SystemMessage

    payload = []
    for p in positions:
        payload.append({
            "symbol": p.symbol, "name": p.name, "asset_class": p.asset_class.value,
            "sector": p.sector, "country": p.country, "market_cap": p.market_cap.value,
            "expected_return": (round(p.expected_return, 4) if p.expected_return is not None else None),
            "weight": round(p.target_weight, 4),
            "news": [{"category": f.category.value, "severity": f.severity, "summary": f.summary}
                     for f in news_map.get(p.symbol, [])],
        })
    system = SystemMessage(content=(
        "You are an equity analyst. For EACH holding, give 2–3 concise PROS and 2–3 concise "
        "CONS of holding that specific security, grounded ONLY in the supplied stats and the "
        "supplied news items. Explicitly weave in **sector/domain geopolitical events and "
        "sentiment** from the `news` field where present (e.g. regulation, tariffs, supply "
        "chain, sanctions). Do not invent facts or numbers not implied by the inputs. Output "
        "ONLY JSON: {\"<symbol>\": {\"pros\": [str], \"cons\": [str]}, ...}"
    ))
    user = HumanMessage(content=f"Holdings:\n{json.dumps(payload, indent=2)}\n\nReturn the JSON.")
    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("annotate[llm]: failed (%s) — deterministic fallback.", e)
        return False
    if not isinstance(parsed, dict):
        return False
    for p in positions:
        entry = parsed.get(p.symbol) or parsed.get(p.symbol.upper()) or {}
        p.pros = [str(x) for x in (entry.get("pros") or [])][:4]
        p.cons = [str(x) for x in (entry.get("cons") or [])][:4]
    _log.info("annotate[llm]: annotated %d position(s)", len(positions))
    return True


def _annotate_deterministic(positions: List[SelectedPosition], news_map: Dict[str, list]) -> None:
    for p in positions:
        pros: List[str] = []
        cons: List[str] = []
        if p.expected_return is not None:
            (pros if p.expected_return >= 0.08 else cons).append(
                f"Historical expected return ~{p.expected_return:.1%} p.a.")
        if p.market_cap.value == "large_cap":
            pros.append("Large-cap: typically more liquid and resilient.")
        elif p.market_cap.value == "small_cap":
            cons.append("Small-cap: higher volatility and liquidity risk.")
        if p.country:
            pros.append(f"Adds {p.country} exposure for geographic diversification.")
        # geopolitical / sector sentiment from the news scan
        for f in news_map.get(p.symbol, []):
            cons.append(f"News risk ({f.category.value}): {f.summary[:120]}")
        if not pros:
            pros.append("Fits the requested style / sleeve.")
        if not cons:
            cons.append("Standard market and sector risk applies; no specific adverse news found.")
        p.pros = pros[:4]
        p.cons = cons[:4]
    _log.info("annotate[deterministic]: annotated %d position(s)", len(positions))

"""
LangGraph node functions for the country-research subgraph — one per analysis topic.

Four of the six (geopolitical, sectoral, bond risk, market risk) are risk-severity reads and
share one classify-with-fallback implementation (`_classify_findings`): build queries, collect
free web/news snippets, classify with the LLM (grounded, "from the snippets only" — the same
template as `osint/scan.py::_synthesise_llm`), and fall back to a deterministic keyword
heuristic if the LLM is unavailable or the call fails. The other two are opportunity/
informational reads, not risk findings: bank FD/deposit rates (`analyze_fd_rates` asks for an
approximate rate number), and growth outlook (`analyze_growth` asks for a growth-outlook
bucket + growing sectors) — the bond-risk node additionally asks for a rough credit-rating
bucket (also not a severity).

Every node fails soft — an exception here never raises past this module; it returns a neutral/
empty partial state update (a `data_gaps` note) instead, exactly like every other network-
dependent piece of this codebase.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from ..config import get_llm, llm_available
from ..logconf import get_logger
from ..schemas import EmergingRiskFinding, NewsEvidenceItem, RiskDimension
from . import collect

_log = get_logger()

_RISK_WORDS = ("lawsuit", "probe", "investigation", "fine", "downgrade", "cut", "selloff",
               "plunge", "warning", "recession", "default", "sanction", "tariff", "conflict",
               "crash", "fraud", "bankrupt", "layoff", "coup", "unrest")

_GROWTH_WORDS = ("growth", "expansion", "boom", "rally", "upturn", "investment",
                 "infrastructure", "innovation", "emerging", "rising", "surge", "record high")


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _classify_keyword(
    dimension: RiskDimension, evidence: List[NewsEvidenceItem],
) -> List[EmergingRiskFinding]:
    findings: List[EmergingRiskFinding] = []
    for e in evidence:
        text = f"{e.title or ''} {e.snippet or ''}".lower()
        n = sum(1 for w in _RISK_WORDS if w in text)
        if n >= 1:
            findings.append(EmergingRiskFinding(
                scope="portfolio", subject=e.related_symbol or "country",
                category=dimension, severity="high" if n >= 2 else "medium",
                summary=(e.title or e.snippet or "")[:200], url=e.url,
            ))
    return findings[:8]


def _classify_llm(
    country: str, topic: str, dimension: RiskDimension, evidence: List[NewsEvidenceItem],
) -> Optional[List[EmergingRiskFinding]]:
    from langchain_core.messages import HumanMessage, SystemMessage

    compact = [{"title": e.title, "snippet": (e.snippet or "")[:300], "url": e.url}
               for e in evidence]
    system = SystemMessage(content=(
        f"You are a {topic} analyst reading recent public snippets about {country}. From the "
        "SNIPPETS ONLY, identify genuine findings. For each, give severity "
        "('low'|'medium'|'high') and a one-line summary. Do NOT invent events not in the "
        "snippets; if the news is benign, return few/no findings. Output ONLY JSON:\n"
        '{"findings": [{"severity": str, "summary": str, "url": str}]}'
    ))
    user = HumanMessage(content=f"Snippets:\n{json.dumps(compact, indent=2)}\n\nReturn JSON.")
    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("research[%s]: LLM classify failed for %s (%s).", topic, country, e)
        return None

    out: List[EmergingRiskFinding] = []
    for item in (parsed.get("findings") or [])[:8]:
        try:
            out.append(EmergingRiskFinding(
                scope="portfolio", subject=country, category=dimension,
                severity=str(item.get("severity", "medium")).lower(),
                summary=str(item.get("summary", ""))[:240],
                url=item.get("url"),
            ))
        except Exception:
            continue
    return out


def _classify_findings(country: str, topic: str, dimension: RiskDimension,
                       evidence: List[NewsEvidenceItem]) -> dict:
    if not evidence:
        return {"data_gaps": [f"No public evidence found for {topic} in {country}."]}
    if llm_available():
        found = _classify_llm(country, topic, dimension, evidence)
        if found is not None:
            return {"findings": found}
    return {"findings": _classify_keyword(dimension, evidence)}


def analyze_geopolitical(state: dict) -> dict:
    country = state["country"]
    try:
        evidence = collect.collect_geopolitical(country)
    except Exception as e:
        _log.warning("research[geopolitical]: collection failed for %s (%s).", country, e)
        return {"data_gaps": [f"Geopolitical search failed for {country}."]}
    return _classify_findings(country, "geopolitical risk", RiskDimension.GEOPOLITICAL, evidence)


def analyze_sectoral(state: dict) -> dict:
    country = state["country"]
    try:
        evidence = collect.collect_sectoral(country)
    except Exception as e:
        _log.warning("research[sectoral]: collection failed for %s (%s).", country, e)
        return {"data_gaps": [f"Sectoral search failed for {country}."]}
    return _classify_findings(country, "equity-sector outlook", RiskDimension.SECTOR, evidence)


def analyze_market_risk(state: dict) -> dict:
    country = state["country"]
    try:
        evidence = collect.collect_market_risk(country)
    except Exception as e:
        _log.warning("research[market]: collection failed for %s (%s).", country, e)
        return {"data_gaps": [f"Market-risk search failed for {country}."]}
    return _classify_findings(country, "equity market risk", RiskDimension.MARKET, evidence)


def _classify_credit_rating(country: str, evidence: List[NewsEvidenceItem]) -> Tuple[Optional[str], str]:
    if not evidence or not llm_available():
        return None, ""
    from langchain_core.messages import HumanMessage, SystemMessage

    compact = [{"title": e.title, "snippet": (e.snippet or "")[:300]} for e in evidence]
    system = SystemMessage(content=(
        f"You are a fixed-income analyst reading public snippets about {country}'s sovereign/"
        "aggregate bond credit quality. From the SNIPPETS ONLY, estimate an approximate credit "
        "rating BUCKET — one of AAA, AA, A, BBB, BB, B, CCC, or 'unknown' if the snippets don't "
        "say. This is a rough proxy, not a securities-grade rating — say 'unknown' rather than "
        'guess if unsupported. Output ONLY JSON: {"rating": str, "basis": str}'
    ))
    user = HumanMessage(content=f"Snippets:\n{json.dumps(compact, indent=2)}\n\nReturn JSON.")
    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
        rating = str(parsed.get("rating", "")).strip().upper()
        if rating in ("", "UNKNOWN", "N/A"):
            return None, ""
        return rating, str(parsed.get("basis", ""))[:200]
    except Exception as e:
        _log.warning("research[bond]: credit-rating classify failed for %s (%s).", country, e)
        return None, ""


def analyze_bond_risk(state: dict) -> dict:
    country = state["country"]
    try:
        evidence = collect.collect_bond_risk(country)
    except Exception as e:
        _log.warning("research[bond]: collection failed for %s (%s).", country, e)
        return {"data_gaps": [f"Bond-risk search failed for {country}."]}
    out = _classify_findings(country, "sovereign/aggregate bond risk",
                             RiskDimension.FIXED_INCOME, evidence)
    rating, basis = _classify_credit_rating(country, evidence)
    out["bond_credit_rating"] = rating
    out["bond_credit_rating_basis"] = basis
    return out


def analyze_fd_rates(state: dict) -> dict:
    country = state["country"]
    try:
        evidence = collect.collect_fd_rates(country)
    except Exception as e:
        _log.warning("research[fd_rates]: collection failed for %s (%s).", country, e)
        return {"data_gaps": [f"FD-rate search failed for {country}."]}
    if not evidence:
        return {"data_gaps": [f"No public FD-rate evidence found for {country}."]}
    if not llm_available():
        return {"data_gaps": [f"LLM unavailable — FD rate for {country} not estimated."]}

    from langchain_core.messages import HumanMessage, SystemMessage

    compact = [{"title": e.title, "snippet": (e.snippet or "")[:300], "url": e.url}
               for e in evidence]
    system = SystemMessage(content=(
        f"You read public snippets about bank fixed-deposit / savings rates in {country}. "
        "From the SNIPPETS ONLY, estimate the approximate current 1-year retail FD/deposit "
        "rate as a percentage (e.g. 7.0 for 7%). This is informational context, not a "
        "guaranteed rate. If the snippets don't support a number, return null. Output ONLY "
        'JSON: {"fd_rate_pct": number_or_null, "basis": str}'
    ))
    user = HumanMessage(content=f"Snippets:\n{json.dumps(compact, indent=2)}\n\nReturn JSON.")
    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
        rate = parsed.get("fd_rate_pct")
        rate_val = float(rate) if isinstance(rate, (int, float)) and 0 < rate < 50 else None
        result: Dict[str, object] = {"fd_rate_basis": str(parsed.get("basis", ""))[:200]}
        if rate_val is not None:
            result["fd_rate_pct"] = rate_val
        else:
            result["data_gaps"] = [f"Snippets didn't support an FD-rate estimate for {country}."]
        return result
    except Exception as e:
        _log.warning("research[fd_rates]: classify failed for %s (%s).", country, e)
        return {"data_gaps": [f"FD-rate classification failed for {country}."]}


def _classify_growth(country: str, evidence: List[NewsEvidenceItem]) -> dict:
    if not evidence:
        return {"data_gaps": [f"No public evidence found for growth outlook in {country}."]}
    if not llm_available():
        # Keyword heuristic can support a coarse tone but never specific sector names —
        # same "unknown rather than guess" ethic as _classify_credit_rating.
        n = sum(1 for e in evidence
                if any(w in f"{e.title or ''} {e.snippet or ''}".lower() for w in _GROWTH_WORDS))
        if n:
            return {"growth_outlook": "moderate"}
        return {"growth_outlook": "unknown",
                "data_gaps": [f"LLM unavailable — growth outlook for {country} not estimated."]}

    from langchain_core.messages import HumanMessage, SystemMessage

    compact = [{"title": e.title, "snippet": (e.snippet or "")[:300], "url": e.url}
               for e in evidence]
    system = SystemMessage(content=(
        f"You are an economic-growth analyst reading recent public snippets about {country}. "
        "From the SNIPPETS ONLY, estimate an overall growth-outlook BUCKET — one of 'low', "
        "'moderate', 'high', or 'unknown' if the snippets don't support a read — and list up "
        "to 6 sectors the snippets specifically call out as growing. Do NOT invent sectors not "
        "in the snippets; return an empty list rather than guess. Output ONLY JSON:\n"
        '{"growth_outlook": str, "growth_sectors": [str, ...]}'
    ))
    user = HumanMessage(content=f"Snippets:\n{json.dumps(compact, indent=2)}\n\nReturn JSON.")
    try:
        parsed = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("research[growth]: classify failed for %s (%s).", country, e)
        return {"data_gaps": [f"Growth-outlook classification failed for {country}."]}

    outlook = str(parsed.get("growth_outlook", "unknown")).strip().lower()
    if outlook not in ("low", "moderate", "high"):
        outlook = "unknown"
    sectors = [str(s)[:60] for s in (parsed.get("growth_sectors") or []) if str(s).strip()][:6]
    result: Dict[str, object] = {"growth_outlook": outlook}
    if sectors:
        result["growth_sectors"] = sectors
    if outlook == "unknown":
        result["data_gaps"] = [f"Snippets didn't support a growth-outlook read for {country}."]
    return result


def analyze_growth(state: dict) -> dict:
    country = state["country"]
    try:
        evidence = collect.collect_growth_outlook(country)
    except Exception as e:
        _log.warning("research[growth]: collection failed for %s (%s).", country, e)
        return {"data_gaps": [f"Growth-outlook search failed for {country}."]}
    return _classify_growth(country, evidence)

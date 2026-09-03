"""
LLM-augmented portfolio constructor (free selection, hard deterministic guardrails).

The LLM does the *judgement* a human advisor would: given the mandate, the candidate universe
WITH its deterministic stats (asset class, sector, volatility, dividend yield, recent news
tone) and the constraints, it proposes which securities to hold, their weights, and — crucially
— a REASON for each pick. It may tilt freely.

It is NOT trusted with the numbers: its proposal is passed straight through the same
deterministic `guardrails` as the rule-based constructor (single-name / sector caps,
exclusions, min size, renormalise), and the share quantities and expected risk metrics are
recomputed deterministically. Any change the guardrails make is recorded in
`guardrail_corrections` — so the comparison shows both the LLM's free choices and where the
rules overrode them. If the LLM/library is unavailable, this falls back to the deterministic
constructor and says so.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from ..config import get_llm, llm_available
from ..feasibility import assess_feasibility, implied_target_annual
from ..logconf import get_logger
from ..reference import target_allocation
from ..schemas import (
    ConstructedPortfolio,
    ConstructionRequest,
    MarketRiskScan,
)
from ..universe import Candidate
from . import common, guardrails
from .deterministic import construct_deterministic

_log = get_logger()


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _news_tone_by_symbol(scan: Optional[MarketRiskScan]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not scan:
        return out
    for f in scan.findings:
        if f.scope == "position" and f.subject:
            out[f.subject.upper()] = f.severity
    return out


def _universe_digest(universe: List[Candidate], scan: Optional[MarketRiskScan]) -> List[Dict]:
    tone = _news_tone_by_symbol(scan)
    digest = []
    for c in universe:
        digest.append({
            "symbol": c.symbol,
            "name": c.name,
            "asset_class": c.asset_class.value,
            "sector": c.sector,
            "region": c.region.value,
            "volatility": round(c.volatility, 4) if c.volatility is not None else None,
            "dividend_yield": round(c.dividend_yield, 4) if c.dividend_yield is not None else None,
            "has_price": c.has_market_data,
            "news_risk": tone.get(c.symbol.upper()),
        })
    return digest


def construct_llm(
    request: ConstructionRequest,
    universe: List[Candidate],
    risk_scan: Optional[MarketRiskScan] = None,
) -> ConstructedPortfolio:
    """Build the LLM-advised portfolio, then enforce hard constraints deterministically."""
    if not llm_available():
        _log.warning("construct[llm]: LLM not configured — falling back to deterministic.")
        cp = construct_deterministic(request, universe)
        cp.method = "llm"
        cp.narrative = ("LLM not configured (set LLM_ENDPOINT / LLM_API_KEY). "
                        "Fell back to the deterministic construction. ") + cp.narrative
        cp.guardrail_corrections.insert(0, "LLM unavailable — deterministic fallback used.")
        return cp

    saa = target_allocation(request.mandate)
    saa_str = ", ".join(f"{k} {v:.0%}" for k, v in saa.items() if v > 0)
    digest = _universe_digest(universe, risk_scan)
    cons = request.constraints

    # investor-profile context for the model
    profile_bits = [f"style: {request.style.value}"]
    if request.countries:
        profile_bits.append(f"countries: {', '.join(request.countries)} "
                            f"({request.geography_mode.value})")
    if request.equity_pct is not None or request.debt_pct is not None:
        profile_bits.append(f"equity/debt split: {request.equity_pct or '—'}/{request.debt_pct or '—'}")
    if request.cap_mix:
        profile_bits.append("cap mix: " + ", ".join(f"{k} {v:.0%}" for k, v in request.cap_mix.items()))
    if request.theme:
        profile_bits.append(f"theme: {request.theme}")
    if request.instrument_types:
        profile_bits.append("instruments: " + ", ".join(i.value for i in request.instrument_types))
    target, _basis = implied_target_annual(request)
    if target is not None:
        profile_bits.append(f"return target: ~{target:.1%} p.a.")
    profile_str = "; ".join(profile_bits)

    from langchain_core.messages import HumanMessage, SystemMessage

    system = SystemMessage(content=(
        "You are a portfolio manager constructing a portfolio from a candidate universe for a "
        f"{request.mandate} mandate (risk tolerance: {request.risk_tolerance}, horizon "
        f"{request.horizon_years}y).\n"
        f"INVESTOR PROFILE — respect it: {profile_str}.\n"
        "You may select any subset of the universe and set weights freely, expressing your "
        "investment judgement (diversification, quality, the supplied volatility, expected "
        "return and recent news-risk signals), but keep the asset-class mix and style aligned "
        "with the profile above.\n"
        "HARD RULES (also enforced after you): weights are fractions that sum to ~1.0; no single "
        f"security above {cons.single_name_cap:.0%}; no sector above {cons.sector_cap:.0%}; do "
        f"not pick excluded names {cons.exclusions}; keep the asset-class mix near {saa_str} "
        "unless the investor's equity/debt split overrides it.\n"
        "If the investor's return target looks unrealistic for this universe, say so in the "
        "narrative. For EACH selected security give a concise reason. Use ONLY the supplied "
        "candidates and their stats — do not invent tickers or numbers. Output ONLY JSON:\n"
        '{"positions": [{"symbol": str, "weight": number, "reason": str}], '
        '"narrative": str}'
    ))
    user = HumanMessage(content=(
        f"Investable amount: {request.invest_amount:,.0f} {request.base_currency}.\n"
        f"Candidate universe (with deterministic stats):\n{json.dumps(digest, indent=2)}\n\n"
        "Return your portfolio JSON now."
    ))

    try:
        raw = get_llm().invoke([system, user]).content
        parsed = json.loads(_strip_json(raw))
    except Exception as e:
        _log.warning("construct[llm]: LLM call/parse failed (%s) — deterministic fallback.", e)
        cp = construct_deterministic(request, universe)
        cp.method = "llm"
        cp.guardrail_corrections.insert(0, f"LLM call failed ({type(e).__name__}) — fallback.")
        return cp

    proposed: Dict[str, float] = {}
    rationale: Dict[str, str] = {}
    valid_syms = {c.symbol.upper() for c in universe}
    for p in (parsed.get("positions") or []):
        sym = str(p.get("symbol", "")).strip().upper()
        w = p.get("weight")
        if sym in valid_syms and isinstance(w, (int, float)) and w > 0:
            proposed[sym] = proposed.get(sym, 0.0) + float(w)
            rationale[sym] = str(p.get("reason", ""))[:300]
    llm_narrative = str(parsed.get("narrative", ""))[:1200]

    corrections: List[str] = []
    if not proposed:
        _log.warning("construct[llm]: LLM returned no usable positions — deterministic fallback.")
        cp = construct_deterministic(request, universe)
        cp.method = "llm"
        cp.guardrail_corrections.insert(0, "LLM returned no usable positions — fallback.")
        return cp

    # record what the LLM proposed before guardrails, for transparency
    n_proposed = len(proposed)
    meta: Dict[str, Candidate] = {c.symbol.upper(): c for c in universe}
    guardrailed, guard_corr = guardrails.enforce(proposed, meta, request.constraints)
    corrections.extend(guard_corr)

    positions, invested, cash = guardrails.finalise_positions(
        guardrailed, meta, request.invest_amount, rationale)
    metrics = common.compute_expected_metrics(positions, request.mandate, request.base_currency)
    feasibility = assess_feasibility(request, positions, [c.expected_return for c in universe])

    narrative = (
        f"LLM-advised construction for a {request.mandate} mandate ({request.style.value}). "
        f"The model proposed {n_proposed} position(s); after deterministic guardrails the book "
        f"holds {len(positions)} position(s). "
        + (llm_narrative or "")
        + (" " + feasibility.message if feasibility and feasibility.message else "")
    )
    _log.info("construct[llm]: proposed=%d final=%d invested=%.0f corrections=%d",
              n_proposed, len(positions), invested, len(corrections))

    return ConstructedPortfolio(
        method="llm", weighting_method=request.weighting_method,
        style=request.style, countries=request.countries,
        positions=positions,
        sleeve_allocation=common.sleeve_allocation(positions),
        cap_allocation=common.cap_allocation(positions),
        country_allocation=common.country_allocation(positions),
        expected_metrics=metrics, feasibility=feasibility,
        excluded=[], guardrail_corrections=corrections, narrative=narrative,
        total_invested=invested, cash_residual=cash,
    )

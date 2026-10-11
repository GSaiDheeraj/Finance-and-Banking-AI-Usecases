"""
OPCO-FINCO agents for LLM-led portfolio construction (WhiteOak-style framework).

Implements WhiteOak Capital's trademark OpcoFinco(TM) lens plus its two
investment pillars — four specialist agents plus an orchestrator:

  * OpCoAgent    — the capital-light operating company: excess return on
    invested capital (return after cost of capital) and scalable, asset-light
    economics. Favours steady compounders with superior return-per-unit-risk
    and payout discipline; penalises volatile, capital-hungry names.
  * FinCoAgent   — the financing company that owns the capital investments:
    capital discipline. Favours cash returned to holders (yield) and funded
    scale (large caps); penalises names where cash looks trapped in capex
    (no yield + high volatility + small scale).
  * BusinessAgent — the "great business" pillar: well managed, scalable, with
    superior returns on incremental capital — and none of the weaker traits
    WhiteOak avoids (poor governance, weak incremental returns, substitution
    or obsolescence risk). **Uses LIVE public search**: per-stock governance /
    disruption queries plus per-sector substitution / structural-change queries
    via free DuckDuckGo (`osint.search`).
  * ValueAgent   — the "attractive valuation" pillar: current price at a
    substantial discount to intrinsic value (PV of future cash flows, economic
    FCF not accounting multiples). Favours cheap P/E + yield with a margin of
    safety; avoids paying for speculative growth.

Design rule (same as the rest of this codebase): deterministic Python computes
every score; the LLM only writes language (per-agent views + per-position
O/F/B/V reasons). The same universe + request + search evidence always yields
the same scores (NFR-1). Search failures and a missing LLM both degrade to
deterministic keyword scoring with a templated reason — the run never breaks.
Set `enable_search=False` for a fully offline run.
"""

from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..config import get_llm, llm_available
from ..logconf import get_logger
from ..osint.search import news_search, search_available, web_search
from ..schemas import ConstructionRequest, InstrumentType, MarketCap
from ..universe import Candidate

_log = get_logger()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class OfScores(BaseModel):
    """Deterministic 0..1 scores per candidate (higher = more attractive)."""

    opco: float = 0.5
    finco: float = 0.5
    business: float = 0.5
    value: float = 0.5
    combined: float = 0.5
    stance: str = "selective"  # portfolio-level read: high_conviction|selective|cautious
    reasons: dict[str, str] = Field(default_factory=dict)  # O/F/B/V one-liners
    evidence_urls: list[str] = Field(default_factory=list)  # search links behind B


class OfAgentVerdict(BaseModel):
    """One agent's output: deterministic scores + LLM commentary per symbol."""

    agent: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    commentary: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class OfReport(BaseModel):
    """Orchestrator output consumed by the LLM constructor."""

    scores: dict[str, OfScores] = Field(default_factory=dict)
    stance: str = "selective"
    search_coverage: float = 0.0  # fraction of scored names with any search hit
    agent_views: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    data_gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic scorers (RULE — no LLM)
# --------------------------------------------------------------------------- #
# Capital-light, scalable operating models (asset-light compounding).
_ASSET_LIGHT = frozenset(
    {
        "information technology",
        "technology",
        "software",
        "financials",
        "financial services",
        "banks",
        "bank",
        "insurance",
        "fmcg",
        "consumer staples",
        "staples",
        "consumer discretionary",
        "pharma",
        "pharmaceuticals",
        "healthcare",
        "telecom",
    }
)
# Capital-heavy models where cash stays trapped in PPE/capex cycles.
_CAPITAL_HEAVY = frozenset(
    {
        "energy",
        "oil",
        "gas",
        "utilities",
        "power",
        "materials",
        "metals",
        "mining",
        "steel",
        "cement",
        "industrials",
        "capital goods",
        "infrastructure",
        "real estate",
        "realty",
        "telecom",
    }
)
# Sectors structurally exposed to substitution / obsolescence risk.
_SUBSTITUTION_RISK = frozenset(
    {
        "telecom",
        "media",
        "retail",
        "oil",
        "gas",
        "coal",
        "utilities",
    }
)


def _sector_kind(sector: str | None) -> str:
    s = (sector or "").strip().lower()
    if any(k in s for k in _CAPITAL_HEAVY):
        return "heavy"
    if any(k in s for k in _ASSET_LIGHT):
        return "light"
    return "neutral"


def opco_score(c: Candidate) -> tuple[float, str]:
    """Capital-light operating quality: excess-return-per-risk + payout + lightness.
    Missing data -> neutral 0.5 legs."""
    exp_ret = c.expected_return if c.expected_return is not None else 0.0
    vol = c.volatility if c.volatility and c.volatility > 0 else 0.25
    # Excess-return proxy: return per unit of risk, mapped roughly [-0.5, +1.0] -> [0, 1].
    excess = (
        max(0.0, min(1.0, (exp_ret / vol + 0.5) / 1.5)) if c.expected_return is not None else 0.5
    )
    payout = 0.5
    if c.dividend_yield is not None:
        payout = max(0.0, min(1.0, c.dividend_yield / 0.04))
    kind = _sector_kind(c.sector)
    lightness = {"light": 0.75, "neutral": 0.55, "heavy": 0.35}[kind]
    score = max(0.05, min(0.95, 0.45 * excess + 0.25 * payout + 0.30 * lightness))
    reason = (
        f"O: excess-return proxy {excess:.2f} (drift {exp_ret:.1%} @ vol {vol:.0%}), "
        f"yield {(c.dividend_yield or 0):.1%}, {kind} model"
    )
    return round(score, 4), reason


def finco_score(c: Candidate) -> tuple[float, str]:
    """Capital discipline: cash returned (yield) + funded scale; penalises
    small, volatile, zero-yield names where cash looks trapped in capex."""
    pile = 0.5
    if c.dividend_yield is not None:
        pile += (max(0.0, min(1.0, c.dividend_yield / 0.04)) - 0.5) * 0.5
    vol = c.volatility if c.volatility is not None else 0.25
    calm = max(0.0, min(1.0, (0.50 - vol) / 0.40))
    pile += (calm - 0.5) * 0.3
    scale = {
        MarketCap.LARGE_CAP: 0.15,
        MarketCap.MID_CAP: 0.05,
        MarketCap.SMALL_CAP: -0.10,
        MarketCap.UNKNOWN: -0.05,
    }.get(c.market_cap, -0.05)
    if c.instrument_type in (InstrumentType.ETF, InstrumentType.MUTUAL_FUND):
        scale = 0.10  # diversified vehicles are funded by construction
    if not c.has_market_data:
        scale -= 0.15
    score = max(0.05, min(0.95, pile + scale))
    reason = f"F: yield {(c.dividend_yield or 0):.1%}, calm {calm:.2f}, {c.market_cap.value} scale"
    return round(score, 4), reason


_GOVERNANCE_BAD = (
    "fraud",
    "probe",
    "investigation",
    "lawsuit",
    "fine",
    "penalty",
    "ban",
    "default",
    "governance",
    "scam",
    "arrest",
    "raid",
)
_OBSOLESCENCE_WORDS = (
    "disruption",
    "obsolete",
    "obsolescence",
    "substitution",
    "displacement",
    "structural decline",
    "sunset",
    "phase-out",
    "phaseout",
)
_POSITIVE_BIZ = (
    "market share gain",
    "expanding",
    "record profit",
    "upgrade",
    "outperform",
    "moat",
    "brand strength",
    "order win",
    "contract win",
)


def business_from_evidence(
    c: Candidate,
    items: list[dict],
    news_tone: str | None = None,
) -> tuple[float, str, list[str]]:
    """Great-business screen: managed + scalable + superior incremental returns,
    minus governance / obsolescence flags mined from live search evidence."""
    exp_ret = c.expected_return if c.expected_return is not None else 0.0
    vol = c.volatility if c.volatility and c.volatility > 0 else 0.25
    incremental = (
        max(0.0, min(1.0, (exp_ret / vol + 0.5) / 1.5)) if c.expected_return is not None else 0.5
    )
    s = (c.sector or "").strip().lower()
    scalable = 0.70 if any(k in s for k in _ASSET_LIGHT) else 0.50
    base = 0.5 * incremental + 0.5 * scalable

    gov_hits = obs_hits = pos_hits = 0
    urls: list[str] = []
    for it in items:
        text = f"{it.get('title') or ''} {it.get('snippet') or ''}".lower()
        gov_hits += sum(1 for w in _GOVERNANCE_BAD if w in text)
        obs_hits += sum(1 for w in _OBSOLESCENCE_WORDS if w in text)
        pos_hits += sum(1 for w in _POSITIVE_BIZ if w in text)
        if it.get("url"):
            urls.append(it["url"])
    discount = min(0.40, 0.12 * gov_hits + 0.10 * obs_hits)
    uplift = min(0.10, 0.03 * pos_hits)
    if news_tone == "high":
        discount += 0.10
    elif news_tone == "medium":
        discount += 0.05
    # Structural substitution-risk sectors start with a small haircut.
    if any(k in s for k in _SUBSTITUTION_RISK):
        discount += 0.05
    score = max(0.05, min(0.95, base - discount + uplift))
    bits = [f"incremental {incremental:.2f}", f"scalable {scalable:.2f}"]
    if items:
        bits.append(f"search {len(items)} hit(s): gov-{gov_hits}/obs-{obs_hits}/pos-{pos_hits}")
    else:
        bits.append("no search hits — fundamentals only")
    return round(score, 4), "B: " + ", ".join(bits), urls[:4]


def value_score(c: Candidate) -> tuple[float, str]:
    """Discount to intrinsic value: cheap P/E + yield, with a margin-of-safety
    demand — no paying for speculative growth. Missing data -> neutral."""
    bits: list[str] = []
    score, adj = 0.5, 0.0
    if c.pe_ratio is not None and c.pe_ratio > 0:
        # Substantial discount demanded: <=12 very cheap, >=35 fully priced.
        pe_part = max(0.0, min(1.0, (35.0 - c.pe_ratio) / (35.0 - 12.0)))
        adj += (pe_part - 0.5) * 0.7
        bits.append(f"P/E {c.pe_ratio:.1f}")
    if c.dividend_yield is not None:
        y_part = max(0.0, min(1.0, c.dividend_yield / 0.04))
        adj += (y_part - 0.5) * 0.3
        bits.append(f"yield {c.dividend_yield:.1%}")
    score = max(0.05, min(0.95, score + adj))
    return round(score, 4), "V: " + (", ".join(bits) if bits else "no P/E/yield data — neutral")


_OF_WEIGHTS = {"opco": 0.20, "finco": 0.15, "business": 0.35, "value": 0.30}


def combined_score(o: float, f: float, b: float, v: float) -> float:
    return round(
        o * _OF_WEIGHTS["opco"]
        + f * _OF_WEIGHTS["finco"]
        + b * _OF_WEIGHTS["business"]
        + v * _OF_WEIGHTS["value"],
        4,
    )


# --------------------------------------------------------------------------- #
# Live evidence: governance / disruption search (stock + sector — REQUIRED input)
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _search_name(symbol: str, symbol_to_name: dict[str, str]) -> str:
    """Company name for search (DuckDuckGo understands names, not tickers)."""
    name = (symbol_to_name.get(symbol.upper(), "") or "").strip()
    if name:
        return name
    base = symbol.strip()
    for suf in (".NS", ".BO", ".L", ".T", ".DE", ".AX", ".HK"):
        if base.upper().endswith(suf):
            base = base[: -len(suf)]
    return base


def collect_opcofinco_evidence(
    universe: list[Candidate],
    symbol_to_name: dict[str, str] | None = None,
    max_names: int | None = None,
    max_results: int | None = None,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Collect governance/disruption hits per stock + substitution hits per sector.

    Returns (evidence_by_symbol, data_gaps). Fail-soft: missing `ddgs`,
    rate limits, or zero results yield empty lists + a gap note — never raise.
    """
    s2n = {k.upper(): v for k, v in (symbol_to_name or {}).items()}
    for c in universe:
        if c.symbol.upper() not in s2n and c.name:
            s2n[c.symbol.upper()] = c.name
    gaps: list[str] = []
    if not search_available():
        return {}, [
            "DuckDuckGo backend (ddgs) not installed — business screen is fundamentals-only."
        ]
    n_names = max_names or _env_int("PM_OF_MAX_NAMES", 15)
    n_res = max_results or _env_int("PM_OF_MAX_RESULTS", 3)

    ranked = sorted(universe, key=lambda c: (not c.has_market_data, c.symbol))[: max(1, n_names)]
    sectors = sorted(
        {
            (c.sector or "").strip()
            for c in ranked
            if (c.sector or "").strip() and (c.sector or "").strip().lower() != "unclassified"
        }
    )[:5]
    sector_hits: dict[str, list[dict]] = {}
    for sec in sectors:
        try:
            sector_hits[sec] = news_search(
                f"{sec} sector disruption substitution risk",
                max_results=n_res,
                fallback_query=f"{sec} sector outlook",
            ) + web_search(
                f"{sec} industry disruption obsolescence",
                max_results=2,
                fallback_query=f"{sec} disruption",
            )
        except Exception as e:  # fail-soft per sector
            _log.warning("opcofinco[business]: sector search failed for %s (%s).", sec, e)
            sector_hits[sec] = []

    out: dict[str, list[dict]] = {}
    for c in ranked:
        name = _search_name(c.symbol, s2n)
        try:
            stock_hits = news_search(
                f"{name} company governance fraud probe outlook",
                max_results=n_res,
                fallback_query=name,
            )
        except Exception as e:
            _log.warning("opcofinco[business]: stock search failed for %s (%s).", c.symbol, e)
            stock_hits = []
        sec_hits = sector_hits.get((c.sector or "").strip(), [])
        for h in stock_hits:
            h["level"] = "stock"
        for h in sec_hits:
            h.setdefault("level", "sector")
        out[c.symbol.upper()] = stock_hits + sec_hits
    covered = sum(1 for v in out.values() if v)
    _log.info(
        "opcofinco[business]: %d/%d names with search hits (%d sectors).",
        covered,
        len(out),
        len(sectors),
    )
    if covered == 0:
        gaps.append(
            "Search returned no hits for any scored name — business screen is fundamentals-only."
        )
    return out, gaps


# --------------------------------------------------------------------------- #
# Per-agent LLM commentary (language only — never changes a score)
# --------------------------------------------------------------------------- #
def _llm_commentary(
    agent: str,
    request: ConstructionRequest,
    digest: list[dict],
    instruction: str,
    stance: str,
) -> dict[str, str]:
    if not llm_available():
        return {}
    try:
        system = SystemMessage(
            content=(
                f"You are the {agent} agent in a WhiteOak-style OPCO-FINCO investment "
                f"committee for a {request.mandate} mandate (stance {stance}). {instruction} "
                'Reply with ONLY JSON: {"views": [{"symbol": str, "view": str}]}. '
                "One short line per symbol you have a view on; skip the rest."
            )
        )
        user = HumanMessage(content=json.dumps(digest[:60], indent=1))
        raw = get_llm().invoke([system, user]).content
        t = raw.strip()
        if t.startswith("```"):
            t = "\n".join(t.split("\n")[1:])
            if t.rstrip().endswith("```"):
                t = t.rstrip()[:-3]
        parsed = json.loads(t.strip())
        out: dict[str, str] = {}
        for v in parsed.get("views") or []:
            sym = str(v.get("symbol", "")).strip().upper()
            if sym:
                out[sym] = str(v.get("view", ""))[:200]
        return out
    except Exception as e:
        _log.warning("opcofinco[%s]: LLM view failed (%s) — deterministic only.", agent, e)
        return {}


def _mini_digest(
    universe: list[Candidate],
    evidence: dict[str, list[dict]],
    news_tones: dict[str, str],
) -> list[dict]:
    out = []
    for c in universe:
        items = evidence.get(c.symbol.upper(), [])
        out.append(
            {
                "symbol": c.symbol,
                "sector": c.sector,
                "cap": c.market_cap.value,
                "instrument": c.instrument_type.value,
                "vol": round(c.volatility, 4) if c.volatility is not None else None,
                "exp_ret": round(c.expected_return, 4) if c.expected_return is not None else None,
                "yield": round(c.dividend_yield, 4) if c.dividend_yield is not None else None,
                "pe": round(c.pe_ratio, 2) if c.pe_ratio is not None else None,
                "news": news_tones.get(c.symbol.upper()),
                "n_search_hits": len(items),
                "headlines": [(i.get("title") or "")[:120] for i in items[:4]],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_opcofinco(
    universe: list[Candidate],
    request: ConstructionRequest,
    news_tones: dict[str, str] | None = None,
    symbol_to_name: dict[str, str] | None = None,
    use_llm: bool = True,
    enable_search: bool = True,
    precollected: dict[str, list[dict]] | None = None,
) -> OfReport:
    """Run all four OPCO-FINCO agents and combine their deterministic scores.

    RULE: scores + stance are always deterministic. Live search evidence feeds
    the Business score (governance/obsolescence screen) and the LLM commentary;
    the LLM never changes a score. Set `enable_search=False` for a fully
    offline run.
    """
    tones = news_tones or {}
    evidence: dict[str, list[dict]] = precollected or {}
    gaps: list[str] = []
    if enable_search and not precollected:
        evidence, gaps = collect_opcofinco_evidence(universe, symbol_to_name)

    o_tbl = {c.symbol.upper(): opco_score(c) for c in universe}
    f_tbl = {c.symbol.upper(): finco_score(c) for c in universe}
    b_tbl: dict[str, tuple[float, str, list[str]]] = {}
    for c in universe:
        sym = c.symbol.upper()
        b_tbl[sym] = business_from_evidence(c, evidence.get(sym, []), tones.get(sym))
    v_tbl = {c.symbol.upper(): value_score(c) for c in universe}

    # Portfolio stance from average business quality: great businesses at scale
    # = high conviction bottom-up book; patchy = selective; weak = cautious.
    avg_b = sum(v[0] for v in b_tbl.values()) / max(1, len(b_tbl))
    stance = "high_conviction" if avg_b >= 0.60 else "selective" if avg_b >= 0.40 else "cautious"

    det_comment: dict[str, dict[str, str]] = {
        "opco": {s: v[1] for s, v in o_tbl.items()},
        "finco": {s: v[1] for s, v in f_tbl.items()},
        "business": {s: v[1] for s, v in b_tbl.items()},
        "value": {s: v[1] for s, v in v_tbl.items()},
    }
    llm_comment: dict[str, dict[str, str]] = {}
    if use_llm and llm_available():
        digest = _mini_digest(universe, evidence, tones)
        llm_comment["opco"] = _llm_commentary(
            "OpCo",
            request,
            digest,
            "Judge the operating company: capital-light scalable economics with "
            "superior return-per-risk win; capital-hungry volatile names lose.",
            stance,
        )
        llm_comment["finco"] = _llm_commentary(
            "FinCo",
            request,
            digest,
            "Judge capital discipline: cash returned as yield at funded scale wins; "
            "cash trapped in capex (no yield, small, volatile) loses.",
            stance,
        )
        # Business LLM sees the REAL governance/disruption search snippets.
        llm_comment["business"] = _llm_commentary(
            "Business",
            request,
            digest,
            "Judge great-business fit ONLY from the supplied stats and headlines: "
            "well managed, scalable, superior incremental returns win; "
            "governance flags or substitution/obsolescence risk lose. "
            "Do not invent news.",
            stance,
        )
        llm_comment["value"] = _llm_commentary(
            "Value",
            request,
            digest,
            "Judge discount to intrinsic value: cheap P/E + yield with margin of safety "
            "wins; fully-priced speculative growth loses.",
            stance,
        )

    def _view(agent: str, sym: str, fallback: str) -> str:
        return llm_comment.get(agent, {}).get(sym) or det_comment[agent].get(sym, fallback)

    combined: dict[str, OfScores] = {}
    for c in universe:
        sym = c.symbol.upper()
        o, f = o_tbl[sym][0], f_tbl[sym][0]
        b, v = b_tbl[sym][0], v_tbl[sym][0]
        reasons = {
            "opco": _view("opco", sym, o_tbl[sym][1]),
            "finco": _view("finco", sym, f_tbl[sym][1]),
            "business": _view("business", sym, b_tbl[sym][1]),
            "value": _view("value", sym, v_tbl[sym][1]),
        }
        combined[sym] = OfScores(
            opco=o,
            finco=f,
            business=b,
            value=v,
            combined=combined_score(o, f, b, v),
            stance=stance,
            reasons=reasons,
            evidence_urls=b_tbl[sym][2],
        )
    covered = sum(1 for c in universe if evidence.get(c.symbol.upper()))
    coverage = round(covered / max(1, len(universe)), 3)
    summary = (
        f"OPCO-FINCO committee: {len(combined)} names scored, stance `{stance}` "
        f"(avg business {avg_b:.2f}; search coverage {coverage:.0%}; weights "
        f"O{_OF_WEIGHTS['opco']:.0%}/F{_OF_WEIGHTS['finco']:.0%}/"
        f"B{_OF_WEIGHTS['business']:.0%}/V{_OF_WEIGHTS['value']:.0%})."
    )
    _log.info("opcofinco: %s", summary)
    views = {
        "opco": f"OpCo screen over {len(combined)} names (excess-return-per-risk + lightness).",
        "finco": "FinCo screen (yield + funded scale vs trapped cash).",
        "business": f"Great-business screen over {covered} searched name(s) + fundamentals.",
        "value": "Value screen (discount to intrinsic value, margin of safety).",
    }
    return OfReport(
        scores=combined,
        stance=stance,
        search_coverage=coverage,
        agent_views=views,
        summary=summary,
        data_gaps=gaps,
    )

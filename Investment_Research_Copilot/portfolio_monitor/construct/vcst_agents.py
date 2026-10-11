"""
VCST agents for LLM-led portfolio construction (ICICI-style framework).

Implements the ICICI Prudential VCTS checklist as four specialist agents —
one per dimension — plus an orchestrator:

  * ValuationAgent — price vs intrinsic value (P/E, yield, earnings proxy).
    Buy when cheap, sell when expensive.
  * CycleAgent     — business-cycle positioning (cyclical vs defensive sectors
    against the prevailing cycle stance). Contrarian: buy cyclicals when the
    cycle is weak, rotate to defensives when it is strong.
  * SentimentAgent — investor affinity from LIVE public search. **Search data is
    required here**: per stock (company-name queries) plus per industry/sector
    queries via free DuckDuckGo (`osint.search`). Buy on negative sentiment,
    sell on euphoria — the score reflects the evidence collected.
  * TriggerAgent   — near-term events that can move the price (earnings,
    policy, deals, regulatory/geopolitical shocks) mined from the same search
    evidence. Unpredictable by nature; the agent names the watch-item.

Design rule (same as the rest of this codebase): deterministic Python computes
every score; the LLM only writes language (per-agent views + per-position
V/C/S/T reasons). The same universe + request + search evidence always yields
the same scores (NFR-1). Search failures and a missing LLM both degrade to
deterministic keyword scoring with a templated reason — the run never breaks.
"""

from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..config import get_llm, llm_available
from ..logconf import get_logger
from ..osint.search import news_search, search_available, web_search
from ..schemas import ConstructionRequest
from ..universe import Candidate

_log = get_logger()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class VcstScores(BaseModel):
    """Deterministic 0..1 scores per candidate (higher = more attractive)."""

    valuation: float = 0.5
    cycle: float = 0.5
    sentiment: float = 0.5
    trigger: float = 0.5
    combined: float = 0.5
    stance: str = "neutral"  # portfolio-level read: buy_dip | neutral | take_profit
    reasons: dict[str, str] = Field(default_factory=dict)  # V/C/S/T one-liners
    evidence_urls: list[str] = Field(default_factory=list)  # search links behind S/T


class VcstAgentVerdict(BaseModel):
    """One agent's output: deterministic scores + LLM commentary per symbol."""

    agent: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    commentary: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class VcstReport(BaseModel):
    """Orchestrator output consumed by the LLM constructor."""

    scores: dict[str, VcstScores] = Field(default_factory=dict)
    stance: str = "neutral"
    cycle_stance: str = "neutral"
    sentiment_coverage: float = 0.0  # fraction of scored names with any search hit
    agent_views: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    data_gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic scorers (RULE — no LLM)
# --------------------------------------------------------------------------- #
def valuation_score(c: Candidate) -> tuple[float, str]:
    """Cheap + income-generating = attractive. Missing data -> neutral 0.5."""
    bits: list[str] = []
    score, adj = 0.5, 0.0
    if c.pe_ratio is not None and c.pe_ratio > 0:
        pe_part = max(0.0, min(1.0, (35.0 - c.pe_ratio) / (35.0 - 12.0)))
        adj += (pe_part - 0.5) * 0.6
        bits.append(f"P/E {c.pe_ratio:.1f}")
    if c.dividend_yield is not None:
        y_part = max(0.0, min(1.0, c.dividend_yield / 0.04))
        adj += (y_part - 0.5) * 0.4
        bits.append(f"yield {c.dividend_yield:.1%}")
    if c.expected_return is not None:
        m_part = max(0.0, min(1.0, (c.expected_return + 0.10) / 0.30))
        adj += (m_part - 0.5) * 0.2
    score = max(0.05, min(0.95, score + adj))
    return round(score, 4), "V: " + (", ".join(bits) if bits else "no P/E/yield data — neutral")


_CYCLICAL = frozenset(
    {
        "financials",
        "financial services",
        "banks",
        "bank",
        "energy",
        "oil",
        "gas",
        "materials",
        "metals",
        "mining",
        "industrials",
        "capital goods",
        "real estate",
        "realty",
        "consumer discretionary",
        "auto",
        "automobile",
        "infrastructure",
        "cement",
        "steel",
    }
)
_DEFENSIVE = frozenset(
    {
        "fmcg",
        "consumer staples",
        "staples",
        "pharma",
        "pharmaceuticals",
        "healthcare",
        "utilities",
        "power",
        "telecom",
        "telecommunication",
    }
)


def _cyclicality(sector: str | None) -> str:
    s = (sector or "").strip().lower()
    if any(k in s for k in _CYCLICAL):
        return "cyclical"
    if any(k in s for k in _DEFENSIVE):
        return "defensive"
    return "neutral"


def cycle_score(c: Candidate, cycle_stance: str = "neutral") -> tuple[float, str]:
    """Contrarian cycle positioning. `cycle_stance`: weak | neutral | strong."""
    stance = (cycle_stance or "neutral").lower()
    kind = _cyclicality(c.sector)
    if stance == "weak":  # buy the dip in cyclicals
        base = {"cyclical": 0.80, "neutral": 0.55, "defensive": 0.35}[kind]
    elif stance == "strong":  # late cycle: prefer defensives
        base = {"cyclical": 0.35, "neutral": 0.55, "defensive": 0.80}[kind]
    else:  # neutral: follow earnings momentum
        exp_ret = c.expected_return if c.expected_return is not None else 0.0
        base = max(0.15, min(0.85, 0.5 + exp_ret))
    reason = f"C: {kind} sector ({c.sector or 'unclassified'}) in a {stance} cycle"
    return round(base, 4), reason


_POS_WORDS = (
    "upgrade",
    "outperform",
    "overweight",
    "buy rating",
    "record profit",
    "strong demand",
    "beats estimates",
    "raised guidance",
    "bullish",
    "contract win",
    "order win",
    "growth",
)
_NEG_WORDS = (
    "downgrade",
    "underperform",
    "sell rating",
    "selloff",
    "plunge",
    "warning",
    "probe",
    "lawsuit",
    "fine",
    "fraud",
    "default",
    "loss",
    "weak demand",
    "guidance cut",
    "layoff",
    "ban",
    "sanction",
)
_TRIGGER_WORDS = (
    "earnings",
    "results",
    "guidance",
    "merger",
    "acquisition",
    "fda approval",
    "contract",
    "policy",
    "rate cut",
    "rate hike",
    "stimulus",
    "election",
    "budget",
    "lawsuit",
    "probe",
    "ban",
    "tariff",
    "war",
    "conflict",
)
_ADVERSE_TRIGGER = frozenset(
    {
        "lawsuit",
        "probe",
        "ban",
        "tariff",
        "war",
        "conflict",
        "default",
        "fraud",
    }
)


def sentiment_from_evidence(items: list[dict]) -> tuple[float, str, list[str]]:
    """Keyword sentiment over collected search snippets (deterministic)."""
    if not items:
        return 0.5, "S: no search hits — neutral (coverage gap)", []
    pos = neg = 0
    urls: list[str] = []
    for it in items:
        text = f"{it.get('title') or ''} {it.get('snippet') or ''}".lower()
        pos += sum(1 for w in _POS_WORDS if w in text)
        neg += sum(1 for w in _NEG_WORDS if w in text)
        if it.get("url"):
            urls.append(it["url"])
    # Contrarian ICICI read: heavy negativity is an opportunity (higher score),
    # euphoria is a take-profit signal — but bounded so sentiment never dominates.
    raw = 0.5 + 0.08 * (neg - pos)
    score = max(0.15, min(0.85, raw))
    reason = f"S: {len(items)} hit(s), +{pos}/-{neg} language → contrarian read"
    return round(score, 4), reason, urls[:4]


def trigger_from_evidence(items: list[dict]) -> tuple[float, str]:
    """Near-term catalyst read over the same evidence (deterministic)."""
    if not items:
        return 0.55, "T: no visible catalyst — neutral watch"
    adverse, catalyst = 0, ""
    for it in items:
        text = f"{it.get('title') or ''} {it.get('snippet') or ''}".lower()
        hits = [w for w in _TRIGGER_WORDS if w in text]
        if hits and not catalyst:
            catalyst = f"{hits[0]}: {(it.get('title') or '')[:90]}"
        adverse += sum(1 for w in _ADVERSE_TRIGGER if w in text)
    if adverse >= 2:
        return 0.30, f"T: adverse trigger overhang ({catalyst or 'see news'}) — wait"
    if catalyst:
        return 0.70, f"T: catalyst to watch — {catalyst}"
    return 0.55, "T: news flow but no clear catalyst"


_VCST_WEIGHTS = {"valuation": 0.30, "cycle": 0.25, "sentiment": 0.25, "trigger": 0.20}


def combined_score(v: float, c: float, s: float, t: float) -> float:
    return round(
        v * _VCST_WEIGHTS["valuation"]
        + c * _VCST_WEIGHTS["cycle"]
        + s * _VCST_WEIGHTS["sentiment"]
        + t * _VCST_WEIGHTS["trigger"],
        4,
    )


# --------------------------------------------------------------------------- #
# Sentiment search (LIVE evidence: stock + industry/sector — REQUIRED input)
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


def collect_vcst_sentiment(
    universe: list[Candidate],
    symbol_to_name: dict[str, str] | None = None,
    max_names: int | None = None,
    max_results: int | None = None,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Collect stock-level + sector-level search hits per candidate.

    Returns (evidence_by_symbol, data_gaps). Fail-soft: missing `ddgs`,
    rate limits, or zero results yield empty lists + a gap note — never raise.
    """
    s2n = {k.upper(): v for k, v in (symbol_to_name or {}).items()}
    for c in universe:
        if c.symbol.upper() not in s2n and c.name:
            s2n[c.symbol.upper()] = c.name
    gaps: list[str] = []
    if not search_available():
        return {}, ["DuckDuckGo backend (ddgs) not installed — sentiment has no search cover."]
    n_names = max_names or _env_int("PM_VCST_MAX_NAMES", 15)
    n_res = max_results or _env_int("PM_VCST_MAX_RESULTS", 3)

    # Bound cost: priced names first (they are the ones that can be bought).
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
                f"{sec} sector outlook demand 2026",
                max_results=n_res,
                fallback_query=f"{sec} sector outlook",
            ) + web_search(
                f"{sec} industry outlook growth", max_results=2, fallback_query=f"{sec} outlook"
            )
        except Exception as e:  # fail-soft per sector
            _log.warning("vcst[sentiment]: sector search failed for %s (%s).", sec, e)
            sector_hits[sec] = []

    out: dict[str, list[dict]] = {}
    for c in ranked:
        name = _search_name(c.symbol, s2n)
        try:
            stock_hits = news_search(
                f"{name} stock analyst rating outlook", max_results=n_res, fallback_query=name
            )
        except Exception as e:
            _log.warning("vcst[sentiment]: stock search failed for %s (%s).", c.symbol, e)
            stock_hits = []
        sec_hits = sector_hits.get((c.sector or "").strip(), [])
        for h in stock_hits:
            h["level"] = "stock"
        for h in sec_hits:
            h.setdefault("level", "sector")
        out[c.symbol.upper()] = stock_hits + sec_hits
    covered = sum(1 for v in out.values() if v)
    _log.info(
        "vcst[sentiment]: %d/%d names with search hits (%d sectors).",
        covered,
        len(out),
        len(sectors),
    )
    if covered == 0:
        gaps.append("Search returned no hits for any scored name — sentiment is neutral.")
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
                f"You are the {agent} agent in an ICICI-style VCST investment committee "
                f"for a {request.mandate} mandate (stance {stance}). {instruction} "
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
        _log.warning("vcst[%s]: LLM view failed (%s) — deterministic only.", agent, e)
        return {}


def _mini_digest(
    universe: list[Candidate],
    evidence: dict[str, list[dict]],
) -> list[dict]:
    out = []
    for c in universe:
        items = evidence.get(c.symbol.upper(), [])
        out.append(
            {
                "symbol": c.symbol,
                "pe": round(c.pe_ratio, 2) if c.pe_ratio is not None else None,
                "yield": round(c.dividend_yield, 4) if c.dividend_yield is not None else None,
                "sector": c.sector,
                "cap": c.market_cap.value,
                "n_search_hits": len(items),
                "headlines": [(i.get("title") or "")[:120] for i in items[:4]],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_vcst(
    universe: list[Candidate],
    request: ConstructionRequest,
    symbol_to_name: dict[str, str] | None = None,
    cycle_stance: str = "neutral",
    use_llm: bool = True,
    enable_search: bool = True,
    precollected: dict[str, list[dict]] | None = None,
) -> VcstReport:
    """Run all four VCST agents and combine their deterministic scores.

    RULE: scores + stance are always deterministic. Live search evidence feeds
    the Sentiment/Trigger scores (keyword path) and the LLM commentary; the LLM
    never changes a score. Set `enable_search=False` for a fully offline run.
    """
    evidence: dict[str, list[dict]] = precollected or {}
    gaps: list[str] = []
    if enable_search and not precollected:
        evidence, gaps = collect_vcst_sentiment(universe, symbol_to_name)

    v_tbl = {c.symbol.upper(): valuation_score(c) for c in universe}
    c_tbl = {c.symbol.upper(): cycle_score(c, cycle_stance) for c in universe}
    s_tbl: dict[str, tuple[float, str, list[str]]] = {}
    t_tbl: dict[str, tuple[float, str]] = {}
    for c in universe:
        sym = c.symbol.upper()
        items = evidence.get(sym, [])
        s_tbl[sym] = sentiment_from_evidence(items)
        t_tbl[sym] = trigger_from_evidence(items)

    # Portfolio stance from the average contrarian sentiment + cycle.
    if s_tbl:
        avg_s = sum(v[0] for v in s_tbl.values()) / len(s_tbl)
        stance = "buy_dip" if avg_s > 0.58 else "take_profit" if avg_s < 0.45 else "neutral"
    else:
        stance = "neutral"

    # LLM commentary per agent (fail-soft; deterministic reasons always stand).
    det_comment: dict[str, dict[str, str]] = {
        "valuation": {s: v[1] for s, v in v_tbl.items()},
        "cycle": {s: v[1] for s, v in c_tbl.items()},
        "sentiment": {s: v[1] for s, v in s_tbl.items()},
        "trigger": {s: v[1] for s, v in t_tbl.items()},
    }
    llm_comment: dict[str, dict[str, str]] = {}
    if use_llm and llm_available():
        digest = _mini_digest(universe, evidence)
        llm_comment["valuation"] = _llm_commentary(
            "Valuation",
            request,
            digest,
            "Judge price vs intrinsic value: cheap P/E + sustainable yield wins.",
            stance,
        )
        llm_comment["cycle"] = _llm_commentary(
            "Cycle",
            request,
            digest,
            f"Judge cycle fit: stance is {cycle_stance}; "
            "cyclicals suit weak cycles, defensives suit strong ones.",
            stance,
        )
        # Sentiment LLM sees the REAL search snippets (stock + sector).
        llm_comment["sentiment"] = _llm_commentary(
            "Sentiment",
            request,
            digest,
            "Judge investor affinity ONLY from the supplied headlines/search counts: "
            "fear is opportunity, euphoria is take-profit. Do not invent news.",
            stance,
        )
        llm_comment["trigger"] = _llm_commentary(
            "Trigger",
            request,
            digest,
            "Name the one near-term trigger to watch per symbol "
            "(earnings/policy/deal/shock) from the headlines, or say none visible.",
            stance,
        )

    def _view(agent: str, sym: str, fallback: str) -> str:
        return llm_comment.get(agent, {}).get(sym) or det_comment[agent].get(sym, fallback)

    combined: dict[str, VcstScores] = {}
    for c in universe:
        sym = c.symbol.upper()
        v, li = v_tbl[sym][0], c_tbl[sym][0]
        s, t = s_tbl[sym][0], t_tbl[sym][0]
        reasons = {
            "valuation": _view("valuation", sym, v_tbl[sym][1]),
            "cycle": _view("cycle", sym, c_tbl[sym][1]),
            "sentiment": _view("sentiment", sym, s_tbl[sym][1]),
            "trigger": _view("trigger", sym, t_tbl[sym][1]),
        }
        combined[sym] = VcstScores(
            valuation=v,
            cycle=li,
            sentiment=s,
            trigger=t,
            combined=combined_score(v, li, s, t),
            stance=stance,
            reasons=reasons,
            evidence_urls=s_tbl[sym][2],
        )
    covered = sum(1 for c in universe if evidence.get(c.symbol.upper()))
    coverage = round(covered / max(1, len(universe)), 3)
    summary = (
        f"VCST committee: {len(combined)} names scored, stance `{stance}` "
        f"(cycle {cycle_stance}; sentiment coverage {coverage:.0%}; "
        f"weights V{_VCST_WEIGHTS['valuation']:.0%}/C{_VCST_WEIGHTS['cycle']:.0%}/"
        f"S{_VCST_WEIGHTS['sentiment']:.0%}/T{_VCST_WEIGHTS['trigger']:.0%})."
    )
    _log.info("vcst: %s", summary)
    views = {
        "valuation": f"Valuation screen over {len(combined)} names (P/E + yield).",
        "cycle": f"Cycle screen ({cycle_stance} stance, cyclical vs defensive).",
        "sentiment": f"Sentiment from live search over {covered} name(s).",
        "trigger": "Trigger watch-items mined from the same search evidence.",
    }
    return VcstReport(
        scores=combined,
        stance=stance,
        cycle_stance=cycle_stance,
        sentiment_coverage=coverage,
        agent_views=views,
        summary=summary,
        data_gaps=gaps,
    )

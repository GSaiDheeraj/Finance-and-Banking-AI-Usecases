"""
SQL agents for LLM-led portfolio construction (Motilal Oswal-style framework).

Implements a Size / Quality / Longevity checklist in the spirit of Motilal
Oswal's philosophy — three specialist agents plus an orchestrator:

  * SizeAgent      — size of the opportunity + headroom to grow. Favours
    companies with a huge addressable opportunity (large-opportunity sectors,
    growing industries) where the current size still leaves headroom —
    typically mid/small caps in big sectors over already-scaled giants.
  * QualityAgent   — Q = quality of business x quality of management
    (multiplicative, not additive — if either is zero, quality is zero).
    Business quality from stability (low volatility), payout discipline
    (dividend yield) and live price data; management quality from the news
    tone (adverse findings discount) and institutional-grade size.
  * LongevityAgent — durability of quality and compounding: sustained positive
    drift with low volatility (extending the Competitive Advantage Period),
    durable vehicles (diversified funds over single fragile names) and
    long-relevance, consumer-facing sectors over fad themes.

Note on Motilal Oswal's published QGLP (Quality, Growth, Longevity, Price):
this module implements the Size/Quality/Longevity subset requested — Growth
appears as compounding momentum inside Longevity, and Price discipline is
already enforced by the guardrails plus the VLRT/VCST valuation agents.

Design rule (same as the rest of this codebase): deterministic Python computes
every score; the LLM only writes language (per-agent views + per-position
S/Q/L reasons). The same universe + request always yields the same scores
(NFR-1). A missing LLM degrades to deterministic reasons — the run never
breaks. Fully offline: no search, no network.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..config import get_llm, llm_available
from ..logconf import get_logger
from ..schemas import ConstructionRequest, InstrumentType, MarketCap
from ..universe import Candidate

_log = get_logger()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class SqlScores(BaseModel):
    """Deterministic 0..1 scores per candidate (higher = more attractive)."""

    size: float = 0.5
    quality: float = 0.5
    longevity: float = 0.5
    combined: float = 0.5
    stance: str = "selective"  # portfolio-level read: high_conviction|selective|cautious
    reasons: dict[str, str] = Field(default_factory=dict)  # S/Q/L one-liners


class SqlAgentVerdict(BaseModel):
    """One agent's output: deterministic scores + LLM commentary per symbol."""

    agent: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    commentary: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class SqlReport(BaseModel):
    """Orchestrator output consumed by the LLM constructor."""

    scores: dict[str, SqlScores] = Field(default_factory=dict)
    stance: str = "selective"
    agent_views: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    data_gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic scorers (RULE — no LLM)
# --------------------------------------------------------------------------- #
# Sectors with structurally large addressable opportunity (financials, domestic
# consumption, healthcare, tech, energy/infra). Everything else is neutral.
_LARGE_OPPORTUNITY = frozenset(
    {
        "financials",
        "financial services",
        "banks",
        "bank",
        "insurance",
        "information technology",
        "technology",
        "it ",
        "pharma",
        "pharmaceuticals",
        "healthcare",
        "fmcg",
        "consumer staples",
        "staples",
        "consumer discretionary",
        "auto",
        "automobile",
        "energy",
        "oil",
        "gas",
        "power",
        "utilities",
        "industrials",
        "infrastructure",
        "capital goods",
        "telecom",
    }
)
# Long-relevance, consumer-facing / durable sectors (compound for decades).
_DURABLE = frozenset(
    {
        "fmcg",
        "consumer staples",
        "staples",
        "consumer discretionary",
        "pharma",
        "pharmaceuticals",
        "healthcare",
        "banks",
        "bank",
        "financials",
        "financial services",
        "information technology",
        "technology",
        "telecom",
        "telecommunication",
    }
)


def _opportunity(sector: str | None) -> str:
    s = (sector or "").strip().lower()
    if any(k in s for k in _LARGE_OPPORTUNITY):
        return "large"
    return "niche"


def size_score(c: Candidate) -> tuple[float, str]:
    """Opportunity size x headroom. Mid/small caps in large-opportunity sectors win;
    already-scaled large caps score moderate; unknown/unpriced names score low."""
    opp = _opportunity(c.sector)
    opp_base = 0.75 if opp == "large" else 0.45
    headroom = {
        MarketCap.SMALL_CAP: 0.85,
        MarketCap.MID_CAP: 0.75,
        MarketCap.LARGE_CAP: 0.55,
        MarketCap.UNKNOWN: 0.45,
    }.get(c.market_cap, 0.45)
    if c.instrument_type in (InstrumentType.ETF, InstrumentType.MUTUAL_FUND):
        # Diversified vehicles already span the opportunity — moderate headroom.
        headroom = min(headroom, 0.60)
    if not c.has_market_data:
        headroom = min(headroom, 0.35)
    score = max(0.05, min(0.95, 0.5 * opp_base + 0.5 * headroom))
    reason = (
        f"S: {opp}-opportunity sector ({c.sector or 'unclassified'}), {c.market_cap.value} headroom"
    )
    return round(score, 4), reason


def quality_score(
    c: Candidate,
    news_tone: str | None = None,
) -> tuple[float, str]:
    """Q = Qbiz x Qmgmt (multiplicative). Missing data -> neutral 0.5 each leg."""
    # --- quality of business: stability + payout discipline + priced data ---
    vol = c.volatility if c.volatility is not None else 0.25
    stability = max(0.0, min(1.0, (0.50 - vol) / 0.40))  # low vol ~ 1
    payout = 0.5
    if c.dividend_yield is not None:
        payout = max(0.0, min(1.0, c.dividend_yield / 0.04))
    data_factor = 1.0 if c.has_market_data else 0.6
    qbiz = max(0.05, min(1.0, (0.6 * stability + 0.4 * payout) * data_factor))
    # --- quality of management: news tone + institutional-grade size ---
    qmgmt = 0.7
    if c.market_cap in (MarketCap.LARGE_CAP, MarketCap.MID_CAP):
        qmgmt = 0.8
    elif c.market_cap == MarketCap.SMALL_CAP:
        qmgmt = 0.6
    if news_tone == "high":
        qmgmt *= 0.5
    elif news_tone == "medium":
        qmgmt *= 0.75
    qmgmt = max(0.05, min(1.0, qmgmt))
    q = max(0.05, min(0.95, qbiz * qmgmt / 0.7))  # renormalise (~0.7 = typical good)
    reason = (
        f"Q: business {qbiz:.2f} (vol {vol:.0%}, yield "
        f"{(c.dividend_yield or 0):.1%}) x management {qmgmt:.2f}"
        + (f" [news {news_tone}]" if news_tone else "")
    )
    return round(q, 4), reason


def longevity_score(c: Candidate) -> tuple[float, str]:
    """Durability of compounding: sustained drift + low vol + durable vehicle +
    long-relevance sector. Single fragile names score low."""
    exp_ret = c.expected_return if c.expected_return is not None else 0.0
    vol = c.volatility if c.volatility and c.volatility > 0 else 0.25
    # Sustained compounding: positive drift at modest vol extends CAP.
    momentum = (
        max(0.0, min(1.0, (exp_ret / vol + 0.5) / 1.5)) if c.expected_return is not None else 0.5
    )
    vehicle = (
        0.75
        if c.instrument_type in (InstrumentType.ETF, InstrumentType.MUTUAL_FUND)
        else (
            0.65
            if c.market_cap == MarketCap.LARGE_CAP
            else 0.50
            if c.market_cap == MarketCap.MID_CAP
            else 0.35
            if c.market_cap == MarketCap.SMALL_CAP
            else 0.45
        )
    )
    s = (c.sector or "").strip().lower()
    relevance = 0.75 if any(k in s for k in _DURABLE) else 0.50
    if not c.has_market_data:
        vehicle = min(vehicle, 0.35)
    score = max(0.05, min(0.95, 0.4 * momentum + 0.35 * vehicle + 0.25 * relevance))
    reason = (
        f"L: drift {exp_ret:.1%} @ vol {vol:.0%}, "
        f"{c.instrument_type.value}/{c.market_cap.value}, "
        f"{'durable' if relevance >= 0.7 else 'neutral'} sector"
    )
    return round(score, 4), reason


_SQL_WEIGHTS = {"size": 0.25, "quality": 0.40, "longevity": 0.35}


def combined_score(s: float, q: float, lng: float) -> float:
    return round(
        s * _SQL_WEIGHTS["size"] + q * _SQL_WEIGHTS["quality"] + lng * _SQL_WEIGHTS["longevity"],
        4,
    )


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
                f"You are the {agent} agent in a Motilal Oswal-style Size/Quality/Longevity "
                f"investment committee for a {request.mandate} mandate (stance {stance}). "
                f"{instruction} "
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
        _log.warning("sql[%s]: LLM view failed (%s) — deterministic only.", agent, e)
        return {}


def _mini_digest(
    universe: list[Candidate],
    news_tones: dict[str, str],
) -> list[dict]:
    out = []
    for c in universe:
        out.append(
            {
                "symbol": c.symbol,
                "sector": c.sector,
                "cap": c.market_cap.value,
                "instrument": c.instrument_type.value,
                "vol": round(c.volatility, 4) if c.volatility is not None else None,
                "exp_ret": round(c.expected_return, 4) if c.expected_return is not None else None,
                "yield": round(c.dividend_yield, 4) if c.dividend_yield is not None else None,
                "news": news_tones.get(c.symbol.upper()),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_sql(
    universe: list[Candidate],
    request: ConstructionRequest,
    news_tones: dict[str, str] | None = None,
    use_llm: bool = True,
) -> SqlReport:
    """Run all three SQL agents and combine their deterministic scores.

    RULE: scores + stance are always deterministic. The LLM (when available and
    use_llm) only adds per-agent commentary lines; it never changes a score.
    Fully offline: no search, no network.
    """
    tones = news_tones or {}
    s_tbl = {c.symbol.upper(): size_score(c) for c in universe}
    q_tbl = {c.symbol.upper(): quality_score(c, tones.get(c.symbol.upper())) for c in universe}
    l_tbl = {c.symbol.upper(): longevity_score(c) for c in universe}

    # Portfolio stance from average quality: high quality everywhere = high
    # conviction buy-and-hold; patchy quality = selective; poor = cautious.
    avg_q = sum(v[0] for v in q_tbl.values()) / max(1, len(q_tbl))
    stance = "high_conviction" if avg_q >= 0.60 else "selective" if avg_q >= 0.40 else "cautious"

    det_comment: dict[str, dict[str, str]] = {
        "size": {s: v[1] for s, v in s_tbl.items()},
        "quality": {s: v[1] for s, v in q_tbl.items()},
        "longevity": {s: v[1] for s, v in l_tbl.items()},
    }
    llm_comment: dict[str, dict[str, str]] = {}
    if use_llm and llm_available():
        digest = _mini_digest(universe, tones)
        llm_comment["size"] = _llm_commentary(
            "Size",
            request,
            digest,
            "Judge opportunity size vs headroom: mid/small caps in huge sectors win; "
            "scaled giants are moderate.",
            stance,
        )
        llm_comment["quality"] = _llm_commentary(
            "Quality",
            request,
            digest,
            "Judge Q = business x management: stable dividend-payers with clean news win; "
            "adverse-news or unpriced names lose.",
            stance,
        )
        llm_comment["longevity"] = _llm_commentary(
            "Longevity",
            request,
            digest,
            "Judge durability: sustained low-vol compounding in long-relevance "
            "sectors wins; fragile single names lose.",
            stance,
        )

    def _view(agent: str, sym: str, fallback: str) -> str:
        return llm_comment.get(agent, {}).get(sym) or det_comment[agent].get(sym, fallback)

    combined: dict[str, SqlScores] = {}
    for c in universe:
        sym = c.symbol.upper()
        s, q = s_tbl[sym][0], q_tbl[sym][0]
        lng = l_tbl[sym][0]
        reasons = {
            "size": _view("size", sym, s_tbl[sym][1]),
            "quality": _view("quality", sym, q_tbl[sym][1]),
            "longevity": _view("longevity", sym, l_tbl[sym][1]),
        }
        combined[sym] = SqlScores(
            size=s,
            quality=q,
            longevity=lng,
            combined=combined_score(s, q, lng),
            stance=stance,
            reasons=reasons,
        )
    summary = (
        f"SQL committee: {len(combined)} names scored, stance `{stance}` "
        f"(avg quality {avg_q:.2f}; weights "
        f"S{_SQL_WEIGHTS['size']:.0%}/Q{_SQL_WEIGHTS['quality']:.0%}/"
        f"L{_SQL_WEIGHTS['longevity']:.0%})."
    )
    _log.info("sql: %s", summary)
    views = {
        "size": f"Size screen over {len(combined)} names (opportunity x headroom).",
        "quality": f"Quality screen (Qbiz x Qmgmt, avg {avg_q:.2f}).",
        "longevity": "Longevity screen (sustained compounding + durable vehicles).",
    }
    return SqlReport(
        scores=combined, stance=stance, agent_views=views, summary=summary, data_gaps=[]
    )

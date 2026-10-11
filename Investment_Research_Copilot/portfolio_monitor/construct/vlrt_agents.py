"""
VLRT agents for LLM-led portfolio construction (Quant-style framework).

Implements Quant Mutual Fund's VLRT investment framework as four specialised
agents — one per dimension — plus an orchestrator:

  * ValuationAgent     — price vs value (P/E, dividend yield, earnings proxy).
  * LiquidityAgent     — flow of money / ease of exit (cap tier, instrument, data).
  * RiskAppetiteAgent  — who should own what (volatility vs mandate tolerance + news).
  * TimingAgent        — when to own it (momentum / trend / risk regime).

Design rule (same as the rest of this codebase): deterministic Python computes
every score; the LLM only writes language (per-agent views + per-position
V/L/R/T reasons). The same universe + request always yields the same scores
(NFR-1). If the LLM is unavailable every agent degrades to its deterministic
score with a templated reason.
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
class VlrtScores(BaseModel):
    """Deterministic 0..1 scores per candidate (higher = more attractive)."""

    valuation: float = 0.5
    liquidity: float = 0.5
    risk_appetite: float = 0.5
    timing: float = 0.5
    combined: float = 0.5
    regime: str = "neutral"  # risk_on | risk_off | neutral (portfolio-level read)
    reasons: dict[str, str] = Field(default_factory=dict)  # V/L/R/T one-liners


class VlrtAgentVerdict(BaseModel):
    """One agent's output: deterministic scores + LLM commentary per symbol."""

    agent: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    commentary: dict[str, str] = Field(default_factory=dict)  # symbol -> LLM one-liner
    summary: str = ""


class VlrtReport(BaseModel):
    """Orchestrator output consumed by the LLM constructor."""

    scores: dict[str, VlrtScores] = Field(default_factory=dict)
    regime: str = "neutral"
    agent_views: dict[str, str] = Field(default_factory=dict)  # agent -> summary
    summary: str = ""


# --------------------------------------------------------------------------- #
# Deterministic scoring (RULE — no LLM)
# --------------------------------------------------------------------------- #
def valuation_score(c: Candidate) -> tuple[float, str]:
    """Cheap + income-generating = attractive. Missing data -> neutral 0.5."""
    bits: list[str] = []
    score = 0.5
    adj = 0.0
    if c.pe_ratio is not None and c.pe_ratio > 0:
        # P/E <= 12 very cheap, >= 35 very expensive (linear in between).
        pe_part = max(0.0, min(1.0, (35.0 - c.pe_ratio) / (35.0 - 12.0)))
        adj += (pe_part - 0.5) * 0.6
        bits.append(f"P/E {c.pe_ratio:.1f}")
    if c.dividend_yield is not None:
        # 0% -> 0, >= 4% -> 1.
        y_part = max(0.0, min(1.0, c.dividend_yield / 0.04))
        adj += (y_part - 0.5) * 0.4
        bits.append(f"yield {c.dividend_yield:.1%}")
    if c.expected_return is not None:
        # Positive historical drift supports value realisation (bounded tilt).
        m_part = max(0.0, min(1.0, (c.expected_return + 0.10) / 0.30))
        adj += (m_part - 0.5) * 0.2
    score = max(0.05, min(0.95, score + adj))
    reason = "V: " + (", ".join(bits) if bits else "no P/E/yield data — neutral")
    return round(score, 4), reason


def liquidity_score(c: Candidate) -> tuple[float, str]:
    """Large/ETF/priced = liquid. Small/unpriced = illiquid."""
    cap_base = {
        MarketCap.LARGE_CAP: 0.90,
        MarketCap.MID_CAP: 0.70,
        MarketCap.SMALL_CAP: 0.45,
        MarketCap.UNKNOWN: 0.50,
    }.get(c.market_cap, 0.50)
    inst_bump = 0.0
    if c.instrument_type in (InstrumentType.ETF, InstrumentType.MUTUAL_FUND):
        inst_bump = 0.05  # diversified vehicles trade/collapse easily
    score = cap_base + inst_bump
    if not c.has_market_data:
        score = min(score, 0.30)
        reason = "L: no live price — treated as illiquid"
    else:
        tier = c.market_cap.value if c.market_cap != MarketCap.UNKNOWN else "unknown cap"
        reason = (
            f"L: {tier} {c.instrument_type.value}, priced — liquid"
            if score >= 0.7
            else f"L: {tier} — moderate liquidity"
        )
    return round(max(0.05, min(0.95, score)), 4), reason


def risk_appetite_score(
    c: Candidate,
    risk_tolerance: str = "medium",
    news_tone: str | None = None,
) -> tuple[float, str]:
    """Low vol + clean news = attractive; penalty scales with mandate tolerance."""
    tol = (risk_tolerance or "medium").lower()
    vol = c.volatility if c.volatility is not None else 0.25
    # vol 10% -> ~1, vol 50% -> ~0.
    base = max(0.0, min(1.0, (0.50 - vol) / 0.40))
    # Conservative investors penalise volatility harder.
    if tol == "low":
        base = base**1.5
    elif tol == "high":
        base = 0.5 + (base - 0.5) * 0.6  # compress toward neutral
    penalty = 0.0
    if news_tone in ("high", "medium"):
        penalty = 0.25 if news_tone == "high" else 0.12
    score = max(0.05, min(0.95, base - penalty))
    reason = f"R: vol {vol:.1%} vs {tol} tolerance" + (
        f", news {news_tone} (-{penalty:.0%})" if penalty else ""
    )
    return round(score, 4), reason


def timing_score(c: Candidate) -> tuple[float, str]:
    """Momentum/trend: positive risk-adjusted drift = good time; negative = wait."""
    exp_ret = c.expected_return if c.expected_return is not None else 0.0
    vol = c.volatility if c.volatility and c.volatility > 0 else 0.25
    sharpe_like = exp_ret / vol  # annualised proxy, rough but deterministic
    # Map roughly [-0.5, +1.0] -> [0, 1].
    score = max(0.0, min(1.0, (sharpe_like + 0.5) / 1.5))
    if c.expected_return is None:
        score = 0.5
        reason = "T: no history — neutral timing"
    elif exp_ret > 0.05:
        reason = f"T: positive momentum {exp_ret:.1%} — favourable"
    elif exp_ret < -0.05:
        reason = f"T: negative drift {exp_ret:.1%} — unfavourable"
    else:
        reason = f"T: flat drift {exp_ret:.1%} — neutral"
    return round(max(0.05, min(0.95, score)), 4), reason


_VLRT_WEIGHTS = {"valuation": 0.30, "liquidity": 0.25, "risk_appetite": 0.25, "timing": 0.20}


def combined_score(v: float, liq: float, r: float, t: float) -> float:
    return round(
        v * _VLRT_WEIGHTS["valuation"]
        + liq * _VLRT_WEIGHTS["liquidity"]
        + r * _VLRT_WEIGHTS["risk_appetite"]
        + t * _VLRT_WEIGHTS["timing"],
        4,
    )


def detect_regime(
    universe: list[Candidate],
    news_tones: dict[str, str],
    risk_tolerance: str = "medium",
) -> str:
    """Portfolio-level risk_on / risk_off / neutral read (deterministic)."""
    vols = [c.volatility for c in universe if c.volatility is not None]
    avg_vol = sum(vols) / len(vols) if vols else 0.25
    n_high = sum(1 for s in news_tones.values() if s == "high")
    n_med = sum(1 for s in news_tones.values() if s == "medium")
    stress = n_high * 2 + n_med
    if avg_vol < 0.22 and stress == 0:
        return "risk_on"
    if avg_vol > 0.32 or n_high >= 2 or stress >= 4:
        return "risk_off"
    return "neutral"


# --------------------------------------------------------------------------- #
# Per-agent runners (deterministic scores + optional LLM commentary)
# --------------------------------------------------------------------------- #
def _llm_commentary(
    agent: str,
    request: ConstructionRequest,
    digest: list[dict],
    instruction: str,
    regime: str,
) -> dict[str, str]:
    """One small LLM call per agent. Returns {} on any failure (fail-soft)."""
    if not llm_available():
        return {}
    try:
        system = SystemMessage(
            content=(
                f"You are the {agent} agent in a Quant-style VLRT investment committee "
                f"for a {request.mandate} mandate (tolerance {request.risk_tolerance}, "
                f"regime {regime}). {instruction} "
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
    except Exception as e:  # fail-soft: deterministic scores still stand
        _log.warning("vlrt[%s]: LLM view failed (%s) — deterministic only.", agent, e)
        return {}


def _mini_digest(universe: list[Candidate], news_tones: dict[str, str]) -> list[dict]:
    out = []
    for c in universe:
        out.append(
            {
                "symbol": c.symbol,
                "pe": round(c.pe_ratio, 2) if c.pe_ratio is not None else None,
                "yield": round(c.dividend_yield, 4) if c.dividend_yield is not None else None,
                "vol": round(c.volatility, 4) if c.volatility is not None else None,
                "exp_ret": round(c.expected_return, 4) if c.expected_return is not None else None,
                "cap": c.market_cap.value,
                "instrument": c.instrument_type.value,
                "news": news_tones.get(c.symbol.upper()),
            }
        )
    return out


def run_valuation_agent(
    universe: list[Candidate],
    request: ConstructionRequest,
    regime: str,
) -> VlrtAgentVerdict:
    scores, det = {}, {}
    for c in universe:
        s, r = valuation_score(c)
        scores[c.symbol.upper()] = s
        det[c.symbol.upper()] = r
    llm_views = _llm_commentary(
        "Valuation",
        request,
        _mini_digest(universe, {}),
        "Judge price vs value: cheap P/E and sustainable yield are attractive; "
        "expensive low-yield names are not.",
        regime,
    )
    commentary = {s: llm_views.get(s, det[s]) for s in scores}
    return VlrtAgentVerdict(
        agent="valuation",
        scores=scores,
        commentary=commentary,
        summary=f"Valuation screen over {len(scores)} names (P/E + yield).",
    )


def run_liquidity_agent(
    universe: list[Candidate],
    request: ConstructionRequest,
    regime: str,
) -> VlrtAgentVerdict:
    scores, det = {}, {}
    for c in universe:
        s, r = liquidity_score(c)
        scores[c.symbol.upper()] = s
        det[c.symbol.upper()] = r
    llm_views = _llm_commentary(
        "Liquidity",
        request,
        _mini_digest(universe, {}),
        "Judge ease of exit and flow: large-cap priced ETFs are most liquid; "
        "small unpriced names least.",
        regime,
    )
    commentary = {s: llm_views.get(s, det[s]) for s in scores}
    return VlrtAgentVerdict(
        agent="liquidity",
        scores=scores,
        commentary=commentary,
        summary=f"Liquidity screen over {len(scores)} names (cap + vehicle + priced).",
    )


def run_risk_appetite_agent(
    universe: list[Candidate],
    request: ConstructionRequest,
    regime: str,
    news_tones: dict[str, str],
) -> VlrtAgentVerdict:
    scores, det = {}, {}
    for c in universe:
        s, r = risk_appetite_score(c, request.risk_tolerance, news_tones.get(c.symbol.upper()))
        scores[c.symbol.upper()] = s
        det[c.symbol.upper()] = r
    llm_views = _llm_commentary(
        "RiskAppetite",
        request,
        _mini_digest(universe, news_tones),
        "Judge suitability: high-vol or adverse-news names suit only high tolerance; "
        "calm low-vol suits low tolerance.",
        regime,
    )
    commentary = {s: llm_views.get(s, det[s]) for s in scores}
    return VlrtAgentVerdict(
        agent="risk_appetite",
        scores=scores,
        commentary=commentary,
        summary=f"Risk-appetite screen vs {request.risk_tolerance} tolerance + news.",
    )


def run_timing_agent(
    universe: list[Candidate],
    request: ConstructionRequest,
    regime: str,
) -> VlrtAgentVerdict:
    scores, det = {}, {}
    for c in universe:
        s, r = timing_score(c)
        scores[c.symbol.upper()] = s
        det[c.symbol.upper()] = r
    # Risk-off regime damps timing enthusiasm deterministically (documented tilt).
    if regime == "risk_off":
        scores = {s: round(max(0.05, v - 0.10), 4) for s, v in scores.items()}
    llm_views = _llm_commentary(
        "Timing",
        request,
        _mini_digest(universe, {}),
        "Judge when: positive momentum is favourable now; "
        "negative drift means wait — especially in risk-off.",
        regime,
    )
    commentary = {s: llm_views.get(s, det[s]) for s in scores}
    return VlrtAgentVerdict(
        agent="timing",
        scores=scores,
        commentary=commentary,
        summary=f"Timing screen ({regime} regime, momentum-based).",
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_vlrt(
    universe: list[Candidate],
    request: ConstructionRequest,
    news_tones: dict[str, str] | None = None,
    use_llm: bool = True,
) -> VlrtReport:
    """Run all four VLRT agents and combine their deterministic scores.

    RULE: scores + regime are always deterministic. LLM (when available and
    use_llm) only adds per-agent commentary lines; it never changes a score.
    """
    tones = news_tones or {}
    regime = detect_regime(universe, tones, request.risk_tolerance)

    # Deterministic scores first (always computed).
    v_scores: dict[str, tuple[float, str]] = {
        c.symbol.upper(): valuation_score(c) for c in universe
    }
    l_scores: dict[str, tuple[float, str]] = {
        c.symbol.upper(): liquidity_score(c) for c in universe
    }
    r_scores: dict[str, tuple[float, str]] = {
        c.symbol.upper(): risk_appetite_score(
            c, request.risk_tolerance, tones.get(c.symbol.upper())
        )
        for c in universe
    }
    t_scores: dict[str, tuple[float, str]] = {c.symbol.upper(): timing_score(c) for c in universe}
    if regime == "risk_off":
        t_scores = {
            s: (max(0.05, v[0] - 0.10), v[1] + " [risk-off -0.10]") for s, v in t_scores.items()
        }

    verdicts: dict[str, VlrtAgentVerdict] = {}
    if use_llm and llm_available():
        verdicts["valuation"] = run_valuation_agent(universe, request, regime)
        verdicts["liquidity"] = run_liquidity_agent(universe, request, regime)
        verdicts["risk_appetite"] = run_risk_appetite_agent(universe, request, regime, tones)
        verdicts["timing"] = run_timing_agent(universe, request, regime)
    else:
        for name, table in (
            ("valuation", v_scores),
            ("liquidity", l_scores),
            ("risk_appetite", r_scores),
            ("timing", t_scores),
        ):
            verdicts[name] = VlrtAgentVerdict(
                agent=name,
                scores={s: v[0] for s, v in table.items()},
                commentary={s: v[1] for s, v in table.items()},
                summary=f"{name} deterministic screen (LLM unavailable/off).",
            )

    combined: dict[str, VlrtScores] = {}
    for c in universe:
        sym = c.symbol.upper()
        v, liq = v_scores[sym][0], l_scores[sym][0]
        r, t = r_scores[sym][0], t_scores[sym][0]
        reasons = {
            "valuation": verdicts["valuation"].commentary.get(sym, v_scores[sym][1]),
            "liquidity": verdicts["liquidity"].commentary.get(sym, l_scores[sym][1]),
            "risk_appetite": verdicts["risk_appetite"].commentary.get(sym, r_scores[sym][1]),
            "timing": verdicts["timing"].commentary.get(sym, t_scores[sym][1]),
        }
        combined[sym] = VlrtScores(
            valuation=v,
            liquidity=liq,
            risk_appetite=r,
            timing=t,
            combined=combined_score(v, liq, r, t),
            regime=regime,
            reasons=reasons,
        )
    summary = (
        f"VLRT committee: {len(combined)} names scored, regime `{regime}` "
        f"(weights V{_VLRT_WEIGHTS['valuation']:.0%}/L{_VLRT_WEIGHTS['liquidity']:.0%}/"
        f"R{_VLRT_WEIGHTS['risk_appetite']:.0%}/T{_VLRT_WEIGHTS['timing']:.0%})."
    )
    _log.info("vlrt: %s", summary)
    return VlrtReport(
        scores=combined,
        regime=regime,
        agent_views={k: v.summary for k, v in verdicts.items()},
        summary=summary,
    )

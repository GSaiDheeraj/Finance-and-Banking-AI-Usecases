"""
BMV agents for LLM-led portfolio construction (Kotak-style framework).

Implements Kotak Mutual Fund's Business / Management / Valuation checklist —
three specialist agents plus an orchestrator, run as a SEQUENTIAL gate
(exactly how Kotak describes it: if the business is not liked, management and
valuation are not evaluated; if management fails its filters, the idea is
dropped no matter how cheap it looks):

  * BusinessAgent   — resilient companies with long-term growth potential:
    industries that can grow faster than GDP, companies that can outgrow
    competitors (sustained drift + low volatility as the proxy), an enduring
    moat (entrenched large caps with payout discipline), and consolidating —
    not fragmenting — industries. 5-to-10-year lens: fad themes score low.
  * ManagementAgent — track record on corporate governance, competency
    (execution = sustained compounding), stakeholder engagement (cash shared
    as yield, not hoarded) and capital allocation. **Uses LIVE public search**:
    per-stock governance / pledge / related-party queries plus per-sector
    conduct queries via free DuckDuckGo (`osint.search`). Adverse findings
    fail the gate.
  * ValuationAgent  — reasonable valuations with moat-adjusted premium: cheap
    P/E + yield wins outright, but a strong business + competent management
    (moat + steady cash flow) earns a reasonable premium instead of a penalty.
    Yet good-at-any-price is rejected — fully-priced speculative growth is
    capped no matter how good the first two legs look ("ready to let go").

Gating (the load-bearing rule): business < 0.35 caps combined at 0.35, else
management < 0.35 caps combined at 0.45, else the weighted blend
(B40/M35/V25). Every gate trip is named in the reason string.

Design rule (same as the rest of this codebase): deterministic Python computes
every score; the LLM only writes language (per-agent views + per-position
B/M/V reasons). The same universe + request + search evidence always yields
the same scores (NFR-1). Search failures and a missing LLM both degrade to
deterministic scoring with a templated reason — the run never breaks.
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
from ..schemas import ConstructionRequest, MarketCap
from ..universe import Candidate

_log = get_logger()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class BmvScores(BaseModel):
    """Deterministic 0..1 scores per candidate (higher = more attractive)."""

    business: float = 0.5
    management: float = 0.5
    valuation: float = 0.5
    combined: float = 0.5
    stance: str = "selective"  # portfolio-level read: high_conviction|selective|cautious
    reasons: dict[str, str] = Field(default_factory=dict)  # B/M/V one-liners
    evidence_urls: list[str] = Field(default_factory=list)  # search links behind M


class BmvAgentVerdict(BaseModel):
    """One agent's output: deterministic scores + LLM commentary per symbol."""

    agent: str = ""
    scores: dict[str, float] = Field(default_factory=dict)
    commentary: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class BmvReport(BaseModel):
    """Orchestrator output consumed by the LLM constructor."""

    scores: dict[str, BmvScores] = Field(default_factory=dict)
    stance: str = "selective"
    search_coverage: float = 0.0  # fraction of scored names with any search hit
    agent_views: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    data_gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic scorers (RULE — no LLM)
# --------------------------------------------------------------------------- #
# Industries with structural above-GDP growth prospects + consolidation
# (Kotak's stated positive pockets: manufacturing, manufacturing-allied such as
# corporate banking / logistics / utilities, infrastructure, domestic consumption).
_HIGH_GROWTH_INDUSTRIES = frozenset(
    {
        "industrials",
        "capital goods",
        "infrastructure",
        "manufacturing",
        "financials",
        "financial services",
        "banks",
        "bank",
        "logistics",
        "transport",
        "utilities",
        "power",
        "energy",
        "cement",
        "steel",
        "materials",
        "auto",
        "automobile",
        "consumer discretionary",
        "fmcg",
        "consumer staples",
        "staples",
        "healthcare",
        "pharma",
        "pharmaceuticals",
    }
)
# Fragmenting / fad-prone themes: hard to underwrite for 5–10 years.
_FRAGMENTING = frozenset(
    {
        "crypto",
        "meme",
        "cannabis",
        "gaming",
        "metaverse",
        "nft",
    }
)


def business_score(c: Candidate) -> tuple[float, str]:
    """Resilient, moated business in a fast-growing, consolidating industry."""
    s = (c.sector or "").strip().lower()
    if any(k in s for k in _FRAGMENTING):
        return 0.20, "B: fragmenting/fad theme — fails the 5–10yr business lens"
    industry = 0.70 if any(k in s for k in _HIGH_GROWTH_INDUSTRIES) else 0.50
    # Outgrow competitors: sustained positive drift at modest vol.
    exp_ret = c.expected_return if c.expected_return is not None else 0.0
    vol = c.volatility if c.volatility and c.volatility > 0 else 0.25
    growers = (
        max(0.0, min(1.0, (exp_ret / vol + 0.5) / 1.5)) if c.expected_return is not None else 0.5
    )
    # Enduring moat: entrenched scale + payout discipline + priced history.
    moat = 0.50
    if c.market_cap == MarketCap.LARGE_CAP:
        moat = 0.75
    elif c.market_cap == MarketCap.MID_CAP:
        moat = 0.60
    if c.dividend_yield is not None and c.dividend_yield >= 0.01:
        moat = min(0.90, moat + 0.10)
    if not c.has_market_data:
        moat = min(moat, 0.35)
    score = max(0.05, min(0.95, 0.40 * industry + 0.35 * growers + 0.25 * moat))
    reason = (
        f"B: {c.sector or 'unclassified'} industry {industry:.2f}, "
        f"outgrow {growers:.2f} (drift {exp_ret:.1%}), moat {moat:.2f}"
    )
    return round(score, 4), reason


_GOVERNANCE_BAD = (
    "fraud",
    "scam",
    "probe",
    "investigation",
    "lawsuit",
    "fine",
    "penalty",
    "default",
    "governance",
    "pledge",
    "pledging",
    "related party",
    "siphon",
    "arrest",
    "raid",
    "ban",
    "debar",
)
_STEWARDSHIP_GOOD = (
    "dividend increase",
    "buyback",
    "minority shareholder",
    "transparent",
    "governance award",
    "succession",
    "dividend payout",
)


def management_from_evidence(
    c: Candidate,
    items: list[dict],
    news_tone: str | None = None,
) -> tuple[float, str, list[str]]:
    """Track record: governance (search), competency/execution (compounding),
    stakeholder engagement + capital allocation (yield)."""
    # Competency: sustained compounding at modest vol.
    exp_ret = c.expected_return if c.expected_return is not None else 0.0
    vol = c.volatility if c.volatility and c.volatility > 0 else 0.25
    execution = (
        max(0.0, min(1.0, (exp_ret / vol + 0.5) / 1.5)) if c.expected_return is not None else 0.5
    )
    # Engagement / allocation: cash shared with holders.
    payout = 0.5
    if c.dividend_yield is not None:
        payout = max(0.0, min(1.0, c.dividend_yield / 0.04))
    base = 0.55 * execution + 0.45 * payout

    gov_hits = good_hits = 0
    urls: list[str] = []
    for it in items:
        text = f"{it.get('title') or ''} {it.get('snippet') or ''}".lower()
        gov_hits += sum(1 for w in _GOVERNANCE_BAD if w in text)
        good_hits += sum(1 for w in _STEWARDSHIP_GOOD if w in text)
        if it.get("url"):
            urls.append(it["url"])
    discount = min(0.45, 0.15 * gov_hits)
    uplift = min(0.10, 0.03 * good_hits)
    if news_tone == "high":
        discount += 0.10
    elif news_tone == "medium":
        discount += 0.05
    score = max(0.05, min(0.95, base - discount + uplift))
    bits = [f"execution {execution:.2f}", f"payout {payout:.2f}"]
    if items:
        bits.append(f"search {len(items)} hit(s): gov-{gov_hits}/stewardship+{good_hits}")
    else:
        bits.append("no search hits — track record from fundamentals only")
    return round(score, 4), "M: " + ", ".join(bits), urls[:4]


def valuation_score(
    c: Candidate,
    business: float = 0.5,
    management: float = 0.5,
) -> tuple[float, str]:
    """Reasonable valuation with a moat-adjusted premium — but never any price.

    Cheap P/E + yield wins outright. A strong business run by competent
    management (moat + steady cash flow) earns a reasonable premium: moderate
    P/E is forgiven, not punished. Fully-priced speculative growth (P/E>=35
    with no yield) stays capped — the "ready to let go" rule.
    """
    bits: list[str] = []
    score, adj = 0.5, 0.0
    if c.pe_ratio is not None and c.pe_ratio > 0:
        pe_part = max(0.0, min(1.0, (35.0 - c.pe_ratio) / (35.0 - 12.0)))
        adj += (pe_part - 0.5) * 0.7
        bits.append(f"P/E {c.pe_ratio:.1f}")
    if c.dividend_yield is not None:
        y_part = max(0.0, min(1.0, c.dividend_yield / 0.04))
        adj += (y_part - 0.5) * 0.3
        bits.append(f"yield {c.dividend_yield:.1%}")
    score += adj
    # Moat-adjusted premium: strong B+M forgives a moderate P/E (up to +0.15).
    moat_bonus = 0.0
    if (
        business >= 0.60
        and management >= 0.55
        and c.pe_ratio is not None
        and 12.0 <= c.pe_ratio <= 28.0
    ):
        moat_bonus = 0.15 * min(business, management)
        bits.append(f"moat premium +{moat_bonus:.2f}")
    score += moat_bonus
    # Any-price rejection: fully priced + no cash return stays capped.
    if c.pe_ratio is not None and c.pe_ratio >= 35.0 and (c.dividend_yield or 0.0) < 0.005:
        score = min(score, 0.30)
        bits.append("fully priced, no yield — capped (let go)")
    score = max(0.05, min(0.95, score))
    return round(score, 4), "V: " + (", ".join(bits) if bits else "no P/E/yield data — neutral")


_BMV_WEIGHTS = {"business": 0.40, "management": 0.35, "valuation": 0.25}
_BUSINESS_GATE = 0.35  # below this the idea is dropped regardless of M/V
_MANAGEMENT_GATE = 0.35  # below this the idea is dropped regardless of V


def gated_combined(b: float, m: float, v: float) -> tuple[float, str]:
    """Sequential BMV gate: B must pass before M matters, M before V matters."""
    if b < _BUSINESS_GATE:
        return 0.30, "gate: business fails (<0.35) — idea dropped"
    if m < _MANAGEMENT_GATE:
        return 0.40, "gate: management fails (<0.35) — idea dropped"
    blend = (
        b * _BMV_WEIGHTS["business"]
        + m * _BMV_WEIGHTS["management"]
        + v * _BMV_WEIGHTS["valuation"]
    )
    return round(max(0.05, min(0.95, blend)), 4), "gate: B+M pass — weighted blend"


# --------------------------------------------------------------------------- #
# Live evidence: governance / stewardship search (stock + sector)
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


def collect_bmv_evidence(
    universe: list[Candidate],
    symbol_to_name: dict[str, str] | None = None,
    max_names: int | None = None,
    max_results: int | None = None,
) -> tuple[dict[str, list[dict]], list[str]]:
    """Collect governance/stewardship hits per stock + conduct hits per sector.

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
            "DuckDuckGo backend (ddgs) not installed — management screen is fundamentals-only."
        ]
    n_names = max_names or _env_int("PM_BMV_MAX_NAMES", 15)
    n_res = max_results or _env_int("PM_BMV_MAX_RESULTS", 3)

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
                f"{sec} sector corporate governance scandal probe",
                max_results=n_res,
                fallback_query=f"{sec} sector governance",
            ) + web_search(
                f"{sec} industry management governance track record",
                max_results=2,
                fallback_query=f"{sec} governance",
            )
        except Exception as e:  # fail-soft per sector
            _log.warning("bmv[management]: sector search failed for %s (%s).", sec, e)
            sector_hits[sec] = []

    out: dict[str, list[dict]] = {}
    for c in ranked:
        name = _search_name(c.symbol, s2n)
        try:
            stock_hits = news_search(
                f"{name} company management governance pledge outlook",
                max_results=n_res,
                fallback_query=name,
            )
        except Exception as e:
            _log.warning("bmv[management]: stock search failed for %s (%s).", c.symbol, e)
            stock_hits = []
        sec_hits = sector_hits.get((c.sector or "").strip(), [])
        for h in stock_hits:
            h["level"] = "stock"
        for h in sec_hits:
            h.setdefault("level", "sector")
        out[c.symbol.upper()] = stock_hits + sec_hits
    covered = sum(1 for v in out.values() if v)
    _log.info(
        "bmv[management]: %d/%d names with search hits (%d sectors).",
        covered,
        len(out),
        len(sectors),
    )
    if covered == 0:
        gaps.append(
            "Search returned no hits for any scored name — management is fundamentals-only."
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
                f"You are the {agent} agent in a Kotak-style Business/Management/Valuation "
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
        _log.warning("bmv[%s]: LLM view failed (%s) — deterministic only.", agent, e)
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
def run_bmv(
    universe: list[Candidate],
    request: ConstructionRequest,
    news_tones: dict[str, str] | None = None,
    symbol_to_name: dict[str, str] | None = None,
    use_llm: bool = True,
    enable_search: bool = True,
    precollected: dict[str, list[dict]] | None = None,
) -> BmvReport:
    """Run the gated BMV sequence and combine scores deterministically.

    RULE: scores + stance are always deterministic (including the B->M->V
    gates). Live search evidence feeds the Management score and the LLM
    commentary; the LLM never changes a score. Set `enable_search=False`
    for a fully offline run.
    """
    tones = news_tones or {}
    evidence: dict[str, list[dict]] = precollected or {}
    gaps: list[str] = []
    if enable_search and not precollected:
        evidence, gaps = collect_bmv_evidence(universe, symbol_to_name)

    b_tbl = {c.symbol.upper(): business_score(c) for c in universe}
    m_tbl: dict[str, tuple[float, str, list[str]]] = {}
    for c in universe:
        sym = c.symbol.upper()
        m_tbl[sym] = management_from_evidence(c, evidence.get(sym, []), tones.get(sym))
    v_tbl: dict[str, tuple[float, str]] = {}
    gated: dict[str, tuple[float, str]] = {}
    for c in universe:
        sym = c.symbol.upper()
        v_tbl[sym] = valuation_score(c, business=b_tbl[sym][0], management=m_tbl[sym][0])
        gated[sym] = gated_combined(b_tbl[sym][0], m_tbl[sym][0], v_tbl[sym][0])

    # Portfolio stance from average gated score: resilient pipeline or not.
    avg_g = sum(v[0] for v in gated.values()) / max(1, len(gated))
    stance = "high_conviction" if avg_g >= 0.60 else "selective" if avg_g >= 0.40 else "cautious"

    det_comment: dict[str, dict[str, str]] = {
        "business": {s: v[1] for s, v in b_tbl.items()},
        "management": {s: v[1] for s, v in m_tbl.items()},
        "valuation": {s: v[1] for s, v in v_tbl.items()},
    }
    llm_comment: dict[str, dict[str, str]] = {}
    if use_llm and llm_available():
        digest = _mini_digest(universe, evidence, tones)
        llm_comment["business"] = _llm_commentary(
            "Business",
            request,
            digest,
            "Judge the business for a 5-10yr lens: above-GDP industries, "
            "outgrowing competitors, enduring moat, consolidating sectors win; "
            "fad themes lose.",
            stance,
        )
        # Management LLM sees the REAL governance search snippets.
        llm_comment["management"] = _llm_commentary(
            "Management",
            request,
            digest,
            "Judge management ONLY from the supplied stats and headlines: "
            "governance track record, execution, stakeholder engagement win; "
            "governance flags lose. Do not invent news.",
            stance,
        )
        llm_comment["valuation"] = _llm_commentary(
            "Valuation",
            request,
            digest,
            "Judge reasonable valuation with a moat premium: cheap wins, "
            "moderate P/E forgiven for moat + cash flow, "
            "fully-priced speculative growth loses.",
            stance,
        )

    def _view(agent: str, sym: str, fallback: str) -> str:
        return llm_comment.get(agent, {}).get(sym) or det_comment[agent].get(sym, fallback)

    combined: dict[str, BmvScores] = {}
    for c in universe:
        sym = c.symbol.upper()
        b, m = b_tbl[sym][0], m_tbl[sym][0]
        v, (g, gate_note) = v_tbl[sym][0], gated[sym]
        reasons = {
            "business": _view("business", sym, b_tbl[sym][1]),
            "management": _view("management", sym, m_tbl[sym][1]),
            "valuation": _view("valuation", sym, v_tbl[sym][1]),
        }
        if "fails" in gate_note or "dropped" in gate_note:
            reasons["gate"] = gate_note
        combined[sym] = BmvScores(
            business=b,
            management=m,
            valuation=v,
            combined=g,
            stance=stance,
            reasons=reasons,
            evidence_urls=m_tbl[sym][2],
        )
    covered = sum(1 for c in universe if evidence.get(c.symbol.upper()))
    coverage = round(covered / max(1, len(universe)), 3)
    summary = (
        f"BMV committee: {len(combined)} names scored, stance `{stance}` "
        f"(avg gated {avg_g:.2f}; search coverage {coverage:.0%}; weights "
        f"B{_BMV_WEIGHTS['business']:.0%}/M{_BMV_WEIGHTS['management']:.0%}/"
        f"V{_BMV_WEIGHTS['valuation']:.0%} with B/M gates)."
    )
    _log.info("bmv: %s", summary)
    views = {
        "business": f"Business screen over {len(combined)} names (growth + moat + consolidation).",
        "management": f"Management screen over {covered} searched name(s) + execution/yield.",
        "valuation": "Valuation screen (reasonable price, moat premium, any-price rejection).",
    }
    return BmvReport(
        scores=combined,
        stance=stance,
        search_coverage=coverage,
        agent_views=views,
        summary=summary,
        data_gaps=gaps,
    )

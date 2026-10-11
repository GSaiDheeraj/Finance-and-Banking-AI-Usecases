"""
LLM-augmented portfolio constructor (VLRT + VCST + SQL + OPCO-FINCO + BMV committees,
free selection, guardrails).

Up to five committees score every candidate deterministically before the
portfolio-manager LLM picks (framework selected on the request: 'vlrt' | 'vcst' |
'sql' | 'opcofinco' | 'bmv' | 'both' | 'all'):

  * VLRT (Quant-style) — Valuation, Liquidity, Risk Appetite, Timing.
  * VCST (ICICI-style) — Valuation, Cycle, Sentiment (from LIVE stock +
    industry/sector search), Trigger (catalyst watch-items from the same evidence).
  * SQL (Motilal Oswal-style) — Size of opportunity x headroom, Quality
    (business x management, multiplicative), Longevity of compounding.
  * OPCO-FINCO (WhiteOak-style) — OpCo (capital-light excess returns), FinCo
    (capital discipline), Business (great-business screen with LIVE
    governance/disruption search), Value (discount to intrinsic value).
  * BMV (Kotak-style) — Business, Management (LIVE governance search),
    Valuation, run as a SEQUENTIAL gate (B must pass before M matters, M
    before V; failures cap the combined score and are named).

The LLM builds the portfolio USING those scores and must give a reason per pick
per active framework. It is NOT trusted with the numbers: its proposal
passes through the same deterministic `guardrails` as the rule-based constructor,
and quantities + expected metrics are recomputed deterministically. If the
LLM/library is unavailable, this falls back to the deterministic constructor.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

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
from .bmv_agents import run_bmv
from .deterministic import construct_deterministic
from .opcofinco_agents import run_opcofinco
from .sql_agents import run_sql
from .vcst_agents import run_vcst
from .vlrt_agents import run_vlrt

_log = get_logger()


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _news_tone_by_symbol(scan: MarketRiskScan | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not scan:
        return out
    for f in scan.findings:
        if f.scope == "position" and f.subject:
            out[f.subject.upper()] = f.severity
    return out


def _framework_of(request: ConstructionRequest) -> str:
    fw = (getattr(request, "framework", "both") or "both").lower()
    return fw if fw in ("vlrt", "vcst", "sql", "opcofinco", "bmv", "both", "all") else "both"


def _use_committees(fw: str) -> tuple[bool, bool, bool, bool, bool]:
    """Return (use_vlrt, use_vcst, use_sql, use_of, use_bmv) for a framework value."""
    if fw == "all":
        return True, True, True, True, True
    if fw == "both":  # legacy: VLRT + VCST
        return True, True, False, False, False
    return fw == "vlrt", fw == "vcst", fw == "sql", fw == "opcofinco", fw == "bmv"


def _universe_digest(
    universe: list[Candidate],
    scan: MarketRiskScan | None,
    vlrt: dict[str, dict] | None = None,
    vcst: dict[str, dict] | None = None,
    sql: dict[str, dict] | None = None,
    of: dict[str, dict] | None = None,
    bmv: dict[str, dict] | None = None,
) -> list[dict]:
    tone = _news_tone_by_symbol(scan)
    digest = []
    for c in universe:
        row = {
            "symbol": c.symbol,
            "name": c.name,
            "asset_class": c.asset_class.value,
            "sector": c.sector,
            "region": c.region.value,
            "volatility": round(c.volatility, 4) if c.volatility is not None else None,
            "dividend_yield": round(c.dividend_yield, 4) if c.dividend_yield is not None else None,
            "pe_ratio": round(c.pe_ratio, 2) if c.pe_ratio is not None else None,
            "market_cap": c.market_cap.value,
            "expected_return": round(c.expected_return, 4)
            if c.expected_return is not None
            else None,
            "has_price": c.has_market_data,
            "news_risk": tone.get(c.symbol.upper()),
        }
        if vlrt and c.symbol.upper() in vlrt:
            row["vlrt"] = vlrt[c.symbol.upper()]
        if vcst and c.symbol.upper() in vcst:
            row["vcst"] = vcst[c.symbol.upper()]
        if sql and c.symbol.upper() in sql:
            row["sql"] = sql[c.symbol.upper()]
        if of and c.symbol.upper() in of:
            row["of"] = of[c.symbol.upper()]
        if bmv and c.symbol.upper() in bmv:
            row["bmv"] = bmv[c.symbol.upper()]
        digest.append(row)
    # Highest average committee score first so the LLM sees the ranking.
    if vlrt or vcst or sql or of or bmv:

        def _rank(r: dict) -> float:
            parts = []
            if vlrt and isinstance(r.get("vlrt"), dict):
                parts.append((r["vlrt"] or {}).get("combined", 0.5))
            if vcst and isinstance(r.get("vcst"), dict):
                parts.append((r["vcst"] or {}).get("combined", 0.5))
            if sql and isinstance(r.get("sql"), dict):
                parts.append((r["sql"] or {}).get("combined", 0.5))
            if of and isinstance(r.get("of"), dict):
                parts.append((r["of"] or {}).get("combined", 0.5))
            if bmv and isinstance(r.get("bmv"), dict):
                parts.append((r["bmv"] or {}).get("combined", 0.5))
            return sum(parts) / len(parts) if parts else 0.5

        digest.sort(key=_rank, reverse=True)
    return digest


def construct_llm(
    request: ConstructionRequest,
    universe: list[Candidate],
    risk_scan: MarketRiskScan | None = None,
) -> ConstructedPortfolio:
    """Build the LLM-advised portfolio, then enforce hard constraints deterministically."""
    fw = _framework_of(request)
    use_vlrt, use_vcst, use_sql, use_of, use_bmv = _use_committees(fw)
    if not llm_available():
        _log.warning("construct[llm]: LLM not configured — deterministic committee fallback.")
        cp = construct_deterministic(request, universe)
        cp.method = "llm"
        bits = []
        if use_vlrt:
            _fb = run_vlrt(
                universe, request, news_tones=_news_tone_by_symbol(risk_scan), use_llm=False
            )
            cp.vlrt_regime = _fb.regime
            cp.vlrt_summary = _fb.summary + " (deterministic fallback — LLM unavailable)."
            for p in cp.positions:
                det = _fb.scores.get(p.symbol.upper())
                if det is not None:
                    p.vlrt_scores = {
                        "valuation": det.valuation,
                        "liquidity": det.liquidity,
                        "risk_appetite": det.risk_appetite,
                        "timing": det.timing,
                        "combined": det.combined,
                    }
                    p.vlrt_reason = "; ".join(det.reasons.values())[:600]
                    if p.vlrt_reason and p.vlrt_reason not in p.rationale:
                        p.rationale = (p.rationale + f" [VLRT: {p.vlrt_reason}]")[:600]
            bits.append(f"VLRT regime `{_fb.regime}`")
        if use_vcst:
            _s2n_fb = {c.symbol.upper(): c.name for c in universe if c.name}
            _vfb = run_vcst(
                universe,
                request,
                symbol_to_name=_s2n_fb,
                cycle_stance=getattr(request, "cycle_stance", "neutral"),
                use_llm=False,
                enable_search=getattr(request, "enable_vcst_search", True),
            )
            cp.vcst_stance = _vfb.stance
            cp.vcst_summary = _vfb.summary + " (deterministic fallback — LLM unavailable.)"
            for p in cp.positions:
                det = _vfb.scores.get(p.symbol.upper())
                if det is not None:
                    p.vcst_scores = {
                        "valuation": det.valuation,
                        "cycle": det.cycle,
                        "sentiment": det.sentiment,
                        "trigger": det.trigger,
                        "combined": det.combined,
                    }
                    if not p.vcst_reason:
                        p.vcst_reason = "; ".join(det.reasons.values())[:600]
            bits.append(f"VCST stance `{_vfb.stance}`")
        if use_sql:
            _sfb = run_sql(
                universe, request, news_tones=_news_tone_by_symbol(risk_scan), use_llm=False
            )
            cp.sql_stance = _sfb.stance
            cp.sql_summary = _sfb.summary + " (deterministic fallback — LLM unavailable.)"
            for p in cp.positions:
                det = _sfb.scores.get(p.symbol.upper())
                if det is not None:
                    p.sql_scores = {
                        "size": det.size,
                        "quality": det.quality,
                        "longevity": det.longevity,
                        "combined": det.combined,
                    }
                    if not p.sql_reason:
                        p.sql_reason = "; ".join(det.reasons.values())[:600]
            bits.append(f"SQL stance `{_sfb.stance}`")
        if use_of:
            _s2n_of = {c.symbol.upper(): c.name for c in universe if c.name}
            _ofb = run_opcofinco(
                universe,
                request,
                news_tones=_news_tone_by_symbol(risk_scan),
                symbol_to_name=_s2n_of,
                use_llm=False,
                enable_search=getattr(request, "enable_of_search", True),
            )
            cp.of_stance = _ofb.stance
            cp.of_summary = _ofb.summary + " (deterministic fallback — LLM unavailable.)"
            for p in cp.positions:
                det = _ofb.scores.get(p.symbol.upper())
                if det is not None:
                    p.of_scores = {
                        "opco": det.opco,
                        "finco": det.finco,
                        "business": det.business,
                        "value": det.value,
                        "combined": det.combined,
                    }
                    if not p.of_reason:
                        p.of_reason = "; ".join(det.reasons.values())[:600]
            bits.append(f"OPCO-FINCO stance `{_ofb.stance}`")
        if use_bmv:
            _s2n_bmv = {c.symbol.upper(): c.name for c in universe if c.name}
            _bfb = run_bmv(
                universe,
                request,
                news_tones=_news_tone_by_symbol(risk_scan),
                symbol_to_name=_s2n_bmv,
                use_llm=False,
                enable_search=getattr(request, "enable_bmv_search", True),
            )
            cp.bmv_stance = _bfb.stance
            cp.bmv_summary = _bfb.summary + " (deterministic fallback — LLM unavailable.)"
            for p in cp.positions:
                det = _bfb.scores.get(p.symbol.upper())
                if det is not None:
                    p.bmv_scores = {
                        "business": det.business,
                        "management": det.management,
                        "valuation": det.valuation,
                        "combined": det.combined,
                    }
                    if not p.bmv_reason:
                        p.bmv_reason = "; ".join(det.reasons.values())[:600]
            bits.append(f"BMV stance `{_bfb.stance}`")
        cp.narrative = (
            "LLM not configured (set LLM_ENDPOINT / LLM_API_KEY). "
            + ("Committees ran deterministically (" + ", ".join(bits) + "). " if bits else "")
            + "Fell back to the deterministic construction. "
        ) + cp.narrative
        cp.guardrail_corrections.insert(
            0, "LLM unavailable — deterministic committee fallback used."
        )
        return cp

    saa = target_allocation(request.mandate)
    saa_str = ", ".join(f"{k} {v:.0%}" for k, v in saa.items() if v > 0)
    cons = request.constraints

    # --- Committees (RULE scores, always computed; LLM views when available) ---
    tones = _news_tone_by_symbol(risk_scan)
    vlrt_report = vcst_report = sql_report = of_report = bmv_report = None
    vlrt_flat = vcst_flat = sql_flat = of_flat = bmv_flat = None
    if use_vlrt:
        vlrt_report = run_vlrt(universe, request, news_tones=tones, use_llm=True)
        vlrt_flat = {
            sym: {
                "valuation": round(s.valuation, 3),
                "liquidity": round(s.liquidity, 3),
                "risk_appetite": round(s.risk_appetite, 3),
                "timing": round(s.timing, 3),
                "combined": round(s.combined, 3),
                "views": s.reasons,
            }
            for sym, s in vlrt_report.scores.items()
        }
    if use_vcst:
        s2n = {c.symbol.upper(): c.name for c in universe if c.name}
        vcst_report = run_vcst(
            universe,
            request,
            symbol_to_name=s2n,
            cycle_stance=getattr(request, "cycle_stance", "neutral"),
            use_llm=True,
            enable_search=getattr(request, "enable_vcst_search", True),
        )
        vcst_flat = {
            sym: {
                "valuation": round(s.valuation, 3),
                "cycle": round(s.cycle, 3),
                "sentiment": round(s.sentiment, 3),
                "trigger": round(s.trigger, 3),
                "combined": round(s.combined, 3),
                "views": s.reasons,
                "evidence": s.evidence_urls[:2],
            }
            for sym, s in vcst_report.scores.items()
        }
    if use_sql:
        sql_report = run_sql(universe, request, news_tones=tones, use_llm=True)
        sql_flat = {
            sym: {
                "size": round(s.size, 3),
                "quality": round(s.quality, 3),
                "longevity": round(s.longevity, 3),
                "combined": round(s.combined, 3),
                "views": s.reasons,
            }
            for sym, s in sql_report.scores.items()
        }
    if use_of:
        s2n_of = {c.symbol.upper(): c.name for c in universe if c.name}
        of_report = run_opcofinco(
            universe,
            request,
            news_tones=tones,
            symbol_to_name=s2n_of,
            use_llm=True,
            enable_search=getattr(request, "enable_of_search", True),
        )
        of_flat = {
            sym: {
                "opco": round(s.opco, 3),
                "finco": round(s.finco, 3),
                "business": round(s.business, 3),
                "value": round(s.value, 3),
                "combined": round(s.combined, 3),
                "views": s.reasons,
                "evidence": s.evidence_urls[:2],
            }
            for sym, s in of_report.scores.items()
        }
    if use_bmv:
        s2n_bmv = {c.symbol.upper(): c.name for c in universe if c.name}
        bmv_report = run_bmv(
            universe,
            request,
            news_tones=tones,
            symbol_to_name=s2n_bmv,
            use_llm=True,
            enable_search=getattr(request, "enable_bmv_search", True),
        )
        bmv_flat = {
            sym: {
                "business": round(s.business, 3),
                "management": round(s.management, 3),
                "valuation": round(s.valuation, 3),
                "combined": round(s.combined, 3),
                "views": s.reasons,
                "evidence": s.evidence_urls[:2],
            }
            for sym, s in bmv_report.scores.items()
        }
    digest = _universe_digest(
        universe, risk_scan, vlrt_flat, vcst_flat, sql_flat, of_flat, bmv_flat
    )

    # investor-profile context for the model
    profile_bits = [f"style: {request.style.value}"]
    if request.countries:
        profile_bits.append(
            f"countries: {', '.join(request.countries)} ({request.geography_mode.value})"
        )
    if request.equity_pct is not None or request.debt_pct is not None:
        profile_bits.append(
            f"equity/debt split: {request.equity_pct or '—'}/{request.debt_pct or '—'}"
        )
    if request.cap_mix:
        profile_bits.append(
            "cap mix: " + ", ".join(f"{k} {v:.0%}" for k, v in request.cap_mix.items())
        )
    if request.theme:
        profile_bits.append(f"theme: {request.theme}")
    if request.instrument_types:
        profile_bits.append("instruments: " + ", ".join(i.value for i in request.instrument_types))
    target, _basis = implied_target_annual(request)
    if target is not None:
        profile_bits.append(f"return target: ~{target:.1%} p.a.")
    profile_str = "; ".join(profile_bits)

    committee_bits = []
    if vlrt_report is not None:
        committee_bits.append(
            f"VLRT READ — regime `{vlrt_report.regime}`: {vlrt_report.summary} "
            "Per-candidate `vlrt` 0..1: Valuation = cheap P/E + yield wins; Liquidity = "
            "large-cap priced vehicles win; RiskAppetite = vol + news vs tolerance; "
            "Timing = momentum now (risk_on favours drift, risk_off defensive)."
        )
    if vcst_report is not None:
        committee_bits.append(
            f"VCST READ — stance `{vcst_report.stance}` (cycle {vcst_report.cycle_stance}): "
            f"{vcst_report.summary} Per-candidate `vcst` 0..1: Valuation = cheap wins; "
            "Cycle = cyclicals suit weak cycles, defensives suit strong ones; Sentiment = "
            "live stock + sector search, contrarian (fear is opportunity, euphoria is "
            "take-profit — `views.sentiment` + `evidence` show why); Trigger = named "
            "near-term catalyst to watch."
        )
    if sql_report is not None:
        committee_bits.append(
            f"SQL READ — stance `{sql_report.stance}`: {sql_report.summary} "
            "Per-candidate `sql` 0..1: Size = opportunity size x headroom (mid/small caps "
            "in huge sectors win); Quality = business x management, multiplicative "
            "(stable dividend-payers with clean news win); Longevity = sustained low-vol "
            "compounding in durable sectors and vehicles wins."
        )
    if of_report is not None:
        committee_bits.append(
            f"OPCO-FINCO READ — stance `{of_report.stance}`: {of_report.summary} "
            "Per-candidate `of` 0..1: OpCo = capital-light excess returns win; FinCo = "
            "yield + funded scale win, trapped cash loses; Business = well managed + "
            "scalable with clean governance/disruption search (see `views.business` + "
            "`evidence`), avoid poor governance and obsolescence risk; Value = substantial "
            "discount to intrinsic value, margin of safety."
        )
    if bmv_report is not None:
        committee_bits.append(
            f"BMV READ — stance `{bmv_report.stance}`: {bmv_report.summary} "
            "Per-candidate `bmv` 0..1 (SEQUENTIAL gate — a failed leg caps combined): "
            "Business = above-GDP industries, outgrowing competitors, enduring moat, "
            "consolidating sectors (5–10yr lens); Management = governance track record + "
            "execution + stakeholder engagement from live search (see `views.management` + "
            "`evidence`); Valuation = reasonable price with a moat premium, never any price."
        )
    committee_str = " ".join(committee_bits)
    per_pos_schema = []
    if vlrt_report is not None:
        per_pos_schema.append(
            '"vlrt": {"valuation": str, "liquidity": str, "risk_appetite": str, "timing": str}'
        )
    if vcst_report is not None:
        per_pos_schema.append(
            '"vcst": {"valuation": str, "cycle": str, "sentiment": str, "trigger": str}'
        )
    if sql_report is not None:
        per_pos_schema.append('"sql": {"size": str, "quality": str, "longevity": str}')
    if of_report is not None:
        per_pos_schema.append('"of": {"opco": str, "finco": str, "business": str, "value": str}')
    if bmv_report is not None:
        per_pos_schema.append('"bmv": {"business": str, "management": str, "valuation": str}')
    per_pos_extra = (", " + ", ".join(per_pos_schema)) if per_pos_schema else ""
    per_pos_hint = " PLUS a vlrt object" if vlrt_report is not None else ""
    if vcst_report is not None:
        per_pos_hint += (" and" if per_pos_hint else " PLUS") + " a vcst object"
    if sql_report is not None:
        per_pos_hint += (" and" if per_pos_hint else " PLUS") + " a sql object"
    if of_report is not None:
        per_pos_hint += (" and" if per_pos_hint else " PLUS") + " an of object"
    if bmv_report is not None:
        per_pos_hint += (" and" if per_pos_hint else " PLUS") + " a bmv object"
    per_pos_hint += " with one line per dimension" if per_pos_schema else ""

    system = SystemMessage(
        content=(
            "You are a portfolio manager constructing a portfolio from a candidate universe for a "
            f"{request.mandate} mandate (risk tolerance: {request.risk_tolerance}, horizon "
            f"{request.horizon_years}y).\n"
            f"INVESTOR PROFILE — respect it: {profile_str}.\n"
            f"{committee_str} "
            "Build the portfolio USING these committee scores — favour high-combined names, "
            "explain any low-score inclusion explicitly.\n"
            "HARD RULES (also enforced after you): weights are fractions that sum to ~1.0; "
            f"no single security above {cons.single_name_cap:.0%}; "
            f"no sector above {cons.sector_cap:.0%}; do "
            f"not pick excluded names {cons.exclusions}; keep the asset-class mix near {saa_str} "
            f"not pick excluded names {cons.exclusions}; keep the asset-class mix near {saa_str} "
            "unless the investor's equity/debt split overrides it.\n"
            "If the investor's return target looks unrealistic for this universe, say so in the "
            f"narrative. For EACH selected security give a concise reason{per_pos_hint}. Use ONLY "
            "the supplied candidates and their stats — do not invent tickers or numbers. "
            "Output ONLY JSON:\n"
            '{"positions": [{"symbol": str, "weight": number, "reason": str'
            f"{per_pos_extra}" + "}], "
            '"narrative": str}'
        )
    )
    user = HumanMessage(
        content=(
            f"Investable amount: {request.invest_amount:,.0f} {request.base_currency}.\n"
            f"Candidate universe (with deterministic stats):\n{json.dumps(digest, indent=2)}\n\n"
            "Return your portfolio JSON now."
        )
    )

    try:
        raw = get_llm().invoke([system, user]).content
        parsed = json.loads(_strip_json(raw))
    except Exception as e:
        _log.warning("construct[llm]: LLM call/parse failed (%s) — deterministic fallback.", e)
        cp = construct_deterministic(request, universe)
        cp.method = "llm"
        if vlrt_report is not None:
            cp.vlrt_regime, cp.vlrt_summary = vlrt_report.regime, vlrt_report.summary
        if vcst_report is not None:
            cp.vcst_stance, cp.vcst_summary = vcst_report.stance, vcst_report.summary
        if sql_report is not None:
            cp.sql_stance, cp.sql_summary = sql_report.stance, sql_report.summary
        if of_report is not None:
            cp.of_stance, cp.of_summary = of_report.stance, of_report.summary
        if bmv_report is not None:
            cp.bmv_stance, cp.bmv_summary = bmv_report.stance, bmv_report.summary
        cp.guardrail_corrections.insert(0, f"LLM call failed ({type(e).__name__}) — fallback.")
        return cp

    def _committee_lines(
        sym: str, p: dict
    ) -> tuple[
        str,
        dict[str, float],
        str,
        dict[str, float],
        str,
        dict[str, float],
        str,
        dict[str, float],
        str,
        dict[str, float],
    ]:
        """Return (vlrt_txt, vlrt_scores, vcst_txt, vcst_scores, sql_txt, sql_scores,
        of_txt, of_scores, bmv_txt, bmv_scores)."""
        vlrt_txt, vcst_txt, sql_txt, of_txt, bmv_txt = "", "", "", "", ""
        vlrt_sc: dict[str, float] = {}
        vcst_sc: dict[str, float] = {}
        sql_sc: dict[str, float] = {}
        of_sc: dict[str, float] = {}
        bmv_sc: dict[str, float] = {}
        if vlrt_report is not None:
            det = vlrt_report.scores.get(sym)
            v_obj = p.get("vlrt") if isinstance(p.get("vlrt"), dict) else {}
            v_lines = []
            for dim in ("valuation", "liquidity", "risk_appetite", "timing"):
                line = str(v_obj.get(dim, "") or "").strip()[:160]
                if not line and det is not None:
                    line = det.reasons.get(dim, "")
                if line:
                    v_lines.append(f"{dim[0].upper()}: {line}")
            if v_lines:
                vlrt_txt = " [VLRT: " + " | ".join(v_lines) + "]"
            if det is not None:
                vlrt_sc = {
                    "valuation": det.valuation,
                    "liquidity": det.liquidity,
                    "risk_appetite": det.risk_appetite,
                    "timing": det.timing,
                    "combined": det.combined,
                }
        if vcst_report is not None:
            det = vcst_report.scores.get(sym)
            v_obj = p.get("vcst") if isinstance(p.get("vcst"), dict) else {}
            v_lines = []
            for dim in ("valuation", "cycle", "sentiment", "trigger"):
                line = str(v_obj.get(dim, "") or "").strip()[:160]
                if not line and det is not None:
                    line = det.reasons.get(dim, "")
                if line:
                    v_lines.append(f"{dim[0].upper()}: {line}")
            if v_lines:
                vcst_txt = " [VCST: " + " | ".join(v_lines) + "]"
            if det is not None:
                vcst_sc = {
                    "valuation": det.valuation,
                    "cycle": det.cycle,
                    "sentiment": det.sentiment,
                    "trigger": det.trigger,
                    "combined": det.combined,
                }
        if sql_report is not None:
            det = sql_report.scores.get(sym)
            v_obj = p.get("sql") if isinstance(p.get("sql"), dict) else {}
            v_lines = []
            for dim in ("size", "quality", "longevity"):
                line = str(v_obj.get(dim, "") or "").strip()[:160]
                if not line and det is not None:
                    line = det.reasons.get(dim, "")
                if line:
                    v_lines.append(f"{dim[0].upper()}: {line}")
            if v_lines:
                sql_txt = " [SQL: " + " | ".join(v_lines) + "]"
            if det is not None:
                sql_sc = {
                    "size": det.size,
                    "quality": det.quality,
                    "longevity": det.longevity,
                    "combined": det.combined,
                }
        if of_report is not None:
            det = of_report.scores.get(sym)
            v_obj = p.get("of") if isinstance(p.get("of"), dict) else {}
            v_lines = []
            for dim in ("opco", "finco", "business", "value"):
                line = str(v_obj.get(dim, "") or "").strip()[:160]
                if not line and det is not None:
                    line = det.reasons.get(dim, "")
                if line:
                    v_lines.append(f"{dim[0].upper()}: {line}")
            if v_lines:
                of_txt = " [OF: " + " | ".join(v_lines) + "]"
            if det is not None:
                of_sc = {
                    "opco": det.opco,
                    "finco": det.finco,
                    "business": det.business,
                    "value": det.value,
                    "combined": det.combined,
                }
        if bmv_report is not None:
            det = bmv_report.scores.get(sym)
            v_obj = p.get("bmv") if isinstance(p.get("bmv"), dict) else {}
            v_lines = []
            for dim in ("business", "management", "valuation"):
                line = str(v_obj.get(dim, "") or "").strip()[:160]
                if not line and det is not None:
                    line = det.reasons.get(dim, "")
                if line:
                    v_lines.append(f"{dim[0].upper()}: {line}")
            if det is not None and det.reasons.get("gate"):
                v_lines.append(f"G: {det.reasons['gate'][:120]}")
            if v_lines:
                bmv_txt = " [BMV: " + " | ".join(v_lines) + "]"
            if det is not None:
                bmv_sc = {
                    "business": det.business,
                    "management": det.management,
                    "valuation": det.valuation,
                    "combined": det.combined,
                }
        return (
            vlrt_txt,
            vlrt_sc,
            vcst_txt,
            vcst_sc,
            sql_txt,
            sql_sc,
            of_txt,
            of_sc,
            bmv_txt,
            bmv_sc,
        )

    proposed: dict[str, float] = {}
    rationale: dict[str, str] = {}
    vlrt_reason_by_sym: dict[str, str] = {}
    vlrt_scores_by_sym: dict[str, dict[str, float]] = {}
    vcst_reason_by_sym: dict[str, str] = {}
    vcst_scores_by_sym: dict[str, dict[str, float]] = {}
    sql_reason_by_sym: dict[str, str] = {}
    sql_scores_by_sym: dict[str, dict[str, float]] = {}
    of_reason_by_sym: dict[str, str] = {}
    of_scores_by_sym: dict[str, dict[str, float]] = {}
    bmv_reason_by_sym: dict[str, str] = {}
    bmv_scores_by_sym: dict[str, dict[str, float]] = {}
    valid_syms = {c.symbol.upper() for c in universe}
    for p in parsed.get("positions") or []:
        sym = str(p.get("symbol", "")).strip().upper()
        w = p.get("weight")
        if sym in valid_syms and isinstance(w, (int, float)) and w > 0:
            proposed[sym] = proposed.get(sym, 0.0) + float(w)
            base_reason = str(p.get("reason", ""))[:300]
            # Per-position committee lines: prefer the LLM's own words, fall back to
            # the deterministic committee views so every holding always has one.
            (
                vlrt_txt,
                vlrt_sc,
                vcst_txt,
                vcst_sc,
                sql_txt,
                sql_sc,
                of_txt,
                of_sc,
                bmv_txt,
                bmv_sc,
            ) = _committee_lines(sym, p)
            rationale[sym] = (base_reason + vlrt_txt + vcst_txt + sql_txt + of_txt + bmv_txt)[:1400]
            if vlrt_txt:
                vlrt_reason_by_sym[sym] = vlrt_txt[:600]
            if vlrt_sc:
                vlrt_scores_by_sym[sym] = vlrt_sc
            if vcst_txt:
                vcst_reason_by_sym[sym] = vcst_txt[:600]
            if vcst_sc:
                vcst_scores_by_sym[sym] = vcst_sc
            if sql_txt:
                sql_reason_by_sym[sym] = sql_txt[:600]
            if sql_sc:
                sql_scores_by_sym[sym] = sql_sc
            if of_txt:
                of_reason_by_sym[sym] = of_txt[:600]
            if of_sc:
                of_scores_by_sym[sym] = of_sc
            if bmv_txt:
                bmv_reason_by_sym[sym] = bmv_txt[:600]
            if bmv_sc:
                bmv_scores_by_sym[sym] = bmv_sc
    llm_narrative = str(parsed.get("narrative", ""))[:1200]

    corrections: list[str] = []
    if not proposed:
        _log.warning("construct[llm]: LLM returned no usable positions — deterministic fallback.")
        cp = construct_deterministic(request, universe)
        cp.method = "llm"
        if vlrt_report is not None:
            cp.vlrt_regime, cp.vlrt_summary = vlrt_report.regime, vlrt_report.summary
        if vcst_report is not None:
            cp.vcst_stance, cp.vcst_summary = vcst_report.stance, vcst_report.summary
        if sql_report is not None:
            cp.sql_stance, cp.sql_summary = sql_report.stance, sql_report.summary
        if of_report is not None:
            cp.of_stance, cp.of_summary = of_report.stance, of_report.summary
        if bmv_report is not None:
            cp.bmv_stance, cp.bmv_summary = bmv_report.stance, bmv_report.summary
        cp.guardrail_corrections.insert(0, "LLM returned no usable positions — fallback.")
        return cp

    # record what the LLM proposed before guardrails, for transparency
    n_proposed = len(proposed)
    meta: dict[str, Candidate] = {c.symbol.upper(): c for c in universe}
    guardrailed, guard_corr = guardrails.enforce(proposed, meta, request.constraints)
    corrections.extend(guard_corr)

    positions, invested, cash = guardrails.finalise_positions(
        guardrailed, meta, request.invest_amount, rationale
    )
    metrics = common.compute_expected_metrics(positions, request.mandate, request.base_currency)
    feasibility = assess_feasibility(request, positions, [c.expected_return for c in universe])

    # Attach deterministic committee scores/reasons to the final positions (audit trail).
    for p in positions:
        sym = p.symbol.upper()
        if sym in vlrt_scores_by_sym:
            p.vlrt_scores = vlrt_scores_by_sym[sym]
        if sym in vlrt_reason_by_sym:
            p.vlrt_reason = vlrt_reason_by_sym[sym]
        if sym in vcst_scores_by_sym:
            p.vcst_scores = vcst_scores_by_sym[sym]
        if sym in vcst_reason_by_sym:
            p.vcst_reason = vcst_reason_by_sym[sym]
        if sym in sql_scores_by_sym:
            p.sql_scores = sql_scores_by_sym[sym]
        if sym in sql_reason_by_sym:
            p.sql_reason = sql_reason_by_sym[sym]
        if sym in of_scores_by_sym:
            p.of_scores = of_scores_by_sym[sym]
        if sym in of_reason_by_sym:
            p.of_reason = of_reason_by_sym[sym]
        if sym in bmv_scores_by_sym:
            p.bmv_scores = bmv_scores_by_sym[sym]
        if sym in bmv_reason_by_sym:
            p.bmv_reason = bmv_reason_by_sym[sym]

    committee_narr = " ".join(
        (
            [f"VLRT regime `{vlrt_report.regime}` — {vlrt_report.summary}"]
            if vlrt_report is not None
            else []
        )
        + (
            [f"VCST stance `{vcst_report.stance}` — {vcst_report.summary}"]
            if vcst_report is not None
            else []
        )
        + (
            [f"SQL stance `{sql_report.stance}` — {sql_report.summary}"]
            if sql_report is not None
            else []
        )
        + (
            [f"OPCO-FINCO stance `{of_report.stance}` — {of_report.summary}"]
            if of_report is not None
            else []
        )
        + (
            [f"BMV stance `{bmv_report.stance}` — {bmv_report.summary}"]
            if bmv_report is not None
            else []
        )
    )
    narrative = (
        f"LLM-advised ({fw.upper()}) construction for a {request.mandate} mandate "
        f"({request.style.value}). {committee_narr} "
        f"The model proposed {n_proposed} position(s); after deterministic guardrails the book "
        f"holds {len(positions)} position(s). "
        + (llm_narrative or "")
        + (" " + feasibility.message if feasibility and feasibility.message else "")
    )
    _log.info(
        "construct[llm+%s]: proposed=%d final=%d invested=%.0f corrections=%d",
        fw,
        n_proposed,
        len(positions),
        invested,
        len(corrections),
    )

    return ConstructedPortfolio(
        method="llm",
        weighting_method=request.weighting_method,
        style=request.style,
        countries=request.countries,
        positions=positions,
        sleeve_allocation=common.sleeve_allocation(positions),
        cap_allocation=common.cap_allocation(positions),
        country_allocation=common.country_allocation(positions),
        expected_metrics=metrics,
        feasibility=feasibility,
        excluded=[],
        guardrail_corrections=corrections,
        narrative=narrative,
        total_invested=invested,
        cash_residual=cash,
        vlrt_regime=vlrt_report.regime if vlrt_report is not None else "neutral",
        vlrt_summary=vlrt_report.summary if vlrt_report is not None else "",
        vcst_stance=vcst_report.stance if vcst_report is not None else "neutral",
        vcst_summary=vcst_report.summary if vcst_report is not None else "",
        sql_stance=sql_report.stance if sql_report is not None else "selective",
        sql_summary=sql_report.summary if sql_report is not None else "",
        of_stance=of_report.stance if of_report is not None else "selective",
        of_summary=of_report.summary if of_report is not None else "",
        bmv_stance=bmv_report.stance if bmv_report is not None else "selective",
        bmv_summary=bmv_report.summary if bmv_report is not None else "",
    )

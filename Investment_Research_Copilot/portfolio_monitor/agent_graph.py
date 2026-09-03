"""
Orchestration for portfolio monitoring and portfolio construction.

Two entry points, both returning a result object plus a human-readable reasoning trace and
run metrics:

  run_monitoring(...)   — value a live portfolio, check it against the IPS, reconcile cash
                          flows, compute risk, scan emerging risks, raise alerts, score it,
                          and write the briefing (deterministic and/or grounded LLM).
  run_construction(...) — build a portfolio from scratch for a mandate, deterministically
                          and/or via the LLM advisor (guardrailed), and compare the two.

Design: the LLM is used for *language* only (emerging-risk classification, the briefing,
construction reasoning, Q&A). Every decision-bearing number — allocation, drift, rebalancing
trades, cash-flow variances, risk metrics, limit breaches, alerts, the score, and all hard
constraints — is deterministic Python. Market-data and news steps fail soft.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .alerts import build_alerts
from .allocation import asset_class_weights, compute_allocation, price_holdings
from .cashflow import analyse_cash_flows
from .compare import compare_constructions
from .config import llm_available
from .drift import build_rebalance_plan, compute_drift
from .ingest import build_portfolio, load_cash_flows, load_holdings
from .logconf import get_logger
from .marketdata import get_histories, get_price_history, get_quotes, market_data_available
from .monitor.deterministic import build_briefing as build_briefing_det
from .monitor.llm_briefing import answer_question, build_briefing_llm
from .reference import benchmark_symbol
from .risk import check_limits, compute_risk_metrics
from .schemas import (
    AllocationBreakdown,
    AllocationBucket,
    CashFlow,
    ConstructedPortfolio,
    ConstructionRequest,
    ConstructionResult,
    CountryResearchProfile,
    Holding,
    MarketRiskScan,
    MonitoringResult,
    PortfolioInput,
)
from .scoring import score_portfolio

_log = get_logger()


# --------------------------------------------------------------------------- #
# Monitoring
# --------------------------------------------------------------------------- #
def assess_portfolio(
    portfolio: PortfolioInput,
    mode: str = "both",
    enable_news: bool = True,
    enable_country_research: bool = True,
) -> MonitoringResult:
    """Run the full deterministic monitoring engine over a portfolio, plus briefing(s)."""
    symbols = [h.symbol for h in portfolio.holdings]
    quotes = get_quotes(symbols) if market_data_available() else {}
    histories = get_histories(symbols) if market_data_available() else {}
    bench_hist = (get_price_history(benchmark_symbol(portfolio.mandate))
                  if market_data_available() else [])

    # 1. price + allocation (deterministic)
    price_holdings(portfolio.holdings, quotes)
    allocation = compute_allocation(portfolio.holdings, portfolio.base_currency)

    # 2. drift vs IPS + rebalancing (deterministic)
    drift = compute_drift(asset_class_weights(allocation), portfolio.mandate)
    rebalance = build_rebalance_plan(drift, allocation.total_market_value)

    # 3. cash-flow variance (deterministic)
    cashflow = analyse_cash_flows(portfolio.holdings, portfolio.cash_flows, quotes)

    # 4. risk metrics + limit checks (deterministic)
    risk_metrics = compute_risk_metrics(
        portfolio.holdings, allocation, histories, bench_hist, quotes)
    limit_checks = check_limits(risk_metrics, allocation, portfolio.mandate)

    # 5. emerging-risk scan (LLM classifies; Python discounts) — optional, fails soft
    risk_scan: Optional[MarketRiskScan] = None
    if enable_news:
        try:
            from .osint.scan import run_risk_scan
            # Use company names for DuckDuckGo; tickers (incl. .NS, .L) return no results.
            s2n = {h.symbol.upper(): h.name for h in portfolio.holdings if h.name}
            risk_scan = run_risk_scan(allocation, symbol_to_name=s2n)
        except Exception as e:
            _log.warning("monitoring: emerging-risk scan failed (%s) — skipped.", e)

    # 5b. country research (geopolitical/sectoral/bond/FD-rate/market) — optional, fails soft.
    # Only the top 3 held countries by weight, same "top_n" bounding collect_evidence already
    # applies to holdings/sectors — not every country in a globally diversified book.
    country_research: Dict[str, CountryResearchProfile] = {}
    if enable_country_research:
        held_countries = [b.name for b in allocation.by_country
                          if b.name and b.name != "Unclassified"][:3]
        if held_countries:
            try:
                from .research.graph import run_country_research
                country_research = run_country_research(held_countries)
            except Exception as e:
                _log.warning("monitoring: country research failed (%s) — skipped.", e)

    # 6. alerts + score (deterministic)
    alerts = build_alerts(drift, rebalance, allocation, limit_checks, cashflow,
                          risk_metrics, risk_scan, country_research)
    score = score_portfolio(drift, risk_metrics, limit_checks, cashflow,
                            portfolio.mandate, risk_scan, country_research)

    result = MonitoringResult(
        portfolio=portfolio, allocation=allocation, drift=drift, rebalance=rebalance,
        cashflow=cashflow, risk_metrics=risk_metrics, limit_checks=limit_checks,
        risk_scan=risk_scan, country_research=country_research, alerts=alerts, score=score,
    )

    # 7. briefing(s)
    if mode in ("deterministic", "both"):
        result.briefing_deterministic = build_briefing_det(
            portfolio.name, allocation, drift, rebalance, cashflow, risk_metrics, score, alerts)
    if mode in ("llm", "both"):
        if llm_available():
            result.briefing_llm = build_briefing_llm(result)
        elif mode == "llm":
            # asked for LLM but none configured — give the deterministic one labelled
            result.briefing_deterministic = build_briefing_det(
                portfolio.name, allocation, drift, rebalance, cashflow, risk_metrics, score, alerts)
    return result


def _construct_from_scratch(request: ConstructionRequest) -> Tuple[List[Holding], str]:
    """Build a fresh book for `request.mandate` when there are no holdings to monitor.

    Reuses `construct_llm` as-is rather than re-implementing an LLM-preferred/deterministic-
    fallback choice here: it already falls back to `construct_deterministic` internally
    whenever the LLM is unavailable or its call/parse fails, so calling it unconditionally
    gives exactly that behavior for free.
    """
    from .construct.common import positions_to_holdings
    from .construct.llm_advisor import construct_llm
    from .universe import build_universe

    universe = build_universe(request)
    built = construct_llm(request, universe)
    holdings = positions_to_holdings(built.positions)
    note = (
        f"**Auto-construction** → no holdings supplied; built a `{request.mandate}` book "
        f"from scratch: {len(holdings)} position(s), invested {built.total_invested:,.0f} "
        f"{request.base_currency}"
        + (f", {len(built.guardrail_corrections)} guardrail correction(s)"
           if built.guardrail_corrections else "")
        + "."
    )
    return holdings, note


def run_monitoring(
    holdings_path: str = "",
    transactions_path: str = "",
    statement_path: str = "",
    construction_request: Optional[ConstructionRequest] = None,
    mandate: str = "balanced",
    base_currency: str = "USD",
    mode: str = "both",
    enable_news: bool = True,
    enable_country_research: bool = True,
    question: str = "",
    holdings: Optional[List[Holding]] = None,
    cash_flows: Optional[List[CashFlow]] = None,
    name: str = "Portfolio",
) -> Dict[str, Any]:
    """Load a portfolio (CSV/JSON, PDF statement, or built from scratch) and run monitoring."""
    start = time.time()
    reasoning: List[str] = []
    metrics: Dict[str, Any] = {}

    # --- ingest ---
    if holdings is None:
        if statement_path:
            from .statement_extract import extract_holdings_from_statement
            holdings = extract_holdings_from_statement(statement_path)
            reasoning.append(f"**Ingest** → grounded PDF extraction from `{statement_path}` "
                             f"→ {len(holdings)} holding(s).")
        elif holdings_path:
            holdings = load_holdings(holdings_path)
            reasoning.append(f"**Ingest** → loaded {len(holdings)} holding(s) from "
                             f"`{holdings_path}`.")
        elif construction_request is not None:
            holdings, note = _construct_from_scratch(construction_request)
            reasoning.append(note)
            # Monitor the book against the same mandate/currency it was just built for —
            # otherwise an "aggressive" book checked against a "conservative" IPS would show
            # every sleeve breached on the very first run for no real reason.
            mandate = construction_request.mandate
            base_currency = construction_request.base_currency
        else:
            raise ValueError(
                "Provide holdings_path, statement_path, a holdings list, or a "
                "construction_request to build a portfolio from scratch."
            )
    if cash_flows is None:
        cash_flows = load_cash_flows(transactions_path) if transactions_path else []

    portfolio = build_portfolio(holdings, cash_flows, name=name,
                                base_currency=base_currency, mandate=mandate)

    result = assess_portfolio(portfolio, mode=mode, enable_news=enable_news,
                              enable_country_research=enable_country_research)
    a, d, m, sc = result.allocation, result.drift, result.risk_metrics, result.score

    # --- reasoning trace ---
    reasoning.append(
        f"**Allocation (deterministic)** → MV {a.total_market_value:,.0f} {a.base_currency}, "
        f"mix: " + ", ".join(f"{b.name} {b.weight:.0%}" for b in a.by_asset_class)
        + (f"; priced coverage {a.priced_coverage:.0%}" if a.priced_coverage < 1 else ""))
    reasoning.append(
        f"**Drift vs {mandate} IPS (deterministic)** → {d.n_breaches} breach(es), "
        f"active share {d.active_share:.1%}; " + result.rebalance.summary)
    reasoning.append(
        f"**Cash-flow variance (deterministic)** → expected {result.cashflow.total_expected_income:,.0f}, "
        f"actual {result.cashflow.total_actual_income:,.0f}, {result.cashflow.n_flags} flag(s).")
    risk_bits = []
    if m.volatility_annual is not None:
        risk_bits.append(f"vol {m.volatility_annual:.1%}")
    if m.max_drawdown is not None:
        risk_bits.append(f"maxDD {m.max_drawdown:.1%}")
    if m.var_95_1d_pct is not None:
        risk_bits.append(f"VaR95 {m.var_95_1d_pct:.1%}")
    if m.concentration_hhi is not None:
        risk_bits.append(f"HHI {m.concentration_hhi:.2f}")
    reasoning.append(f"**Risk (deterministic)** → " + (", ".join(risk_bits) or "n/a")
                     + f"; data coverage {m.data_coverage:.0%}; "
                     + f"{sum(1 for c in result.limit_checks if c.status=='breach')} limit breach(es).")
    if result.risk_scan:
        reasoning.append(
            f"**Emerging-risk scan (news)** → tone `{result.risk_scan.overall_risk_tone}`, "
            f"{len(result.risk_scan.findings)} finding(s), {len(result.risk_scan.evidence)} evidence item(s).")
    if result.country_research:
        reasoning.append(
            f"**Country research** → {len(result.country_research)} held countr"
            f"{'y' if len(result.country_research) == 1 else 'ies'} researched "
            "(geopolitical/sectoral/bond/FD-rate/market/growth): " + ", ".join(
                f"{c} `{p.overall_risk_tone}` risk / `{p.growth_outlook}` growth"
                for c, p in result.country_research.items()))
    reasoning.append(
        f"**Score (deterministic)** → {sc.normalized_score:.0f}/100, health `{sc.health_band.value}`; "
        f"{len(result.alerts)} alert(s).")

    # --- answer (LLM, grounded) ---
    answer = ""
    if question:
        answer = answer_question(result, question)

    # --- metrics ---
    n_llm = ((1 if (result.briefing_llm) else 0)
             + (1 if (result.risk_scan and llm_available()) else 0)
             + (1 if (question and llm_available()) else 0))
    metrics.update({
        "task": "monitoring", "mode": mode, "holdings": len(holdings),
        "market_value": a.total_market_value, "health_band": sc.health_band.value,
        "risk_score": sc.normalized_score, "n_alerts": len(result.alerts),
        "drift_breaches": d.n_breaches, "data_coverage": m.data_coverage,
        "market_data": market_data_available(), "llm_calls": n_llm,
        "latency_ms": round((time.time() - start) * 1000.0, 2),
    })
    return {"result": result, "answer": answer, "reasoning": reasoning, "metrics": metrics}


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
def _universe_pseudo_allocation(universe) -> AllocationBreakdown:
    """Build a light allocation view over the universe so the news scan has subjects."""
    eq = [c for c in universe if c.asset_class.value == "equity" and c.has_market_data][:5]
    by_pos = [AllocationBucket(name=c.symbol, weight=0.0) for c in eq]
    secs: Dict[str, float] = {}
    for c in eq:
        secs[c.sector or "Unclassified"] = secs.get(c.sector or "Unclassified", 0.0) + 1.0
    by_sec = [AllocationBucket(name=k, weight=v) for k, v in secs.items()]
    return AllocationBreakdown(by_position=by_pos, by_sector=by_sec)


def run_construction(
    request: ConstructionRequest,
    mode: str = "both",
    enable_news: bool = True,
    question: str = "",
) -> Dict[str, Any]:
    """Construct a portfolio from scratch (deterministic and/or LLM), and compare."""
    start = time.time()
    reasoning: List[str] = []
    metrics: Dict[str, Any] = {}

    # Country research (geopolitical/sectoral/bond/FD-rate/market) — only when the investor
    # profile actually names countries; nothing to research for an unconstrained global mix,
    # and this is materially more expensive than the news scan (5 agents x N countries).
    country_research: Dict[str, CountryResearchProfile] = {}
    if request.enable_country_research and request.countries:
        from .universe import resolve_countries
        countries_to_research = resolve_countries(request)
        try:
            from .research.graph import run_country_research
            country_research = run_country_research(countries_to_research)
            reasoning.append(
                f"**Country research** → {len(country_research)}/{len(countries_to_research)} "
                "countries researched (geopolitical/sectoral/bond/FD-rate/market/growth): " + ", ".join(
                    f"{c} `{p.overall_risk_tone}` risk / `{p.growth_outlook}` growth"
                    for c, p in country_research.items()))
        except Exception as e:
            _log.warning("construction: country research failed (%s) — skipped.", e)

    from .universe import build_universe
    universe = build_universe(request, country_research=country_research or None)
    reasoning.append(f"**Universe (auto-fetched)** → {len(universe)} candidate(s) "
                     f"({sum(1 for c in universe if c.has_market_data)} with live prices).")

    risk_scan: Optional[MarketRiskScan] = None
    if enable_news:
        try:
            from .osint.scan import run_risk_scan
            # Build company-name map from the universe so DuckDuckGo gets "Reliance Industries"
            # not "RELIANCE.NS".
            s2n = {c.symbol.upper(): c.name for c in universe if c.name}
            risk_scan = run_risk_scan(_universe_pseudo_allocation(universe),
                                      symbol_to_name=s2n)
            reasoning.append(f"**Market context (news)** → tone "
                             f"`{risk_scan.overall_risk_tone}`, {len(risk_scan.findings)} finding(s).")
        except Exception as e:
            _log.warning("construction: news scan failed (%s) — skipped.", e)

    det: Optional[ConstructedPortfolio] = None
    llm: Optional[ConstructedPortfolio] = None
    comparison = None

    from .construct.annotate import annotate_positions
    if mode in ("deterministic", "both"):
        from .construct.deterministic import construct_deterministic
        det = construct_deterministic(request, universe)
        annotate_positions(det.positions, risk_scan, use_llm=False)   # deterministic pros/cons
        reasoning.append(f"**Deterministic construction** → {len(det.positions)} position(s), "
                         f"invested {det.total_invested:,.0f}, {len(det.guardrail_corrections)} "
                         f"guardrail correction(s); "
                         f"feasibility: {'realistic' if (det.feasibility and det.feasibility.realistic) else 'see note'}.")
    if mode in ("llm", "both"):
        from .construct.llm_advisor import construct_llm
        llm = construct_llm(request, universe, risk_scan)
        annotate_positions(llm.positions, risk_scan, use_llm=True)    # grounded LLM pros/cons
        reasoning.append(f"**LLM construction** → {len(llm.positions)} position(s), "
                         f"invested {llm.total_invested:,.0f}, {len(llm.guardrail_corrections)} "
                         f"guardrail correction(s).")
    if det and llm:
        comparison = compare_constructions(det, llm)
        reasoning.append("**Comparison** → " + " ".join(comparison.notes))

    result = ConstructionResult(request=request, deterministic=det, llm=llm,
                                comparison=comparison, risk_scan=risk_scan,
                                country_research=country_research)

    metrics.update({
        "task": "construction", "mode": mode, "universe": len(universe),
        "market_data": market_data_available(), "llm_used": bool(llm and llm_available()),
        "latency_ms": round((time.time() - start) * 1000.0, 2),
    })
    return {"result": result, "reasoning": reasoning, "metrics": metrics}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _usage() -> None:
    print("Usage:")
    print("  python -m portfolio_monitor.agent_graph monitor [<holdings.csv|.json>] "
          "[--transactions f] [--statement f.pdf] [--mandate balanced] [--amount 1000000] "
          "[--mode deterministic|llm|both] [--no-news] [-- question]")
    print("    (omit the holdings path and --statement to build a book from scratch for "
          "--mandate instead of monitoring an existing one)")
    print("  python -m portfolio_monitor.agent_graph construct [--mandate growth] "
          "[--amount 1000000] [--method equal_weight|inverse_vol|risk_parity] "
          "[--countries US,IN] [--mode deterministic|llm|both] [--no-news]")


def _main() -> None:
    import sys
    from .schemas import WeightingMethod

    argv = sys.argv[1:]
    if not argv:
        _usage(); sys.exit(1)
    task = argv[0]
    rest = argv[1:]

    question = ""
    if "--" in rest:
        i = rest.index("--")
        question = " ".join(rest[i + 1:])
        rest = rest[:i]

    enable_news = True
    if "--no-news" in rest:
        enable_news = False
        rest.remove("--no-news")

    def opt(name: str, default: str = "") -> str:
        if name in rest:
            i = rest.index(name)
            val = rest[i + 1] if i + 1 < len(rest) else ""
            del rest[i:i + 2]
            return val
        return default

    mode = opt("--mode", "both")
    mandate = opt("--mandate", "balanced")

    if task == "monitor":
        transactions = opt("--transactions")
        statement = opt("--statement")
        amount = opt("--amount")
        holdings_path = rest[0] if rest and not rest[0].startswith("--") else ""
        # No holdings/statement given on the command line is unambiguous, deliberate intent
        # here (unlike an HTTP client that might silently omit a file by accident), so no
        # separate opt-in flag is needed for the CLI — build a book from scratch instead.
        construction_request = (
            ConstructionRequest(mandate=mandate, invest_amount=float(amount or 1_000_000))
            if not holdings_path and not statement else None
        )
        out = run_monitoring(
            holdings_path=holdings_path, transactions_path=transactions,
            statement_path=statement, construction_request=construction_request,
            mandate=mandate, mode=mode,
            enable_news=enable_news, question=question)
        r = out["result"]
        sc = r.score
        print("\n=== PORTFOLIO HEALTH ===")
        print(f"{r.portfolio.name}: {sc.health_band.value.upper()} "
              f"(score {sc.normalized_score:.0f}/100), MV {r.allocation.total_market_value:,.0f} "
              f"{r.allocation.base_currency}")
        print("\n=== ALLOCATION vs IPS ===")
        for l in r.drift.lines:
            print(f"  {l.bucket:14s} {l.actual_weight:6.1%} (target {l.target_weight:5.1%}) "
                  f"-> {l.status.value}")
        if r.rebalance.trades:
            print("\n=== REBALANCING ===")
            for t in r.rebalance.trades:
                print(f"  {t.action.upper():4s} {t.bucket:14s} {t.trade_value:+,.0f}  | {t.rationale}")
        print("\n=== ALERTS ===")
        for al in r.alerts:
            print(f"  [{al.severity.value:8s}] {al.title}")
            print(f"             ↳ {al.recommended_action}")
        b = r.briefing_llm or r.briefing_deterministic
        if b:
            print(f"\n=== BRIEFING ({b.method}) ===\n{b.executive_summary}")
        if out["answer"]:
            print("\n=== ANSWER ===\n", out["answer"])
        print("\n=== METRICS ===\n", out["metrics"])

    elif task == "construct":
        amount = opt("--amount", "1000000")
        method = opt("--method", "equal_weight")
        countries_arg = opt("--countries")
        try:
            wm = WeightingMethod(method)
        except ValueError:
            wm = WeightingMethod.EQUAL_WEIGHT
        countries = [c.strip() for c in countries_arg.split(",") if c.strip()] if countries_arg else []
        req = ConstructionRequest(
            mandate=mandate, invest_amount=float(amount), weighting_method=wm,
            countries=countries)
        out = run_construction(req, mode=mode, enable_news=enable_news)
        r = out["result"]
        for label, cp in (("DETERMINISTIC", r.deterministic), ("LLM", r.llm)):
            if not cp:
                continue
            print(f"\n=== {label} CONSTRUCTION ({cp.weighting_method.value}) ===")
            print(f"  invested {cp.total_invested:,.0f}, cash {cp.cash_residual:,.0f}")
            for p in cp.positions:
                print(f"  {p.symbol:6s} {p.target_weight:6.1%}  {p.amount:>12,.0f}  | {p.rationale}")
            if cp.guardrail_corrections:
                print("  guardrails:")
                for c in cp.guardrail_corrections:
                    print(f"    - {c}")
        if r.comparison:
            print("\n=== COMPARISON ===")
            for n in r.comparison.notes:
                print(f"  - {n}")
        print("\n=== METRICS ===\n", out["metrics"])

    else:
        _usage(); sys.exit(1)


if __name__ == "__main__":
    _main()

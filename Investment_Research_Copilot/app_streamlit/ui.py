"""
Streamlit UI — Investment Research Copilot: Portfolio & Risk Monitoring (+ Construction).

Two tasks, each runnable in three engine modes (selectable, and comparable side-by-side):

  • MONITOR  — upload a holdings file (CSV/JSON) + an optional transactions ledger, or a PDF
               brokerage statement; see allocation vs the IPS, drift + a rebalancing plan,
               cash-flow variances, risk metrics + limit breaches, emerging-risk news, a
               prioritised alert list, the portfolio health score, and a briefing.
  • CONSTRUCT — build a portfolio from scratch for a mandate from a yfinance-enriched
               universe; every position carries the reason it was chosen.

  Modes:  Deterministic (rules only) · LLM-augmented (grounded) · Both (side-by-side).
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio_monitor.agent_graph import run_construction, run_monitoring  # noqa: E402
from portfolio_monitor.ingest import (  # noqa: E402
    load_cash_flows_from_text,
    load_holdings_from_text,
)
from portfolio_monitor.reference import available_countries  # noqa: E402
from portfolio_monitor.research.deterministic import growth_potential  # noqa: E402
from portfolio_monitor.schemas import (  # noqa: E402
    ConstructionConstraints,
    ConstructionRequest,
    GeographyMode,
    InstrumentType,
    PortfolioStyle,
    Region,
    WeightingMethod,
)

st.set_page_config(page_title="Portfolio & Risk Monitoring", layout="wide")
st.title("📈 Investment Research Copilot — Portfolio & Risk Monitoring")

_HEALTH_ICON = {"healthy": "🟢", "watch": "🟡", "elevated": "🟠", "critical": "🔴"}
_SEV_ICON = {"info": "⚪", "low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
_TONE_ICON = {"calm": "🟢", "normal": "⚪", "elevated": "🟠", "stressed": "🔴"}
_DRIFT_ICON = {"within_band": "🟢", "overweight": "🟡", "underweight": "🟡",
               "breach_over": "🔴", "breach_under": "🔴"}
_GROWTH_ICON = {"high": "🟢", "medium": "🟡", "low": "🟠", "unknown": "⚪"}


def _fmt(v, pct=False, money=False):
    if v is None:
        return "—"
    if pct:
        return f"{v:.1%}"
    if money:
        return f"{v:,.0f}"
    return f"{v:.2f}"


def _growth_cell(sector, country, country_research):
    rating, _ = growth_potential(sector, country, country_research)
    return f"{_GROWTH_ICON.get(rating, '')} {rating.title()}"


def _group_by_country(items):
    """Group items by their `.country`, sorted by country name; items with no country form
    a trailing 'Unclassified' group. Returns [(label, [item, ...]), ...]."""
    by_country: dict = {}
    unclassified = []
    for item in items:
        if item.country:
            by_country.setdefault(item.country, []).append(item)
        else:
            unclassified.append(item)
    ordered = [(c, by_country[c]) for c in sorted(by_country)]
    if unclassified:
        ordered.append(("Unclassified", unclassified))
    return ordered


def _render_country_growth_table(country_research):
    if not country_research:
        return
    st.markdown("**Country growth outlook**")
    st.dataframe(pd.DataFrame([{
        "Country": p.country.replace("_", " ").title(),
        "Risk tone": f"{_TONE_ICON.get(p.overall_risk_tone, '')} {p.overall_risk_tone}",
        "Growth outlook": f"{_GROWTH_ICON.get(p.growth_outlook, '')} {p.growth_outlook}",
        "Growing sectors": ", ".join(p.growth_sectors) or "—",
    } for p in country_research.values()]), hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------- #
# Sidebar — task + mode + inputs
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### Task")
    task = st.radio("What do you want to do?",
                    ["Monitor an existing portfolio", "Construct a portfolio from scratch"],
                    label_visibility="collapsed")
    is_monitor = task.startswith("Monitor")

    st.markdown("### Engine mode")
    mode_label = st.radio(
        "Engine", ["Deterministic", "LLM-augmented", "Both (compare)"],
        help="Deterministic = rules only (reproducible, offline). LLM-augmented = grounded "
             "language layer. Both = run each and compare.", label_visibility="collapsed")
    mode = {"Deterministic": "deterministic", "LLM-augmented": "llm",
            "Both (compare)": "both"}[mode_label]

    enable_news = st.checkbox("Scan free news for emerging risks (DuckDuckGo)", value=False,
                              help="Off by default — turning it on adds a few network calls.")
    mandate = st.selectbox("Mandate / risk profile",
                           ["conservative", "balanced", "growth", "aggressive"], index=1)

    if is_monitor:
        st.markdown("### Holdings & transactions")
        st.caption("Upload structured files (CSV/JSON). Columns are matched flexibly "
                   "(ticker/symbol, qty/quantity, ...).")
        holdings_file = st.file_uploader("Holdings (CSV/JSON)", type=["csv", "json"])
        txns_file = st.file_uploader("Transactions ledger (CSV/JSON, optional)",
                                     type=["csv", "json"])
        statement_file = st.file_uploader("…or a PDF brokerage statement (optional)",
                                          type=["pdf"])
        base_ccy = st.text_input("Base currency", value="USD")
        use_sample = st.checkbox("Use bundled sample portfolio", value=True)
        question = st.text_input("Ask a question (LLM modes)",
                                 value="What are the top two actions I should take and why?")
        run = st.button("Run monitoring", type="primary")
    else:
        st.markdown("### Investor profile")
        amount = st.number_input("Amount to invest", min_value=1000.0,
                                 value=1_000_000.0, step=10000.0)
        horizon = st.number_input("Time horizon (years)", min_value=1, max_value=40, value=5)

        geo_label = st.radio("Geography", ["Global", "Specific country", "List of countries"],
                             horizontal=False)
        geo_mode = {"Global": GeographyMode.GLOBAL,
                    "Specific country": GeographyMode.COUNTRY_SPECIFIC,
                    "List of countries": GeographyMode.COUNTRY_LIST}[geo_label]
        countries: list = []
        if geo_mode == GeographyMode.COUNTRY_SPECIFIC:
            countries = [st.selectbox("Country", available_countries())]
        elif geo_mode == GeographyMode.COUNTRY_LIST:
            countries = st.multiselect("Countries", available_countries(),
                                       default=["united_states"])

        style_label = st.selectbox(
            "Portfolio style",
            ["large_cap", "mid_cap", "small_cap", "multi_cap", "fund_of_funds", "thematic", "elss"],
            index=0)
        style = PortfolioStyle(style_label)
        cap_mix = {}
        if style == PortfolioStyle.MULTI_CAP:
            st.caption("Large/Mid/Small weightage within equity (auto-normalised)")
            lc = st.slider("Large %", 0.0, 1.0, 0.6, 0.05)
            mc = st.slider("Mid %", 0.0, 1.0, 0.3, 0.05)
            sc = st.slider("Small %", 0.0, 1.0, 0.1, 0.05)
            cap_mix = {"large_cap": lc, "mid_cap": mc, "small_cap": sc}
        theme = ""
        if style == PortfolioStyle.THEMATIC:
            theme = st.text_input("Theme", value="AI", help="e.g. AI, clean energy, semiconductor")

        st.markdown("**Equity / Debt split** (overrides the mandate when set)")
        eq = st.slider("Equity %", 0.0, 1.0, 0.7, 0.05)
        use_eqd = st.checkbox("Apply this equity/debt split", value=False)

        instruments = st.multiselect("Instrument types (optional filter)",
                                     ["stock", "etf", "mutual_fund"], default=[])

        st.markdown("**Return objective** (optional)")
        ret_kind = st.radio("Express as", ["None", "Annual %", "Target value"], horizontal=True)
        target_pct = target_val = None
        if ret_kind == "Annual %":
            target_pct = st.number_input("Expected annual return %", value=12.0, step=1.0) / 100.0
        elif ret_kind == "Target value":
            target_val = st.number_input("Target portfolio value at horizon", value=1_500_000.0,
                                         step=10000.0)

        weighting = st.selectbox("Within-sleeve weighting",
                                 ["equal_weight", "inverse_vol", "risk_parity", "return_tilted"],
                                 index=0)
        single_cap = st.slider("Single-name cap", 0.02, 0.25, 0.10, 0.01)
        sector_cap = st.slider("Sector cap", 0.10, 0.60, 0.30, 0.05)
        exclusions = st.text_input("Exclusions (comma-separated symbols/sectors)", value="")
        universe_syms = st.text_input("Custom universe (comma-separated tickers, optional)",
                                      value="", help="Overrides the country/style universe.")
        run = st.button("Construct portfolio", type="primary")

st.caption("Numbers, drift, rebalancing, risk metrics, alerts and the health score are "
           "**deterministic**. The LLM only writes language (news classification, briefing, "
           "construction reasoning, Q&A) and can never breach a hard constraint.")


# --------------------------------------------------------------------------- #
# MONITOR
# --------------------------------------------------------------------------- #
def _read_upload(file):
    text = file.getvalue().decode("utf-8", errors="ignore")
    fmt = "json" if file.name.lower().endswith(".json") else "csv"
    return text, fmt


def render_monitoring(out):
    r = out["result"]
    a, d, m, sc = r.allocation, r.drift, r.risk_metrics, r.score
    metrics = out["metrics"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Health", f"{_HEALTH_ICON.get(sc.health_band.value,'')} {sc.health_band.value.title()}")
    c2.metric("Risk score", f"{sc.normalized_score:.0f}/100")
    c3.metric("Market value", f"{a.total_market_value:,.0f} {a.base_currency}")
    c4.metric("Drift breaches", d.n_breaches)
    c5.metric("Alerts", len(r.alerts))
    st.caption(f"Mandate `{r.portfolio.mandate}` · holdings {m.n_holdings} · data coverage "
               f"{m.data_coverage:.0%} · market data {'on' if metrics.get('market_data') else 'off'} "
               f"· LLM calls {metrics.get('llm_calls')} · {metrics.get('latency_ms')} ms")

    tabs = st.tabs(["⚖️ Alerts", "🥧 Allocation", "📊 Drift & Rebalance", "💵 Cash flow",
                    "📉 Risk", "🌐 Emerging risk", "📝 Briefing", "🧠 Trace"])

    with tabs[0]:
        st.subheader("Prioritised alerts")
        if not r.alerts:
            st.success("No alerts — portfolio within policy and risk limits.")
        for al in r.alerts:
            with st.expander(f"{_SEV_ICON.get(al.severity.value,'')} [{al.severity.value.upper()}] "
                             f"{al.title}", expanded=al.severity.value in ("high", "critical")):
                st.write(al.detail)
                if al.evidence:
                    st.caption("Evidence: " + ", ".join(str(e) for e in al.evidence))
                st.markdown(f"**Recommended action:** {al.recommended_action}")

    with tabs[1]:
        st.subheader("Allocation breakdown")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**By asset class**")
            st.dataframe(pd.DataFrame([{"Asset class": b.name, "Weight": _fmt(b.weight, pct=True),
                                        "Value": _fmt(b.market_value, money=True)}
                                       for b in a.by_asset_class]),
                         hide_index=True, use_container_width=True)
            st.markdown("**By region**")
            st.dataframe(pd.DataFrame([{"Region": b.name, "Weight": _fmt(b.weight, pct=True)}
                                       for b in a.by_region]), hide_index=True,
                         use_container_width=True)
        with cc2:
            st.markdown("**By position**")
            holdings = r.portfolio.holdings
            distinct_countries = {h.country for h in holdings if h.country}
            if len(distinct_countries) >= 2:
                for label, group in _group_by_country(holdings):
                    st.caption(label)
                    st.dataframe(_holdings_df(group, r.country_research), hide_index=True,
                                 use_container_width=True)
            else:
                st.dataframe(_holdings_df(holdings, r.country_research), hide_index=True,
                             use_container_width=True)
        ec1, ec2 = st.columns(2)
        with ec1:
            if a.by_country:
                st.markdown("**By country**")
                st.dataframe(pd.DataFrame([{"Country": b.name, "Weight": _fmt(b.weight, pct=True)}
                                           for b in a.by_country]), hide_index=True,
                             use_container_width=True)
        with ec2:
            if a.by_market_cap:
                st.markdown("**By market cap (equity)**")
                st.dataframe(pd.DataFrame([{"Cap": b.name, "Weight": _fmt(b.weight, pct=True)}
                                           for b in a.by_market_cap]), hide_index=True,
                             use_container_width=True)
        if a.equity_debt:
            st.caption("Equity / Debt / Cash: " + " · ".join(
                f"{k} {v:.0%}" for k, v in a.equity_debt.items() if v > 0))
        _render_country_growth_table(r.country_research)
        if a.by_asset_class:
            st.bar_chart(pd.DataFrame(
                {b.name: [b.weight] for b in a.by_asset_class}).T.rename(columns={0: "weight"}))
        if a.unpriced_symbols:
            st.warning("Unpriced (excluded from allocation/risk): " + ", ".join(a.unpriced_symbols))

    with tabs[2]:
        st.subheader(f"Allocation vs IPS — {d.n_breaches} breach(es), active share {d.active_share:.1%}")
        st.dataframe(pd.DataFrame([{
            "Sleeve": l.bucket, "Actual": _fmt(l.actual_weight, pct=True),
            "Target": _fmt(l.target_weight, pct=True),
            "Band": f"{l.lower_band:.0%}–{l.upper_band:.0%}",
            "Drift": f"{l.drift:+.1%}",
            "Status": f"{_DRIFT_ICON.get(l.status.value,'')} {l.status.value}",
            "Rationale": l.rationale,
        } for l in d.lines]), hide_index=True, use_container_width=True)
        st.markdown("**Rebalancing plan**")
        st.info(r.rebalance.summary)
        if r.rebalance.trades:
            st.dataframe(pd.DataFrame([{
                "Sleeve": t.bucket, "Action": t.action.upper(),
                "Trade value": _fmt(t.trade_value, money=True),
                "Trade %": _fmt(t.trade_pct, pct=True), "Rationale": t.rationale,
            } for t in r.rebalance.trades]), hide_index=True, use_container_width=True)

    with tabs[3]:
        cf = r.cashflow
        st.subheader("Cash-flow variance")
        c1, c2, c3 = st.columns(3)
        c1.metric("Expected income", f"{cf.total_expected_income:,.0f}")
        c2.metric("Actual income", f"{cf.total_actual_income:,.0f}")
        c3.metric("Net variance", f"{cf.net_variance:+,.0f}")
        if cf.variances:
            st.dataframe(pd.DataFrame([{
                "Symbol": v.symbol or "—", "Type": v.type.value,
                "Expected": _fmt(v.expected, money=True), "Actual": _fmt(v.actual, money=True),
                "Variance": _fmt(v.variance, money=True), "Status": v.status,
                "Severity": f"{_SEV_ICON.get(v.severity.value,'')} {v.severity.value}",
                "Note": v.note,
            } for v in cf.variances]), hide_index=True, use_container_width=True)
        for n in cf.notes:
            st.caption(n)

    with tabs[4]:
        st.subheader("Risk metrics")
        rows = [
            ("Annualised volatility", _fmt(m.volatility_annual, pct=True)),
            ("Max drawdown", _fmt(m.max_drawdown, pct=True)),
            ("1-day 95% VaR", f"{_fmt(m.var_95_1d_pct, pct=True)} ({_fmt(m.var_95_1d_value, money=True)})"),
            ("Beta (vs benchmark)", _fmt(m.beta)),
            ("Tracking error", _fmt(m.tracking_error, pct=True)),
            ("Dividend yield", _fmt(m.dividend_yield, pct=True)),
            ("Concentration (HHI)", _fmt(m.concentration_hhi)),
            ("Effective N positions", _fmt(m.effective_n)),
            ("Top-1 / Top-5 weight", f"{_fmt(m.top1_weight, pct=True)} / {_fmt(m.top5_weight, pct=True)}"),
            ("Data coverage", _fmt(m.data_coverage, pct=True)),
        ]
        st.table(pd.DataFrame(rows, columns=["Metric", "Value"]))
        st.markdown("**Risk-limit checks**")
        st.dataframe(pd.DataFrame([{
            "Limit": c.name.replace("_", " "), "Observed": _fmt(c.observed),
            "Limit value": _fmt(c.limit),
            "Status": f"{_SEV_ICON.get(c.severity.value,'')} {c.status}", "Detail": c.detail,
        } for c in r.limit_checks]), hide_index=True, use_container_width=True)
        for n in m.notes:
            st.caption(n)

    with tabs[5]:
        render_risk_scan(r.risk_scan)

    with tabs[6]:
        render_briefings(r.briefing_deterministic, r.briefing_llm, out.get("answer"))

    with tabs[7]:
        for i, step in enumerate(out["reasoning"], 1):
            st.markdown(f"**{i}.** {step}")


def render_risk_scan(scan):
    if not scan:
        st.info("Emerging-risk news scan was not run. Enable it in the sidebar.")
        return
    st.subheader(f"Emerging-risk scan — tone "
                 f"{_TONE_ICON.get(scan.overall_risk_tone,'')} {scan.overall_risk_tone.title()}")
    st.write(scan.summary or "—")
    if scan.findings:
        st.dataframe(pd.DataFrame([{
            "Scope": f.scope, "Subject": f.subject, "Category": f.category.value,
            "Severity": f"{_SEV_ICON.get(f.severity,'')} {f.severity}", "Summary": f.summary,
            "Source": f.url or "—",
        } for f in scan.findings]), hide_index=True, use_container_width=True)
    else:
        st.write("No material emerging-risk findings.")
    if scan.evidence:
        with st.expander(f"Evidence trail ({len(scan.evidence)} items)"):
            st.dataframe(pd.DataFrame([{
                "Angle": e.dimension.value, "Title": e.title or "—", "URL": e.url or "—",
                "Published": e.published or "—",
            } for e in scan.evidence]), hide_index=True, use_container_width=True)
    for g in scan.data_gaps:
        st.caption("Data gap: " + g)


def render_briefings(det, llm, answer=None):
    cols = st.columns(2) if (det and llm) else [st.container()]
    pairs = [c for c in [det, llm] if c]
    for col, b in zip((cols if (det and llm) else cols), pairs):
        with col:
            st.markdown(f"#### Briefing — *{b.method}*")
            st.markdown("**Executive summary**"); st.write(b.executive_summary or "—")
            st.markdown("**Allocation**"); st.write(b.allocation_commentary or "—")
            st.markdown("**Risk**"); st.write(b.risk_commentary or "—")
            st.markdown("**Cash flow**"); st.write(b.cashflow_commentary or "—")
            st.markdown("**Recommended actions**")
            for x in b.recommended_actions or ["—"]:
                st.markdown(f"- {x}")
            if b.watch_items:
                st.markdown("**Watch items**")
                for x in b.watch_items:
                    st.markdown(f"- {x}")
    if answer:
        st.divider()
        st.markdown("**Answer to your question**")
        st.write(answer)


def _holdings_df(holdings, country_research):
    return pd.DataFrame([{
        "Symbol": h.symbol, "Sector": h.sector or "—", "Country": h.country or "—",
        "Weight": _fmt(h.weight, pct=True), "Value": _fmt(h.market_value, money=True),
        "Growth": _growth_cell(h.sector, h.country, country_research),
    } for h in holdings])


# --------------------------------------------------------------------------- #
# CONSTRUCT
# --------------------------------------------------------------------------- #
def _positions_df(positions, country_research):
    return pd.DataFrame([{
        "Symbol": p.symbol, "Class": p.asset_class.value, "Cap": p.market_cap.value,
        "Country": p.country or "—", "Sector": p.sector or "—",
        "Weight": _fmt(p.target_weight, pct=True), "Amount": _fmt(p.amount, money=True),
        "Exp.return": _fmt(p.expected_return, pct=True),
        "Growth": _growth_cell(p.sector, p.country, country_research),
        "Shares": (f"{p.shares:.2f}" if p.shares else "—"), "Reason": p.rationale,
    } for p in positions])


def render_constructed(cp, title, country_research=None):
    st.markdown(f"#### {title} — *{cp.method}* · {cp.style.value} ({cp.weighting_method.value})")
    st.caption(f"Invested {cp.total_invested:,.0f} · cash residual {cp.cash_residual:,.0f}"
               + (f" · countries: {', '.join(cp.countries)}" if cp.countries else ""))
    st.write(cp.narrative)

    # feasibility of the return objective
    fb = cp.feasibility
    if fb and fb.target_basis != "none":
        (st.success if fb.realistic else st.error)(
            f"**Return objective** — target ~{_fmt(fb.target_return_annual, pct=True)} p.a. vs "
            f"expected ~{_fmt(fb.expected_return_annual, pct=True)} p.a. "
            f"({'realistic' if fb.realistic else 'UNREALISTIC'}). {fb.message}")
    elif fb:
        st.caption(fb.message)

    distinct_countries = {p.country for p in cp.positions if p.country}
    if len(distinct_countries) >= 2:
        for label, group in _group_by_country(cp.positions):
            st.caption(label)
            st.dataframe(_positions_df(group, country_research), hide_index=True,
                         use_container_width=True)
    else:
        st.dataframe(_positions_df(cp.positions, country_research), hide_index=True,
                     use_container_width=True)

    em = cp.expected_metrics
    st.caption(f"Expected: return {_fmt(em.expected_return_annual, pct=True)} · "
               f"vol {_fmt(em.volatility_annual, pct=True)} · yield {_fmt(em.dividend_yield, pct=True)} · "
               f"HHI {_fmt(em.concentration_hhi)} · effective N {_fmt(em.effective_n)}")

    ac1, ac2 = st.columns(2)
    with ac1:
        if cp.cap_allocation:
            st.caption("By market cap (equity): " + ", ".join(
                f"{b.name} {b.weight:.0%}" for b in cp.cap_allocation))
    with ac2:
        if cp.country_allocation:
            st.caption("By country: " + ", ".join(
                f"{b.name} {b.weight:.0%}" for b in cp.country_allocation))

    # per-stock pros & cons (incl. geopolitical / sector sentiment)
    with st.expander("Per-stock pros & cons (incl. sector / geopolitical sentiment)", expanded=False):
        for p in cp.positions:
            if not (p.pros or p.cons):
                continue
            st.markdown(f"**{p.symbol}** — {p.name or ''}")
            pc1, pc2 = st.columns(2)
            with pc1:
                st.markdown("✅ **Pros**")
                for x in p.pros or ["—"]:
                    st.markdown(f"- {x}")
            with pc2:
                st.markdown("⚠️ **Cons**")
                for x in p.cons or ["—"]:
                    st.markdown(f"- {x}")

    if cp.guardrail_corrections:
        with st.expander(f"Guardrail corrections ({len(cp.guardrail_corrections)})"):
            for c in cp.guardrail_corrections:
                st.markdown(f"- {c}")
    if cp.excluded:
        with st.expander(f"Excluded candidates ({len(cp.excluded)})"):
            for e in cp.excluded:
                st.markdown(f"- **{e.symbol}** — {e.reason}")


def render_construction(out):
    r = out["result"]
    metrics = out["metrics"]
    st.caption(f"Universe {metrics.get('universe')} candidate(s) · market data "
               f"{'on' if metrics.get('market_data') else 'off'} · "
               f"LLM {'used' if metrics.get('llm_used') else 'not used'} · "
               f"{metrics.get('latency_ms')} ms")
    _render_country_growth_table(r.country_research)

    if r.deterministic and r.llm:
        c1, c2 = st.columns(2)
        with c1:
            render_constructed(r.deterministic, "Deterministic", r.country_research)
        with c2:
            render_constructed(r.llm, "LLM-advised", r.country_research)
        if r.comparison:
            st.divider()
            st.subheader("Comparison")
            for n in r.comparison.notes:
                st.markdown(f"- {n}")
            st.dataframe(pd.DataFrame(r.comparison.allocation_diff), hide_index=True,
                         use_container_width=True)
            cc1, cc2 = st.columns(2)
            cc1.metric("Name overlap (Jaccard)", f"{r.comparison.jaccard:.0%}")
            cc2.write({"Deterministic-only": r.comparison.det_only,
                       "LLM-only": r.comparison.llm_only})
    else:
        cp = r.deterministic or r.llm
        if cp:
            render_constructed(cp, "Constructed portfolio", r.country_research)
    if r.risk_scan:
        st.divider()
        render_risk_scan(r.risk_scan)
    with st.expander("Reasoning trace"):
        for i, step in enumerate(out["reasoning"], 1):
            st.markdown(f"**{i}.** {step}")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
if not run:
    st.info("Configure the task and inputs in the sidebar, then run.")
    st.stop()

if is_monitor:
    holdings = cash_flows = None
    statement_path = ""
    if statement_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(statement_file.getvalue())
            statement_path = tmp.name
    elif holdings_file is not None:
        text, fmt = _read_upload(holdings_file)
        holdings = load_holdings_from_text(text, fmt)
        if txns_file is not None:
            ttext, tfmt = _read_upload(txns_file)
            cash_flows = load_cash_flows_from_text(ttext, tfmt)
    elif use_sample:
        sample = Path(__file__).parent.parent / "sample_data"
        holdings = load_holdings_from_text((sample / "holdings.csv").read_text(), "csv")
        cash_flows = load_cash_flows_from_text((sample / "transactions.csv").read_text(), "csv")
    else:
        st.error("Upload a holdings file / PDF statement, or tick 'Use bundled sample portfolio'.")
        st.stop()

    with st.spinner("Valuing, checking the policy, computing risk, scoring..."):
        out = run_monitoring(
            statement_path=statement_path, mandate=mandate, base_currency=base_ccy,
            mode=mode, enable_news=enable_news, question=question,
            holdings=holdings, cash_flows=cash_flows)
    render_monitoring(out)

else:
    cons = ConstructionConstraints(
        single_name_cap=single_cap, sector_cap=sector_cap,
        exclusions=[x.strip() for x in exclusions.split(",") if x.strip()],
    )
    req = ConstructionRequest(
        mandate=mandate, invest_amount=amount, horizon_years=int(horizon),
        weighting_method=WeightingMethod(weighting), constraints=cons,
        universe_symbols=[s.strip().upper() for s in universe_syms.split(",") if s.strip()],
        geography_mode=geo_mode, countries=[c for c in countries if c],
        style=style, cap_mix=cap_mix, theme=(theme or None),
        instrument_types=[InstrumentType(i) for i in instruments],
        equity_pct=(eq if use_eqd else None), debt_pct=((1.0 - eq) if use_eqd else None),
        target_return_pct=target_pct, target_value=target_val,
    )
    with st.spinner("Fetching the universe from yfinance, constructing, applying guardrails..."):
        out = run_construction(req, mode=mode, enable_news=enable_news)
    render_construction(out)

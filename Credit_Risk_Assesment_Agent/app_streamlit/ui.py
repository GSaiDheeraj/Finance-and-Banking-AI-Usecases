"""
Streamlit UI — SME / Corporate Credit Risk Assessment.

A pure HTTP client of the `credit_risk.api` FastAPI backend (see `credit_risk/api/`) —
this process no longer imports `credit_risk.agent_graph` or touches Postgres directly.
Upload a credit submission (audited accounts across several years, interim statements,
information memorandum) -> the backend indexes it, extracts and scores it -> review the
deterministic credit scorecard, internal rating, PD/LGD, lending decision, the grounded
credit memo, a full trace, and — new in this build — ask follow-up questions, submit a
review, rerun with a correction, and inspect the audit trail, all against the same
persisted assessment.
"""
import os
import time
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st

from credit_risk.schemas import CreditAssessment

API_BASE_URL = os.getenv("CREDIT_RISK_API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Credit Risk Assessment Agent", layout="wide")
st.title("🏦 Credit Risk Assessment — SMEs through Large Corporates")

_BAND_COLOR = {
    "investment_grade_strong": "🟢", "investment_grade": "🟢",
    "sub_investment_grade": "🟡", "speculative": "🟠",
    "distressed": "🔴", "default": "🔴",
}
_DECISION_LABEL = {
    "approve": "Approve",
    "approve_with_conditions": "Approve — with conditions",
    "refer_credit_committee": "Refer to credit committee",
    "decline": "Decline",
}
_CLASS_LABEL = {
    "above_75th": "🟢 Top quartile", "above_median": "🟢 Above median",
    "median": "⚪ Median", "below_median": "🟡 Below median",
    "below_25th": "🔴 Bottom quartile", "not_available": "—",
}
_DIR_ICON = {"improving": "📈", "deteriorating": "📉", "stable": "➡️", "volatile": "🔀"}


def _fmt(v, pct=False):
    if v is None:
        return "—"
    return f"{v:.1%}" if pct else f"{v:.2f}"


# --------------------------------------------------------------------------- #
# API client — every call to the backend goes through one of these
# --------------------------------------------------------------------------- #
def _multipart_fields(data: dict[str, Any]) -> list[tuple[str, tuple[None, str]]]:
    """Encode plain form fields as multipart parts (no filename) via `requests`' `files`
    parameter — needed so the request stays multipart/form-data even when zero files are
    attached, matching what FastAPI's `File(...)` parameter requires regardless of upload
    count (the standard `files={key: (None, value)}` trick)."""
    return [(k, (None, str(v))) for k, v in data.items() if v is not None]


def api_create_assessment(
    files, ticker: str, company_name: str, question: str,
    search_region: Optional[str], enable_web_sentiment: bool, requested_by: str,
) -> dict:
    parts = _multipart_fields({
        "ticker": ticker, "company_name": company_name, "question": question,
        "search_region": search_region, "enable_web_sentiment": enable_web_sentiment,
        "requested_by": requested_by,
    })
    parts += [("files", (f.name, f.getvalue(), "application/pdf")) for f in (files or [])]
    resp = requests.post(f"{API_BASE_URL}/assessments", files=parts, timeout=900)
    resp.raise_for_status()
    return resp.json()


def api_get_assessment(version_id: str) -> dict:
    resp = requests.get(f"{API_BASE_URL}/assessments/{version_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_list_assessments() -> list[dict]:
    resp = requests.get(f"{API_BASE_URL}/assessments", timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_ask_question(version_id: str, question: str) -> str:
    resp = requests.post(
        f"{API_BASE_URL}/assessments/{version_id}/question", json={"question": question}, timeout=120
    )
    resp.raise_for_status()
    return resp.json()["answer"]


def api_submit_review(version_id: str, reviewer: str, action: str, reason: str) -> dict:
    resp = requests.post(
        f"{API_BASE_URL}/assessments/{version_id}/review",
        json={"reviewer": reviewer, "action": action, "reason": reason or None},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def api_rerun_assessment(version_id: str, instruction: str, enable_web_sentiment: bool) -> dict:
    resp = requests.post(
        f"{API_BASE_URL}/assessments/{version_id}/rerun",
        json={"instruction": instruction or None, "enable_web_sentiment": enable_web_sentiment},
        timeout=900,
    )
    resp.raise_for_status()
    return resp.json()


def api_get_audit_trace(version_id: str) -> dict:
    resp = requests.get(f"{API_BASE_URL}/assessments/{version_id}/audit", timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_get_status(version_id: str) -> dict:
    resp = requests.get(f"{API_BASE_URL}/assessments/{version_id}/status", timeout=10)
    resp.raise_for_status()
    return resp.json()


_PHASE_LABEL = {
    "queued": "Queued",
    "indexing_documents": "Indexing documents",
    "extracting_financials": "Extracting financials",
    "enriching_and_scoring": "Enriching data and scoring",
    "writing_memo": "Writing the credit memo",
}
_TERMINAL_STATUSES = {"awaiting_review", "validation_review", "approved", "rejected"}


def poll_until_done(version_id: str) -> dict:
    """Block (with a live spinner) until the background job reaches a terminal status.

    Polls the cheap `/status` endpoint (Redis-backed, no DB read in the common case —
    see `status_tracker.py`) every couple of seconds rather than holding the original
    POST open: the wall-clock wait for the analyst is unchanged, but no single HTTP
    request needs to stay open longer than the poll interval, which is what actually
    avoids browser/proxy/gateway idle timeouts on a multi-minute pipeline.
    """
    placeholder = st.empty()
    while True:
        status = api_get_status(version_id)
        if status["status"] in _TERMINAL_STATUSES:
            placeholder.empty()
            return api_get_assessment(version_id)
        label = _PHASE_LABEL.get(status.get("phase") or "", status["status"].title())
        placeholder.info(f"⏳ {label}...")
        time.sleep(2)


# --------------------------------------------------------------------------- #
# Sidebar — start a new assessment, or reopen a past one
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### Reopen a past assessment")
    try:
        past = api_list_assessments()
    except requests.RequestException as exc:
        past = []
        st.error(f"Can't reach the API at {API_BASE_URL}: {exc}")
    if past:
        labels = {
            f"{p['company_name'] or p['ticker'] or 'Unnamed'} — {p['status']} "
            f"({p['version_id'][:8]})": p["version_id"]
            for p in past
        }
        chosen = st.selectbox("Case history", ["(new assessment)"] + list(labels.keys()))
        if chosen != "(new assessment)" and st.button("Load"):
            st.session_state["version_id"] = labels[chosen]
            st.rerun()

    st.divider()
    st.markdown("### Upload the credit submission")
    st.caption("Audited accounts (multiple years), interim statements, info memorandum.")
    uploaded = st.file_uploader("Financial statements", type=["pdf"], accept_multiple_files=True)

    st.markdown("### Market-data fallback (optional)")
    st.caption("Provide a stock ticker to fill any figure the documents don't contain "
               "from Yahoo Finance (yfinance). Documents always take precedence. With a "
               "ticker you can even run with no PDFs at all.")
    ticker = st.text_input("Ticker", value="", placeholder="e.g. AAPL, TSLA, RELIANCE.NS")

    st.markdown("### Web sentiment (optional)")
    st.caption("Add a company name to also search free public web/news (DuckDuckGo) and "
               "fold the sentiment into the rating. Leave blank to use the name read from "
               "the filing, or disable below to assess on financials alone.")
    company_name = st.text_input("Company name", value="", placeholder="e.g. Acme Manufacturing Ltd")
    search_region = st.text_input("Search region", value="",
                                  help="DuckDuckGo region: wt-wt (worldwide), uk-en, us-en, in-en, "
                                       "... leave blank for the backend's default.")
    enable_web = st.checkbox("Run web-sentiment search", value=True)

    question = st.text_input("Question", value="What drove the rating and is the trend a concern?")
    requested_by = st.text_input("Your name (for the audit trail)", value="local-analyst")
    run = st.button("Run credit assessment", type="primary")

if run:
    if not (uploaded or ticker.strip()):
        st.sidebar.warning("Upload at least one PDF or enter a ticker.")
        st.stop()
    try:
        # Returns almost immediately (202, status="created") — the pipeline itself
        # runs as a background task on the API side; see routes.py's module docstring
        # for why this can't be one long-held request.
        created = api_create_assessment(
            uploaded, ticker.strip(), company_name.strip(), question,
            search_region.strip() or None, enable_web, requested_by.strip() or "local-analyst",
        )
        st.session_state["version_id"] = created["version_id"]
        with st.spinner("Running the assessment..."):
            st.session_state["result"] = poll_until_done(created["version_id"])
    except requests.RequestException as exc:
        st.error(f"Assessment failed: {exc}")
        st.stop()

version_id = st.session_state.get("version_id")
if not version_id:
    st.info("Upload one or more PDFs (or enter a ticker) and click **Run credit assessment**, "
            "or reopen a past case from the sidebar.")
    st.stop()

# Always refetch on load (not just after Run) so a reopened case shows its latest state.
if "result" not in st.session_state or st.session_state["result"]["version_id"] != version_id:
    try:
        loaded = api_get_assessment(version_id)
        # A reopened case might still be mid-flight from a run that outlived the
        # previous page session — resume polling rather than showing a dead end.
        if loaded["status"] in ("created", "processing"):
            with st.spinner("This case is still running — resuming..."):
                loaded = poll_until_done(version_id)
        st.session_state["result"] = loaded
    except requests.RequestException as exc:
        st.error(f"Can't load assessment {version_id}: {exc}")
        st.stop()

result = st.session_state["result"]
if result.get("assessment") is None:
    st.error(f"Assessment {version_id} ended with status `{result['status']}` and produced "
             f"no result — check the API server log for the underlying error.")
    st.stop()

a = CreditAssessment.model_validate(result["assessment"])
sc = a.scorecard

# --- headline ---
band_icon = _BAND_COLOR.get(sc.band.value, "")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Rating grade", sc.grade.value)
c2.metric("Risk band", f"{band_icon} {sc.band.value.replace('_', ' ').title()}")
c3.metric("Score", f"{sc.normalized_score:.0f} / 100")
c4.metric("Decision", _DECISION_LABEL.get(sc.decision.value, sc.decision.value))
c5.metric("Expected loss", f"{sc.expected_loss_pct:.2%}")
if sc.hard_stop_reason:
    st.error(f"**HARD STOP** — {sc.hard_stop_reason}")
st.caption(
    f"**Status:** `{result['status']}`  |  **Version:** `{version_id[:8]}`"
    + (f" (rerun of `{result['parent_version_id'][:8]}`)" if result.get("parent_version_id") else "")
    + f"  |  **Company:** {a.company.company_name or '—'}  |  "
    f"**Industry:** {a.company.industry or '—'} (benchmarked as `{a.benchmark.industry_used}`)  |  "
    f"**Periods:** {', '.join(a.periods) or '—'}  |  "
    f"**PD:** {sc.probability_of_default:.2%}  |  **LGD:** {sc.loss_given_default:.0%}  |  "
    f"**Pricing:** {sc.suggested_pricing or '—'}  |  "
    f"**LLM calls:** {result.get('llm_call_count')}  |  **Latency:** {result.get('latency_ms')} ms"
)

(t_dec, t_ratios, t_bench, t_trend, t_sent, t_memo, t_data,
 t_trace, t_review, t_audit) = st.tabs([
    "⚖️ Decision", "📊 Ratios", "🏭 Benchmark", "📈 Trend", "🌐 Web Sentiment",
    "📝 Credit Memo", "📄 Extracted Data", "🧠 Trace", "✅ Review & Rerun", "🕵️ Audit Trail",
])

with t_dec:
    st.subheader("Credit scorecard")
    st.dataframe(pd.DataFrame([{
        "Factor": f.factor.value.replace("_", " ").title(),
        "Present": "✅" if f.present else "—",
        "Severity": f.severity if f.present else "—",
        "Weight": f.weight,
        "Contribution": f.contribution,
        "Evidence": "; ".join(f.evidence) if f.evidence else "—",
    } for f in sc.factors]), use_container_width=True, hide_index=True)
    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**Recommended conditions**")
        for c in sc.conditions or ["—"]:
            st.markdown(f"- {c}")
    with cc2:
        st.markdown("**Rating mechanics**")
        st.write(f"Raw score: {sc.raw_score} → normalized {sc.normalized_score}/100")
        st.write(f"Grade **{sc.grade.value}** → band **{sc.band.value.replace('_', ' ')}**")
        st.write(f"PD {sc.probability_of_default:.2%} × LGD {sc.loss_given_default:.0%} "
                 f"= EL {sc.expected_loss_pct:.2%}")

with t_ratios:
    st.subheader("Computed ratios by period (deterministic)")
    if a.ratios_by_period:
        # one row per ratio, one column per period
        all_names = list(a.ratios_by_period[-1].ratios.keys())
        rows = []
        for name in all_names:
            row = {"Ratio": name.replace("_", " ").title(),
                   "Category": a.ratios_by_period[-1].ratios[name].category}
            for fr in a.ratios_by_period:
                rv = fr.ratios.get(name)
                row[fr.period] = rv.value if (rv and rv.value is not None) else None
            missing = a.ratios_by_period[-1].ratios[name].missing_inputs
            row["Missing inputs"] = ", ".join(missing) if missing else "—"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.write("No ratios computed — check that financial line items were extracted.")

with t_bench:
    st.subheader(f"Sector benchmark — {a.benchmark.industry_used} "
                 f"(overall: {a.benchmark.overall_position})")
    if a.benchmark.material_gaps:
        st.warning("Material gaps (bottom-quartile on key ratios): "
                   + ", ".join(a.benchmark.material_gaps))
    st.dataframe(pd.DataFrame([{
        "Ratio": c.ratio_name.replace("_", " ").title(),
        "Company": _fmt(c.company_value),
        "Sector p25": _fmt(c.sector_p25),
        "Sector median": _fmt(c.sector_median),
        "Sector p75": _fmt(c.sector_p75),
        "Position": _CLASS_LABEL.get(c.classification, c.classification),
        "Higher better": "↑" if c.higher_is_better else "↓",
    } for c in a.benchmark.comparisons]), use_container_width=True, hide_index=True)

with t_trend:
    st.subheader(f"Multi-period trend — overall trajectory: {a.trend.overall_trajectory}")
    st.caption(f"Management-commentary signal (deterministic keyword read): "
               f"**{a.trend.commentary_signal}**")
    for note in a.trend.notes:
        st.info(note)
    if a.trend.trends:
        st.dataframe(pd.DataFrame([{
            "Ratio": t.ratio_name.replace("_", " ").title(),
            "Direction": f"{_DIR_ICON.get(t.direction, '')} {t.direction}",
            "Magnitude": t.magnitude,
            "Change": f"{t.pct_change:+.1%}" if t.pct_change is not None else "—",
            **{p: (v if v is not None else None) for p, v in zip(t.periods, t.series)},
        } for t in a.trend.trends]), use_container_width=True, hide_index=True)
        # line chart of the headline ratios over time
        chart_rows = []
        for t in a.trend.trends:
            for p, v in zip(t.periods, t.series):
                if v is not None:
                    chart_rows.append({"Period": p, "Ratio": t.ratio_name, "Value": v})
        if chart_rows:
            df = pd.DataFrame(chart_rows).pivot(index="Period", columns="Ratio", values="Value")
            st.line_chart(df)

with t_sent:
    ws = a.web_sentiment
    if not ws:
        st.info("No web-sentiment search ran. Add a company name in the sidebar and "
                "enable the search to include public web/news signals in the rating.")
    else:
        _SENT_ICON = {"positive": "🟢", "neutral": "⚪", "mixed": "🟡", "negative": "🔴"}
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Overall sentiment",
                   f"{_SENT_ICON.get(ws.overall_sentiment, '')} {ws.overall_sentiment.title()}")
        sc2.metric("Right-company confidence", ws.disambiguation_confidence.title())
        sc3.metric("Evidence collected", len(ws.evidence))
        if ws.disambiguation_confidence == "low":
            st.warning("Low confidence that these web results are about the same company — "
                       "treat the sentiment signal with caution. The scorecard already "
                       "discounts low-confidence web evidence.")
        st.markdown("**Summary**"); st.write(ws.sentiment_summary or "—")

        st.markdown("**Adverse findings**")
        if ws.adverse_findings:
            st.dataframe(pd.DataFrame([{
                "Category": f.category, "Severity": f.severity,
                "Summary": f.summary, "Source": f.url or "—",
            } for f in ws.adverse_findings]), use_container_width=True, hide_index=True)
        else:
            st.write("No adverse web/news findings.")

        if ws.positive_highlights:
            st.markdown("**Positive highlights**")
            for p in ws.positive_highlights:
                st.markdown(f"- {p}")

        with st.expander(f"Evidence trail ({len(ws.evidence)} items)"):
            st.dataframe(pd.DataFrame([{
                "Angle": e.dimension.value, "Source": e.source,
                "Title": e.title or "—", "URL": e.url or "—",
                "Published": e.published or "—",
            } for e in ws.evidence]), use_container_width=True, hide_index=True)
        if ws.data_gaps:
            st.caption("Data gaps: " + "; ".join(ws.data_gaps))

with t_memo:
    m = a.memo
    st.subheader("Credit memo (grounded)")
    st.markdown("**Executive summary**"); st.write(m.executive_summary or "—")
    st.markdown("**Financial analysis**"); st.write(m.financial_analysis or "—")
    mc1, mc2 = st.columns(2)
    with mc1:
        st.markdown("**Key strengths**")
        for s in m.key_strengths or ["—"]:
            st.markdown(f"- {s}")
        st.markdown("**Mitigants**")
        for s in m.mitigants or ["—"]:
            st.markdown(f"- {s}")
    with mc2:
        st.markdown("**Key risks**")
        for s in m.key_risks or ["—"]:
            st.markdown(f"- {s}")
        st.markdown("**Monitoring triggers**")
        for s in m.monitoring_triggers or ["—"]:
            st.markdown(f"- {s}")
    st.markdown("**Recommended covenants**")
    for s in m.recommended_covenants or ["—"]:
        st.markdown(f"- {s}")

    if result.get("answer"):
        st.divider()
        st.markdown("**Answer to your question**")
        st.write(result["answer"])

    st.divider()
    st.markdown("**Ask a follow-up question**")
    with st.form("ask_question_form"):
        follow_up = st.text_input("Question", value="")
        if st.form_submit_button("Ask") and follow_up.strip():
            with st.spinner("Answering..."):
                try:
                    answer = api_ask_question(version_id, follow_up.strip())
                    st.session_state["result"]["answer"] = answer
                    st.rerun()
                except requests.RequestException as exc:
                    st.error(f"Question failed: {exc}")

with t_data:
    st.subheader("Extracted line items")

    def _src(li):
        if (li.source_snippet or "").startswith("yfinance:"):
            return f"🌐 {li.source_snippet}"
        return f"📄 p.{li.page}" if li.page else "📄 doc"

    if a.line_items:
        n_yf = sum(1 for li in a.line_items if (li.source_snippet or "").startswith("yfinance:"))
        if n_yf:
            st.caption(f"{n_yf} line item(s) were filled from the yfinance fallback "
                       f"(🌐); the rest came from the uploaded documents (📄).")
        st.dataframe(pd.DataFrame([{
            "Label": li.label,
            "Standardised": li.standardised_label.value,
            "Value": li.value,
            "Unit": li.unit,
            "Currency": li.currency or "—",
            "Period": li.period,
            "Period end": li.period_end_date.isoformat() if li.period_end_date else "—",
            "Length (mo.)": li.period_length_months or "—",
            "Statement": li.statement_type.value,
            "Source": _src(li),
        } for li in a.line_items]), use_container_width=True, hide_index=True)
    else:
        st.write("No line items extracted.")
    st.markdown("**Off-balance-sheet / contingencies**")
    if a.notes:
        st.dataframe(pd.DataFrame([{
            "Category": n.category, "Description": n.description,
            "Amount": n.amount, "Currency": n.currency or "—", "Page": n.page,
        } for n in a.notes]), use_container_width=True, hide_index=True)
    else:
        st.write("No off-balance-sheet items extracted.")
    if a.facility.facility_type or a.facility.amount:
        st.markdown("**Facility request**")
        st.write(f"{a.facility.facility_type or '—'} — "
                 f"{a.facility.amount or '—'} {a.facility.currency or ''} — "
                 f"{a.facility.purpose or '—'} ({a.facility.tenor or 'tenor n/a'})")

    rec = a.reconciliation
    if rec and rec.yfinance_attempted:
        st.divider()
        st.markdown("### 🔗 Data reconciliation (yfinance fallback)")
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Filled from yfinance", rec.cells_filled_from_yfinance)
        rc2.metric("Doc vs yfinance mismatches", len(rec.discrepancies))
        rc3.metric("Ratios still missing inputs", len(rec.ratio_gaps))
        for n in rec.notes:
            st.warning(n)
        if rec.discrepancies:
            st.markdown("**Discrepancies** (document value kept; flagged for review)")
            for d in rec.discrepancies:
                st.markdown(f"- ⚠️ {d}")
        if rec.ratio_gaps:
            st.markdown("**Ratios that could not be computed**")
            for g in rec.ratio_gaps:
                st.markdown(f"- {g}")
        if rec.filled:
            with st.expander(f"Cells filled from yfinance ({len(rec.filled)})"):
                for f in rec.filled:
                    st.markdown(f"- 🌐 {f}")

with t_trace:
    for i, step in enumerate(result.get("reasoning", []), 1):
        st.markdown(f"**Step {i}**"); st.markdown(step); st.divider()

with t_review:
    st.subheader("Analyst / credit-committee review")
    st.caption("FR-8.2: a draft stays a draft until an authorized reviewer approves it. "
               "A correction is recorded alongside the original — it doesn't silently "
               "replace history — and only takes effect once you rerun the case.")
    with st.form("review_form"):
        reviewer = st.text_input("Reviewer name", value=requested_by or "")
        action = st.selectbox("Action", ["approved", "corrected", "rejected"])
        reason = st.text_area("Reason / notes", value="")
        if st.form_submit_button("Submit review"):
            try:
                api_submit_review(version_id, reviewer or "anonymous", action, reason)
                st.success(f"Review recorded: {action}.")
                st.session_state["result"] = api_get_assessment(version_id)
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"Review failed: {exc}")

    st.divider()
    st.subheader("Rerun with a correction")
    st.caption("Reprocesses this case's own documents with an updated instruction — "
               "creates a NEW version; this one is never overwritten.")
    with st.form("rerun_form"):
        instruction = st.text_area("What changed / what to correct", value="")
        rerun_web = st.checkbox("Re-run web-sentiment search too", value=bool(a.web_sentiment))
        if st.form_submit_button("Rerun assessment"):
            try:
                created = api_rerun_assessment(version_id, instruction, rerun_web)
                st.session_state["version_id"] = created["version_id"]
                with st.spinner("Rerunning..."):
                    st.session_state["result"] = poll_until_done(created["version_id"])
                st.rerun()
            except requests.RequestException as exc:
                st.error(f"Rerun failed: {exc}")

with t_audit:
    st.subheader("Audit trail")
    try:
        audit = api_get_audit_trace(version_id)
    except requests.RequestException as exc:
        st.error(f"Can't load audit trace: {exc}")
        audit = None
    if audit:
        st.write(f"**Model:** {audit.get('model_name') or '—'}  |  "
                 f"**Embedding model:** {audit.get('embed_model_name') or '—'}  |  "
                 f"**Requested by:** {audit['requested_by']}  |  "
                 f"**Requested at:** {audit['requested_at']}")
        st.markdown("**Source documents**")
        if audit["documents"]:
            st.dataframe(pd.DataFrame(audit["documents"]), use_container_width=True, hide_index=True)
        else:
            st.write("No documents (ticker-only assessment).")
        st.markdown("**Rating factors**")
        st.dataframe(pd.DataFrame(audit["rating_factors"]), use_container_width=True, hide_index=True)
        st.markdown("**Review history**")
        if audit["reviews"]:
            st.dataframe(pd.DataFrame(audit["reviews"]), use_container_width=True, hide_index=True)
        else:
            st.write("No reviews recorded yet.")

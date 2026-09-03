"""
Streamlit UI — Subject 360° (name-driven public-information profile).

Type a person's name and a few details; the agent searches free public web sources,
builds a 360° picture (who they are, their companies and how those are doing, any
negative news, political links, public behaviour, family/partner), and runs the same
deterministic risk rating used by the document flow.

Run:  streamlit run app_streamlit/ui_subject.py
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from onboarding_risk.osint.search import search_available  # noqa: E402
from onboarding_risk.schemas import SubjectSeed  # noqa: E402
from onboarding_risk.subject_graph import run_subject_360  # noqa: E402

st.set_page_config(page_title="Subject 360° Profiler", layout="wide")
st.title("🔎 Subject 360° — Public-Information Profile & Risk Read")
st.caption(
    "Gathers only **public** web information for customer due diligence. No logins, no "
    "private accounts, no scraping behind sign-in walls. Results may be incomplete and "
    "should be verified before any decision."
)

_BAND_COLOR = {"low": "🟢", "medium": "🟡", "high": "🟠", "prohibited": "🔴"}
_DECISION_LABEL = {
    "approve_standard_cdd": "Approve — standard checks",
    "approve_with_edd": "Approve — with deeper checks",
    "escalate_mlro": "Escalate to compliance",
    "decline": "Decline",
}


def _split(text: str):
    return [t.strip() for t in (text or "").replace("\n", ",").split(",") if t.strip()]


with st.sidebar:
    st.markdown("### Who are we checking?")
    full_name = st.text_input("Full name *", placeholder="e.g. Jane A. Smith")
    location = st.text_input("Location (city / country)", placeholder="e.g. London, UK")
    nationality = st.text_input("Nationality", placeholder="e.g. British")
    approx_age = st.text_input("Approx. age / DOB", placeholder="e.g. ~55 or 1969")
    education = st.text_input("Education (comma-separated)", placeholder="e.g. LSE, Harvard")
    work_history = st.text_input("Work history (comma-separated)", placeholder="e.g. Acme Capital CEO")
    known_companies = st.text_input("Known companies (comma-separated)", placeholder="e.g. Acme Capital, Beta Ltd")
    aliases = st.text_input("Aliases (comma-separated)")
    search_region = st.text_input("Search region (country code)", value="in",
                                  help="Biases web search, e.g. 'in' for India, 'us' for USA. Clear for worldwide.")
    purpose = st.text_input("Relationship purpose", placeholder="e.g. discretionary wealth mandate")
    question = st.text_input("Optional question", value="Summarize the main risks found.")
    run = st.button("Build 360° profile", type="primary")

if not search_available():
    st.warning(
        "Free search backend (`ddgs`) is not installed, so live web results will be empty. "
        "Install it with `pip install ddgs wikipedia beautifulsoup4`. The pipeline still runs."
    )

if not (run and full_name.strip()):
    st.info("Enter at least a **full name** in the sidebar, then click **Build 360° profile**.")
    st.stop()

seed = SubjectSeed(
    full_name=full_name.strip(),
    aliases=_split(aliases),
    approx_age=approx_age or None,
    location=location or None,
    nationality=nationality or None,
    education=_split(education),
    work_history=_split(work_history),
    known_companies=_split(known_companies),
    search_region=(search_region.strip().lower() or None),
    relationship_purpose=purpose or None,
)

with st.spinner("Searching public sources and building the profile..."):
    result = run_subject_360(seed, question)

a = result["assessment"]
p = result["profile"]
sc = a.scorecard
metrics = result["metrics"]

# --- headline ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("Risk band", f"{_BAND_COLOR.get(sc.band.value, '')} {sc.band.value.upper()}")
c2.metric("Score", f"{sc.normalized_score:.0f} / 100")
c3.metric("Suggested action", _DECISION_LABEL.get(sc.decision.value, sc.decision.value))
c4.metric("Right person?", p.disambiguation_confidence.upper())
if sc.hard_stop_reason:
    st.error(f"**STOP** — {sc.hard_stop_reason}")
if p.disambiguation_confidence == "low":
    st.warning(
        "Low confidence that the public results are about the same person. Add more detail "
        "(location, employer, age) to narrow it down."
    )
st.caption(
    f"**Subject:** {p.seed.full_name}  |  **Evidence items:** {metrics.get('evidence_items')}  |  "
    f"**LLM calls:** {metrics.get('llm_calls')}  |  **Latency:** {metrics.get('latency_ms')} ms"
)

t_overview, t_prof, t_adverse, t_people, t_behav, t_edd, t_evidence, t_trace = st.tabs(
    ["🧭 Overview", "💼 Professional", "⚠️ Negative news", "👪 People", "🧠 Behaviour",
     "📝 Risk write-up", "🔗 Evidence", "🪜 Trace"]
)

with t_overview:
    st.subheader("Who they are")
    st.write(p.biography or "No public biography found.")
    if p.wealth_summary:
        st.markdown("**Wealth (public info)**"); st.write(p.wealth_summary)
    if p.source_of_wealth_signal:
        st.markdown(
            f"**Likely source of wealth:** {p.source_of_wealth_signal}  "
            f"({'corroborated' if p.source_of_wealth_corroborated else 'not corroborated'})"
        )
    if p.pep_indicators:
        st.markdown("**Political-exposure signals**")
        for i in p.pep_indicators:
            st.markdown(f"- {i}")
    if p.data_gaps:
        st.markdown("**What we could NOT find**")
        for g in p.data_gaps:
            st.markdown(f"- {g}")

with t_prof:
    st.subheader("Professional history")
    st.write(p.professional_summary or "No public professional information found.")
    if p.company_affiliations:
        st.markdown("**Companies & how they're doing**")
        st.dataframe(pd.DataFrame([{
            "Company": c.company, "Role": c.role or "—", "Status": c.status or "—",
            "Performance (public)": c.performance_summary or "—",
        } for c in p.company_affiliations]), use_container_width=True, hide_index=True)

with t_adverse:
    st.subheader("Negative news & flags")
    if p.adverse_media:
        st.dataframe(pd.DataFrame([{
            "Category": a_.category, "Severity": a_.severity, "Summary": a_.summary,
            "Link": a_.url or "—",
        } for a_ in p.adverse_media]), use_container_width=True, hide_index=True)
    else:
        st.success("No negative news found in public sources.")

with t_people:
    st.subheader("Family, partner & associates")
    if p.relationships:
        st.dataframe(pd.DataFrame([{
            "Name": r.name, "Relation": r.relation,
            "Politically exposed": r.is_pep, "Details": r.details or "—",
        } for r in p.relationships]), use_container_width=True, hide_index=True)
    else:
        st.write("No related people found in public sources.")

with t_behav:
    st.subheader("Behaviour read (public information only)")
    st.caption("A cautious, fact-based read from public posts/interviews — not a clinical judgement.")
    b = p.behavioral
    st.write(b.summary or "—")
    st.markdown(f"**Public sentiment:** {b.public_sentiment or '—'}  |  **Confidence:** {b.confidence}")
    cL, cR = st.columns(2)
    with cL:
        st.markdown("**Observed traits**")
        for t in b.traits or ["—"]:
            st.markdown(f"- {t}")
    with cR:
        st.markdown("**Caution flags**")
        for t in b.risk_indicators or ["—"]:
            st.markdown(f"- {t}")

with t_edd:
    st.subheader("Risk scorecard")
    st.dataframe(pd.DataFrame([{
        "Factor": f.factor.value, "Present": "✅" if f.present else "—",
        "Severity": f.severity if f.present else "—", "Contribution": f.contribution,
        "Why": "; ".join(f.evidence) if f.evidence else "—",
    } for f in sc.factors]), use_container_width=True, hide_index=True)
    r = a.edd_rationale
    st.markdown("**Summary**"); st.write(r.risk_summary or "—")
    st.markdown("**Main reasons**")
    for d in r.key_drivers or ["—"]:
        st.markdown(f"- {d}")
    st.markdown("**Extra checks to do**")
    for m in r.edd_measures or ["—"]:
        st.markdown(f"- {m}")
    st.markdown("**Information still needed**")
    for i in r.information_required or ["—"]:
        st.markdown(f"- {i}")
    if result["answer"]:
        st.divider(); st.markdown("**Answer to your question**"); st.write(result["answer"])

with t_evidence:
    st.subheader("Sources used")
    if p.evidence:
        st.dataframe(pd.DataFrame([{
            "Angle": e.dimension.value, "Source": e.source, "Title": e.title or "—",
            "Snippet": (e.snippet or "")[:160], "Link": e.url or "—",
        } for e in p.evidence]), use_container_width=True, hide_index=True)
    else:
        st.write("No public evidence was retrieved (search backend offline or no results).")

with t_trace:
    for i, step in enumerate(result["reasoning"], 1):
        st.markdown(f"**Step {i}**"); st.markdown(step); st.divider()

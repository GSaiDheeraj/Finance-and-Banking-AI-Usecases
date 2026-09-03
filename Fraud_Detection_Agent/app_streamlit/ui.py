"""
Streamlit UI — HNI/UHNI Customer-Onboarding Risk Scoring.

Upload an onboarding pack (KYC form, trust deed, ownership chart, source-of-wealth
declaration) -> resolve the ownership graph & UBOs -> review the deterministic risk
scorecard and decision, screening hits, the grounded EDD rationale, and a full trace.
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from onboarding_risk.agent_graph import run_onboarding_assessment  # noqa: E402

st.set_page_config(page_title="Onboarding Risk-Scoring Agent", layout="wide")
st.title("🛡️ Customer-Onboarding Risk Scoring — HNI / UHNI & Complex Structures")

_BAND_COLOR = {"low": "🟢", "medium": "🟡", "high": "🟠", "prohibited": "🔴"}
_DECISION_LABEL = {
    "approve_standard_cdd": "Approve — standard CDD",
    "approve_with_edd": "Approve — with EDD",
    "escalate_mlro": "Escalate to MLRO",
    "decline": "Decline",
}


def _to_temp(uploaded) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded.getvalue())
        return tmp.name


with st.sidebar:
    st.markdown("### Upload the onboarding pack")
    st.caption("KYC form, trust deed / charter, ownership chart, source-of-wealth, IDs.")
    uploaded = st.file_uploader("Documents", type=["pdf"], accept_multiple_files=True)
    question = st.text_input("Question", value="Who are the UBOs above 25% and why was EDD triggered?")
    run = st.button("Run onboarding assessment", type="primary")

if not (run and uploaded):
    st.info("Upload one or more PDFs and click **Run onboarding assessment**.")
    st.stop()

paths = [_to_temp(u) for u in uploaded]
with st.spinner(f"Indexing {len(paths)} document(s), extracting parties, scoring..."):
    result = run_onboarding_assessment(paths, question)

a = result["assessment"]
sc = a.scorecard
og = a.ownership
metrics = result["metrics"]

# --- headline ---
band_icon = _BAND_COLOR.get(sc.band.value, "")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Risk band", f"{band_icon} {sc.band.value.upper()}")
c2.metric("Score", f"{sc.normalized_score:.0f} / 100")
c3.metric("Decision", _DECISION_LABEL.get(sc.decision.value, sc.decision.value))
c4.metric("EDD required", "Yes" if sc.edd_required else "No")
if sc.hard_stop_reason:
    st.error(f"**HARD STOP** — {sc.hard_stop_reason}")
st.caption(
    f"**Applicant:** {a.case.applicant_name or '—'}  |  **Product:** {a.case.product or '—'}  |  "
    f"**Parties:** {metrics.get('parties')}  |  **UBOs:** {metrics.get('ubos')}  |  "
    f"**LLM calls:** {metrics.get('llm_calls')}  |  **Latency:** {metrics.get('latency_ms')} ms"
)

t_dec, t_own, t_screen, t_parties, t_edd, t_trace = st.tabs(
    ["⚖️ Decision", "🕸️ Ownership / UBOs", "🔎 Screening", "👥 Parties", "📝 EDD Rationale", "🧠 Trace"]
)

with t_dec:
    st.subheader("Risk scorecard")
    st.dataframe(pd.DataFrame([{
        "Factor": f.factor.value,
        "Present": "✅" if f.present else "—",
        "Severity": f.severity if f.present else "—",
        "Weight": f.weight,
        "Contribution": f.contribution,
        "Evidence": "; ".join(f.evidence) if f.evidence else "—",
    } for f in sc.factors]), use_container_width=True, hide_index=True)
    st.markdown("**EDD triggers fired:** " +
                (", ".join(t.value for t in sc.triggers) if sc.triggers else "—"))

with t_own:
    st.subheader("Beneficial owners (deterministic)")
    if og.ubos:
        st.dataframe(pd.DataFrame([{
            "UBO": u.name,
            "Effective ownership %": u.effective_ownership_pct,
            "Basis": u.basis,
            "Control roles": ", ".join(u.control_roles) or "—",
            "Path(s)": " | ".join(u.paths) if u.paths else "—",
            "Pages": ", ".join(str(p) for p in u.pages) or "—",
        } for u in og.ubos]), use_container_width=True, hide_index=True)
    else:
        st.write("No UBOs resolved.")
    st.caption(
        f"Entities: {og.num_entities}  |  Parties: {og.num_parties}  |  "
        f"Layering depth: {og.max_layering_depth}  |  Nominee: {og.has_nominee}  |  "
        f"Bearer shares: {og.has_bearer_shares}  |  Circular: {og.has_circular_ownership}"
    )
    if a.edges:
        st.markdown("**Ownership edges**")
        id_to_name = {p.party_id: p.name for p in a.parties}
        st.dataframe(pd.DataFrame([{
            "Owner": id_to_name.get(e.owner_id, e.owner_id),
            "Owns": id_to_name.get(e.owned_id, e.owned_id),
            "%": e.percentage,
            "Interest": e.interest_type.value,
            "Page": e.page,
        } for e in a.edges]), use_container_width=True, hide_index=True)

with t_screen:
    st.subheader("Screening hits")
    if a.watchlist_hits:
        st.dataframe(pd.DataFrame([{
            "Party": h.party_name, "List": h.list_type, "Matched": h.matched_name,
            "Score": h.match_score, "Source": h.source_list or "—",
            "Tier": h.tier or "—", "Details": h.details or "—",
        } for h in a.watchlist_hits]), use_container_width=True, hide_index=True)
    else:
        st.write("No PEP / sanctions / adverse-media hits.")
    st.markdown("**High-risk geographies**")
    if a.geography_hits:
        st.dataframe(pd.DataFrame([{
            "Party": g.party_name, "Country": g.country, "Tier": g.tier,
            "Offshore": "✅" if g.offshore else "—",
        } for g in a.geography_hits]), use_container_width=True, hide_index=True)
    else:
        st.write("No high-risk jurisdictions.")

with t_parties:
    st.subheader("Parties & roles")
    roles_by_party = {}
    for r in a.relationships:
        roles_by_party.setdefault(r.party_id, []).append(r.role.value)
    st.dataframe(pd.DataFrame([{
        "ID": p.party_id, "Name": p.name, "Type": p.party_type.value,
        "Country": p.country or "—", "Nationality": p.nationality or "—",
        "Roles": ", ".join(roles_by_party.get(p.party_id, [])) or "—",
        "Declared PEP": p.declared_pep, "Page": p.page,
    } for p in a.parties]), use_container_width=True, hide_index=True)

    st.markdown("**Source of wealth**")
    if a.source_of_wealth:
        id_to_name = {p.party_id: p.name for p in a.parties}
        st.dataframe(pd.DataFrame([{
            "Party": id_to_name.get(s.party_id, s.party_id),
            "Declared source": s.declared_source or "—",
            "Corroborated": s.corroborating_evidence_present,
            "Narrative": s.narrative or "—", "Page": s.page,
        } for s in a.source_of_wealth]), use_container_width=True, hide_index=True)
    else:
        st.write("No source-of-wealth declarations extracted.")

with t_edd:
    r = a.edd_rationale
    st.subheader("EDD rationale (grounded)")
    st.markdown("**Risk summary**"); st.write(r.risk_summary or "—")
    st.markdown("**Key drivers**")
    for d in r.key_drivers or ["—"]:
        st.markdown(f"- {d}")
    st.markdown("**EDD measures**")
    for m in r.edd_measures or ["—"]:
        st.markdown(f"- {m}")
    st.markdown("**Information required**")
    for i in r.information_required or ["—"]:
        st.markdown(f"- {i}")
    st.markdown("**Watch items**")
    for w in r.watch_items or ["—"]:
        st.markdown(f"- {w}")

    if result["answer"]:
        st.divider()
        st.markdown("**Answer to your question**")
        st.write(result["answer"])

with t_trace:
    for i, step in enumerate(result["reasoning"], 1):
        st.markdown(f"**Step {i}**"); st.markdown(step); st.divider()

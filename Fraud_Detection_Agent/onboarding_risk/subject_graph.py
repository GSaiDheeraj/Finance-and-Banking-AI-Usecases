"""
Orchestration for the name-driven OSINT 360° flow.

Give it a person's name and a few details; it gathers their public footprint from free
web sources, synthesizes a 360° profile, and then runs the SAME deterministic engine the
PDF flow uses (screening + scoring + EDD) so the final risk rating is reproducible.

    SubjectSeed (name + details)
      -> collect_evidence            (free web/news/wikipedia search)
      -> synthesize_profile          (LLM, grounded in the evidence)
      -> profile_to_parties          (deterministic: subject + spouse + companies)
      -> screen_parties              (deterministic list match) + OSINT adverse/PEP signals
      -> resolve_ubos / score_case   (deterministic rating + decision)
      -> synthesize_edd_rationale    (LLM, grounded in the score) + optional Q&A

NOTE ON REPRODUCIBILITY: the *scoring* is deterministic given a fixed set of findings.
The *collection* step depends on the live web, so re-running on a different day may
surface different evidence — that is the nature of open-source research, and the trace
records exactly what was used.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_llm
from .edd import synthesize_edd_rationale
from .osint.collect import collect_evidence
from .osint.profile import synthesize_profile
from .ownership import resolve_ubos
from .schemas import (
    CaseMetadata,
    GeographyHit,
    OnboardingAssessment,
    Party,
    PartyType,
    Profile360,
    Relationship,
    RoleType,
    SourceOfWealth,
    SubjectSeed,
    WatchlistHit,
)
from .scoring import score_case
from .screening import screen_parties

_STRONG_PEP_HINTS = ("minister", "head of state", "president", "governor",
                     "senator", "member of parliament", "ambassador")

# How severe a web adverse-media finding can be, by category. Gossip/reputational items
# are capped low; only hard categories (alleged fraud, regulatory/criminal action) can be
# high. This keeps soft negative news from dominating the score.
_ADVERSE_CATEGORY_CEILING = {
    "fraud": "high", "regulatory": "high", "criminal": "high",
    "litigation": "medium", "sanctions": "high",
    "reputational": "low", "other": "low",
}
_SEV_RANK = {"low": 1, "medium": 2, "high": 3}


def _adverse_hit_tier(category: str, severity: str) -> str:
    """Effective tier = the model's severity, capped by what the category can justify."""
    sev = (severity or "medium").lower()
    ceiling = _ADVERSE_CATEGORY_CEILING.get((category or "other").lower(), "medium")
    rank = min(_SEV_RANK.get(sev, 2), _SEV_RANK.get(ceiling, 2))
    return {1: "low", 2: "medium", 3: "high"}[rank]


def profile_to_parties(profile: Profile360) -> Tuple[
    List[Party], List[Relationship], List[WatchlistHit], List[SourceOfWealth]
]:
    """Turn the OSINT profile into the deterministic engine's inputs.

    Creates the subject (S1), each named relationship (R*) and each affiliated company
    (C*) as parties, plus OSINT-derived watchlist hits (adverse media / PEP found on the
    web) that supplement the static list screening.
    """
    seed = profile.seed
    parties: List[Party] = []
    relationships: List[Relationship] = []
    osint_hits: List[WatchlistHit] = []

    subject = Party(
        party_id="S1", name=seed.full_name, party_type=PartyType.INDIVIDUAL,
        country=seed.nationality or seed.location, nationality=seed.nationality,
        date_of_birth_or_incorporation=seed.date_of_birth,
    )
    parties.append(subject)
    relationships.append(Relationship(party_id="S1", role=RoleType.APPLICANT))

    # related persons (spouse / family / associates)
    for i, r in enumerate(profile.relationships, 1):
        pid = f"R{i}"
        parties.append(Party(party_id=pid, name=r.name, party_type=PartyType.INDIVIDUAL))
        role = RoleType.SPOUSE if r.relation.lower() in ("spouse", "wife", "husband", "partner") \
            else RoleType.AUTHORIZED_PERSON
        relationships.append(Relationship(party_id=pid, role=role, related_to="S1"))
        if r.is_pep:
            osint_hits.append(WatchlistHit(
                party_id=pid, party_name=r.name, list_type="pep", matched_name=r.name,
                match_score=1.0, source_list="osint:web",
                details=f"Related party ({r.relation}) flagged as politically exposed.",
                tier="medium",
            ))

    # affiliated companies (directorships / partnerships)
    for i, c in enumerate(profile.company_affiliations, 1):
        pid = f"C{i}"
        parties.append(Party(party_id=pid, name=c.company, party_type=PartyType.OPERATING_COMPANY))
        role = RoleType.DIRECTOR
        rl = (c.role or "").lower()
        if "partner" in rl:
            role = RoleType.PARTNER
        elif "shareholder" in rl or "owner" in rl:
            role = RoleType.SHAREHOLDER
        relationships.append(Relationship(party_id=pid, role=role, related_to="S1"))

    # OSINT adverse-media findings -> adverse_media hits on the subject.
    # Carry the per-finding severity (capped by category) so scoring can weight them
    # instead of treating every web mention as 'high'.
    for a in profile.adverse_media:
        osint_hits.append(WatchlistHit(
            party_id="S1", party_name=seed.full_name, list_type="adverse_media",
            matched_name=seed.full_name, match_score=1.0, source_list="osint:web",
            details=f"[{a.category}] {a.summary}",
            tier=_adverse_hit_tier(a.category, a.severity),
        ))

    # OSINT political-exposure indicators -> PEP hit on the subject
    if profile.pep_indicators:
        blob = " ".join(profile.pep_indicators).lower()
        tier = "high" if any(h in blob for h in _STRONG_PEP_HINTS) else "medium"
        osint_hits.append(WatchlistHit(
            party_id="S1", party_name=seed.full_name, list_type="pep",
            matched_name=seed.full_name, match_score=1.0, source_list="osint:web",
            details="; ".join(profile.pep_indicators), tier=tier,
        ))

    # source of wealth from public info
    sow = [SourceOfWealth(
        party_id="S1",
        declared_source=profile.source_of_wealth_signal or "unknown",
        narrative=profile.wealth_summary,
        corroborating_evidence_present=profile.source_of_wealth_corroborated,
    )]
    return parties, relationships, osint_hits, sow


def assess_subject(seed: SubjectSeed) -> Tuple[OnboardingAssessment, Profile360, List[str]]:
    """Collect, profile, and score a single subject. Returns (assessment, profile, trace)."""
    trace: List[str] = []

    evidence = collect_evidence(seed)
    trace.append(
        f"**Collected** {len(evidence)} public evidence item(s) across "
        f"{len({e.dimension for e in evidence})} research angle(s)."
    )

    profile = synthesize_profile(seed, evidence)
    trace.append(
        f"**Profile (LLM, grounded)** → disambiguation={profile.disambiguation_confidence}, "
        f"companies={len(profile.company_affiliations)}, adverse={len(profile.adverse_media)}, "
        f"pep_indicators={len(profile.pep_indicators)}, relationships={len(profile.relationships)}"
    )

    parties, relationships, osint_hits, sow = profile_to_parties(profile)

    # deterministic: static-list screening + OSINT-derived signals
    list_hits, geo_hits = screen_parties(parties)
    watchlist_hits = list_hits + osint_hits

    ownership = resolve_ubos(parties, [], relationships, applicant_party_id="S1")
    scorecard = score_case(ownership, watchlist_hits, geo_hits, sow, [])

    trace.append(
        f"**Scorecard (deterministic)** → score={scorecard.normalized_score}/100, "
        f"band=`{scorecard.band.value}`, EDD_required={scorecard.edd_required}, "
        f"decision=`{scorecard.decision.value}`"
        + (f", HARD STOP: {scorecard.hard_stop_reason}" if scorecard.hard_stop_reason else "")
    )

    assessment = OnboardingAssessment(
        case=CaseMetadata(
            applicant_name=seed.full_name, applicant_party_id="S1",
            product=seed.relationship_purpose, relationship_purpose=seed.relationship_purpose,
        ),
        parties=parties, edges=[], relationships=relationships,
        source_of_wealth=sow, identity_checks=[], ownership=ownership,
        watchlist_hits=watchlist_hits, geography_hits=geo_hits, scorecard=scorecard,
        profile_360=profile,
    )
    assessment.edd_rationale = synthesize_edd_rationale(assessment)
    return assessment, profile, trace


def _answer_over_profile(profile: Profile360, assessment: OnboardingAssessment, question: str) -> str:
    compact = {
        "name": profile.seed.full_name,
        "disambiguation_confidence": profile.disambiguation_confidence,
        "biography": profile.biography,
        "professional_summary": profile.professional_summary,
        "companies": [{"company": c.company, "role": c.role,
                       "performance": c.performance_summary} for c in profile.company_affiliations],
        "adverse_media": [{"category": a.category, "summary": a.summary} for a in profile.adverse_media],
        "pep_indicators": profile.pep_indicators,
        "wealth_summary": profile.wealth_summary,
        "relationships": [{"name": r.name, "relation": r.relation, "is_pep": r.is_pep}
                          for r in profile.relationships],
        "behavioral": profile.behavioral.model_dump(),
        "decision": assessment.scorecard.decision.value,
        "band": assessment.scorecard.band.value,
        "data_gaps": profile.data_gaps,
    }
    system = SystemMessage(content=(
        "You answer questions about a public-information profile and its risk assessment. "
        "Use ONLY the supplied facts. If the answer is not present, say it was not found in "
        "public sources. Be clear that the information is from public web search and may be "
        "incomplete. Plain language, no jargon."
    ))
    user = HumanMessage(content=(
        f"Question:\n{question}\n\nProfile:\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Answer concisely."
    ))
    return get_llm().invoke([system, user]).content


def run_subject_360(seed: SubjectSeed, question: str = "") -> Dict[str, Any]:
    """Full name-driven 360° assessment. Returns assessment, profile, answer, trace, metrics."""
    start = time.time()
    assessment, profile, trace = assess_subject(seed)
    metrics: Dict[str, Any] = {
        "evidence_items": len(profile.evidence),
        "llm_calls": 2,                          # profile synthesis + EDD rationale
        "band": assessment.scorecard.band.value,
        "decision": assessment.scorecard.decision.value,
    }

    answer = ""
    if question:
        answer = _answer_over_profile(profile, assessment, question)
        metrics["llm_calls"] += 1

    metrics["latency_ms"] = round((time.time() - start) * 1000.0, 2)
    return {
        "assessment": assessment,
        "profile": profile,
        "answer": answer,
        "reasoning": trace,
        "metrics": metrics,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print('  python -m onboarding_risk.subject_graph "Full Name" '
              '[--location "City"] [--nationality "Country"] [--ask "question"]')
        sys.exit(1)

    args = sys.argv[1:]
    name = args[0]
    seed_kwargs: Dict[str, Any] = {"full_name": name}
    q = ""
    i = 1
    while i < len(args):
        if args[i] == "--location" and i + 1 < len(args):
            seed_kwargs["location"] = args[i + 1]; i += 2
        elif args[i] == "--nationality" and i + 1 < len(args):
            seed_kwargs["nationality"] = args[i + 1]; i += 2
        elif args[i] == "--ask" and i + 1 < len(args):
            q = args[i + 1]; i += 2
        else:
            i += 1

    out = run_subject_360(SubjectSeed(**seed_kwargs), q)
    a, p = out["assessment"], out["profile"]
    sc = a.scorecard
    print(f"\n=== {p.seed.full_name} — public profile ===")
    print("Disambiguation confidence:", p.disambiguation_confidence)
    print("Biography:", p.biography[:500])
    print("\n=== RISK ===")
    print(f"band={sc.band.value}, decision={sc.decision.value}, score={sc.normalized_score}/100")
    print("triggers:", [t.value for t in sc.triggers])
    print("\n=== BEHAVIORAL READ ===\n", p.behavioral.summary)
    if out["answer"]:
        print("\n=== ANSWER ===\n", out["answer"])
    print("\n=== METRICS ===\n", out["metrics"])

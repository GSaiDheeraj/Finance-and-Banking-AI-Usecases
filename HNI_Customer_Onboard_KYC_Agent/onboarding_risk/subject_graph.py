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
import sys
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_llm
from .edd import synthesize_edd_rationale
from .osint.collect import collect_evidence
from .osint.profile import synthesize_profile
from .ownership import resolve_ubos
from .schemas import (
    CaseMetadata,
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

_STRONG_PEP_HINTS = (
    "minister",
    "head of state",
    "president",
    "governor",
    "senator",
    "member of parliament",
    "ambassador",
)

# How severe a web adverse-media finding can be, by category. Gossip/reputational items
# are capped low; only hard categories (alleged fraud, regulatory/criminal action) can be
# high. This keeps soft negative news from dominating the score.
_ADVERSE_CATEGORY_CEILING = {
    "fraud": "high",
    "regulatory": "high",
    "criminal": "high",
    "litigation": "medium",
    "sanctions": "high",
    "reputational": "low",
    "other": "low",
}
_SEV_RANK = {"low": 1, "medium": 2, "high": 3}


_SUBJECT_PARTY_ID = "S1"
_OSINT_SOURCE = "osint:web"
_FULL_MATCH_SCORE = 1.0
_SPOUSE_RELATIONS = frozenset({"spouse", "wife", "husband", "partner"})
_MS_PER_SECOND = 1000.0


def _adverse_hit_tier(category: str, severity: str) -> str:
    """Effective tier = the model's severity, capped by what the category can justify."""
    sev = (severity or "medium").lower()
    ceiling = _ADVERSE_CATEGORY_CEILING.get((category or "other").lower(), "medium")
    rank = min(_SEV_RANK.get(sev, 2), _SEV_RANK.get(ceiling, 2))
    return {1: "low", 2: "medium", 3: "high"}[rank]


def _build_subject_party(seed: SubjectSeed) -> Party:
    return Party(
        party_id=_SUBJECT_PARTY_ID,
        name=seed.full_name,
        party_type=PartyType.INDIVIDUAL,
        country=seed.nationality or seed.location,
        nationality=seed.nationality,
        date_of_birth_or_incorporation=seed.date_of_birth,
    )


def _relationship_role(relation: str) -> RoleType:
    if relation.lower() in _SPOUSE_RELATIONS:
        return RoleType.SPOUSE
    return RoleType.AUTHORIZED_PERSON


def _build_related_parties(
    profile: Profile360,
    parties: list[Party],
    relationships: list[Relationship],
    osint_hits: list[WatchlistHit],
) -> None:
    for index, finding in enumerate(profile.relationships, 1):
        party_id = f"R{index}"
        parties.append(Party(party_id=party_id, name=finding.name, party_type=PartyType.INDIVIDUAL))
        relationships.append(
            Relationship(
                party_id=party_id,
                role=_relationship_role(finding.relation),
                related_to=_SUBJECT_PARTY_ID,
            )
        )
        if finding.is_pep:
            osint_hits.append(
                WatchlistHit(
                    party_id=party_id,
                    party_name=finding.name,
                    list_type="pep",
                    matched_name=finding.name,
                    match_score=_FULL_MATCH_SCORE,
                    source_list=_OSINT_SOURCE,
                    details=f"Related party ({finding.relation}) flagged as politically exposed.",
                    tier="medium",
                )
            )


def _company_role(company_role: str | None) -> RoleType:
    role_text = (company_role or "").lower()
    if "partner" in role_text:
        return RoleType.PARTNER
    if "shareholder" in role_text or "owner" in role_text:
        return RoleType.SHAREHOLDER
    return RoleType.DIRECTOR


def _build_company_parties(
    profile: Profile360,
    parties: list[Party],
    relationships: list[Relationship],
) -> None:
    for index, affiliation in enumerate(profile.company_affiliations, 1):
        party_id = f"C{index}"
        parties.append(
            Party(
                party_id=party_id,
                name=affiliation.company,
                party_type=PartyType.OPERATING_COMPANY,
            )
        )
        relationships.append(
            Relationship(
                party_id=party_id,
                role=_company_role(affiliation.role),
                related_to=_SUBJECT_PARTY_ID,
            )
        )


def _build_osint_subject_hits(profile: Profile360) -> list[WatchlistHit]:
    seed_name = profile.seed.full_name
    subject_hits = [
        WatchlistHit(
            party_id=_SUBJECT_PARTY_ID,
            party_name=seed_name,
            list_type="adverse_media",
            matched_name=seed_name,
            match_score=_FULL_MATCH_SCORE,
            source_list=_OSINT_SOURCE,
            details=f"[{finding.category}] {finding.summary}",
            tier=_adverse_hit_tier(finding.category, finding.severity),
        )
        for finding in profile.adverse_media
    ]
    if profile.pep_indicators:
        indicators_blob = " ".join(profile.pep_indicators).lower()
        tier = "high" if any(hint in indicators_blob for hint in _STRONG_PEP_HINTS) else "medium"
        subject_hits.append(
            WatchlistHit(
                party_id=_SUBJECT_PARTY_ID,
                party_name=seed_name,
                list_type="pep",
                matched_name=seed_name,
                match_score=_FULL_MATCH_SCORE,
                source_list=_OSINT_SOURCE,
                details="; ".join(profile.pep_indicators),
                tier=tier,
            )
        )
    return subject_hits


def profile_to_parties(
    profile: Profile360,
) -> tuple[list[Party], list[Relationship], list[WatchlistHit], list[SourceOfWealth]]:
    """Turn the OSINT profile into the deterministic engine's inputs.

    Creates the subject (S1), each named relationship (R*) and each affiliated company
    (C*) as parties, plus OSINT-derived watchlist hits (adverse media / PEP found on the
    web) that supplement the static list screening.
    """
    parties: list[Party] = [_build_subject_party(profile.seed)]
    relationships: list[Relationship] = [
        Relationship(party_id=_SUBJECT_PARTY_ID, role=RoleType.APPLICANT)
    ]
    osint_hits: list[WatchlistHit] = []

    _build_related_parties(profile, parties, relationships, osint_hits)
    _build_company_parties(profile, parties, relationships)
    osint_hits.extend(_build_osint_subject_hits(profile))

    sow = [
        SourceOfWealth(
            party_id=_SUBJECT_PARTY_ID,
            declared_source=profile.source_of_wealth_signal or "unknown",
            narrative=profile.wealth_summary,
            corroborating_evidence_present=profile.source_of_wealth_corroborated,
        )
    ]
    return parties, relationships, osint_hits, sow


def assess_subject(seed: SubjectSeed) -> tuple[OnboardingAssessment, Profile360, list[str]]:
    """Collect, profile, and score a single subject. Returns (assessment, profile, trace)."""
    trace: list[str] = []

    evidence = collect_evidence(seed)
    researched_angles = {item.dimension for item in evidence}
    trace.append(
        f"**Collected** {len(evidence)} public evidence item(s) across "
        f"{len(researched_angles)} research angle(s)."
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

    ownership = resolve_ubos(parties, [], relationships, applicant_party_id=_SUBJECT_PARTY_ID)
    scorecard = score_case(ownership, watchlist_hits, geo_hits, sow, [])

    hard_stop_suffix = (
        f", HARD STOP: {scorecard.hard_stop_reason}" if scorecard.hard_stop_reason else ""
    )
    trace.append(
        f"**Scorecard (deterministic)** → score={scorecard.normalized_score}/100, "
        f"band=`{scorecard.band.value}`, EDD_required={scorecard.edd_required}, "
        f"decision=`{scorecard.decision.value}`{hard_stop_suffix}"
    )

    assessment = OnboardingAssessment(
        case=CaseMetadata(
            applicant_name=seed.full_name,
            applicant_party_id=_SUBJECT_PARTY_ID,
            product=seed.relationship_purpose,
            relationship_purpose=seed.relationship_purpose,
        ),
        parties=parties,
        edges=[],
        relationships=relationships,
        source_of_wealth=sow,
        identity_checks=[],
        ownership=ownership,
        watchlist_hits=watchlist_hits,
        geography_hits=geo_hits,
        scorecard=scorecard,
        profile_360=profile,
    )
    assessment.edd_rationale = synthesize_edd_rationale(assessment)
    return assessment, profile, trace


def _answer_over_profile(
    profile: Profile360, assessment: OnboardingAssessment, question: str
) -> str:
    compact = {
        "name": profile.seed.full_name,
        "disambiguation_confidence": profile.disambiguation_confidence,
        "biography": profile.biography,
        "professional_summary": profile.professional_summary,
        "companies": [
            {
                "company": affiliation.company,
                "role": affiliation.role,
                "performance": affiliation.performance_summary,
            }
            for affiliation in profile.company_affiliations
        ],
        "adverse_media": [
            {"category": finding.category, "summary": finding.summary}
            for finding in profile.adverse_media
        ],
        "pep_indicators": profile.pep_indicators,
        "wealth_summary": profile.wealth_summary,
        "relationships": [
            {"name": finding.name, "relation": finding.relation, "is_pep": finding.is_pep}
            for finding in profile.relationships
        ],
        "behavioral": profile.behavioral.model_dump(),
        "decision": assessment.scorecard.decision.value,
        "band": assessment.scorecard.band.value,
        "data_gaps": profile.data_gaps,
    }
    system = SystemMessage(
        content=(
            "You answer questions about a public-information profile and its risk assessment. "
            "Use ONLY the supplied facts. If the answer is not present, say it was not found in "
            "public sources. Be clear that the information is from public web search and may be "
            "incomplete. Plain language, no jargon."
        )
    )
    user = HumanMessage(
        content=(
            f"Question:\n{question}\n\nProfile:\n{json.dumps(compact, indent=2, default=str)}\n\n"
            "Answer concisely."
        )
    )
    return get_llm().invoke([system, user]).content


def run_subject_360(seed: SubjectSeed, question: str = "") -> dict[str, Any]:
    """Full name-driven 360° assessment. Returns assessment, profile, answer, trace, metrics."""
    start = time.time()
    assessment, profile, trace = assess_subject(seed)
    metrics: dict[str, Any] = {
        "evidence_items": len(profile.evidence),
        "llm_calls": 2,  # profile synthesis + EDD rationale
        "band": assessment.scorecard.band.value,
        "decision": assessment.scorecard.decision.value,
    }

    answer = ""
    if question:
        answer = _answer_over_profile(profile, assessment, question)
        metrics["llm_calls"] += 1

    metrics["latency_ms"] = round((time.time() - start) * _MS_PER_SECOND, 2)
    return {
        "assessment": assessment,
        "profile": profile,
        "answer": answer,
        "reasoning": trace,
        "metrics": metrics,
    }


def _parse_subject_cli_args(args: list[str]) -> tuple[SubjectSeed, str]:
    seed_fields: dict[str, Any] = {"full_name": args[0]}
    question = ""
    index = 1
    while index < len(args):
        option = args[index]
        has_value = index + 1 < len(args)
        if option == "--location" and has_value:
            seed_fields["location"] = args[index + 1]
            index += 2
        elif option == "--nationality" and has_value:
            seed_fields["nationality"] = args[index + 1]
            index += 2
        elif option == "--ask" and has_value:
            question = args[index + 1]
            index += 2
        else:
            index += 1
    return SubjectSeed(**seed_fields), question


def _print_subject_result(result: dict[str, Any]) -> None:
    assessment = result["assessment"]
    profile = result["profile"]
    scorecard = assessment.scorecard
    print(f"\n=== {profile.seed.full_name} — public profile ===")
    print("Disambiguation confidence:", profile.disambiguation_confidence)
    print("Biography:", profile.biography[:500])
    print("\n=== RISK ===")
    print(
        f"band={scorecard.band.value}, decision={scorecard.decision.value}, "
        f"score={scorecard.normalized_score}/100"
    )
    print("triggers:", [trigger.value for trigger in scorecard.triggers])
    print("\n=== BEHAVIORAL READ ===\n", profile.behavioral.summary)
    if result["answer"]:
        print("\n=== ANSWER ===\n", result["answer"])
    print("\n=== METRICS ===\n", result["metrics"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print(
            '  python -m onboarding_risk.subject_graph "Full Name" '
            '[--location "City"] [--nationality "Country"] [--ask "question"]'
        )
        sys.exit(1)

    cli_args = sys.argv[1:]
    cli_seed, cli_question = _parse_subject_cli_args(cli_args)
    _print_subject_result(run_subject_360(cli_seed, cli_question))

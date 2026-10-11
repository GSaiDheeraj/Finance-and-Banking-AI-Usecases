"""
Profile synthesis — turn raw web evidence into a structured 360° profile.

The LLM does language work only: read the collected search snippets and write a grounded
profile (biography, professional history and companies, negative news, political
exposure, wealth, relationships, and a plain-language behavioral read). It is told to use
ONLY the supplied evidence, to cite the source links, and to be explicit about what it
could NOT find and how confident it is that the results are even the right person.

No risk score is decided here — that happens deterministically in `scoring.py` so the
rating is reproducible.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_llm
from ..schemas import (
    AdverseFinding,
    BehavioralProfile,
    CompanyAffiliation,
    EvidenceItem,
    Profile360,
    RelationshipFinding,
    SubjectSeed,
)
from .search import dbg

_EVIDENCE_SNIPPET_LENGTH = 280
_DEFAULT_EVIDENCE_LIMIT = 120


def _evidence_block(evidence: list[EvidenceItem], max_items: int = _DEFAULT_EVIDENCE_LIMIT) -> str:
    """Compact, numbered evidence list grouped by research angle (token-bounded).

    Sorted by research angle so related items sit together, and capped generously so the
    later angles (relationships, wealth) are not truncated before the model sees them.
    """
    ordered = sorted(evidence, key=lambda item: item.dimension.value)
    lines: list[str] = []
    for index, item in enumerate(ordered[:max_items]):
        snippet = (item.snippet or "")[:_EVIDENCE_SNIPPET_LENGTH]
        lines.append(
            f"[{index}] ({item.dimension.value}/{item.source}) {item.title or ''} — {snippet} <{item.url or 'no-url'}>"
        )
    if lines:
        return "\n".join(lines)
    return "(no public evidence was found)"


def _extract_json_object(text: str) -> str:
    """Extract a JSON object from LLM output with fences and prose removed.

    Differs from `extraction._strip_json` (strict JSON-only replies): profile
    synthesis returns a long object where the model sometimes adds commentary,
    so brace-matching repair is needed here.
    """
    stripped = text.strip()

    if stripped.startswith("```"):
        fenced_lines = stripped.split("\n")
        if len(fenced_lines) > 1:
            stripped = "\n".join(fenced_lines[1:])
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()

    stripped = stripped.strip()

    start_index = stripped.find("{")
    end_index = stripped.rfind("}")

    if start_index != -1 and end_index != -1 and end_index > start_index:
        stripped = stripped[start_index : end_index + 1]

    return stripped.strip()


def synthesize_profile(seed: SubjectSeed, evidence: list[EvidenceItem]) -> Profile360:
    """Build a grounded Profile360 from the collected public evidence."""
    seed_blob = {
        "full_name": seed.full_name,
        "aliases": seed.aliases,
        "approx_age": seed.approx_age,
        "location": seed.location,
        "nationality": seed.nationality,
        "education": seed.education,
        "work_history": seed.work_history,
        "known_companies": seed.known_companies,
    }

    system = SystemMessage(
        content=(
            "You are a due-diligence analyst building a public-information profile of a person "
            "for a bank's customer checks. You are given a few seed details and a numbered list "
            "of PUBLIC web search results (each line has an angle tag, a title, a snippet, and a link).\n"
            "Your job is to EXTRACT what is in the snippets, not to withhold it. The snippets are "
            "short, so read them carefully — names, companies and facts are often stated briefly.\n"
            "Rules:\n"
            "1. Use ONLY the supplied evidence. Do not invent facts. But DO extract anything that "
            "the evidence states or clearly implies — do not leave a field empty if the snippets "
            "mention it. Only add to 'data_gaps' what is genuinely absent from the evidence.\n"
            "2. PEOPLE: list every named person connected to the subject in 'relationships' — "
            "spouse, ex-spouse, partner, husband/wife, sibling/brother/sister, parent, child, "
            "co-founder, business partner, named associate. Include them even if the detail is "
            "thin (put what little is known in 'details'). Use the relation word the evidence uses "
            "(e.g. 'brother', 'ex-wife', 'co-founder'). A divorce/marriage mention still counts — "
            "list the spouse/ex-spouse.\n"
            "3. COMPANIES: list every company or fund the subject founded, co-founded, runs, "
            "directs, or owns in 'company_affiliations', with their role and any performance detail "
            "(revenue, funding, results) found in the evidence.\n"
            "3b. ADVERSE MEDIA — be disciplined. Only record an item in 'adverse_media' if the "
            "evidence says the SUBJECT THEMSELVES did something wrong (alleged or proven). Do NOT "
            "flag: articles where the subject merely comments on fraud/scams, general market or "
            "company news, opinion/criticism, or another person's wrongdoing. Set 'severity' "
            "honestly: 'high' only for alleged/proven fraud, criminal or regulatory enforcement; "
            "'medium' for active litigation or formal disputes; 'low' for reputational chatter, "
            "gossip, or unproven rumour. Pick 'category' accordingly "
            "(fraud/regulatory/litigation/reputational/other). Building a large legitimate business "
            "is NOT adverse media.\n"
            "3c. POLITICAL EXPOSURE — only put something in 'pep_indicators' if the subject (or a "
            "close family member) actually holds or held public office, a senior government/state-"
            "owned role, or a political-party leadership position. Ordinary business lobbying, "
            "charity, or meeting politicians is NOT political exposure.\n"
            "4. Judge whether the results are about the SAME person as the seed (name + location + "
            "job) and report 'disambiguation_confidence' as low/medium/high. Low confidence does "
            "NOT mean output empty — still extract what the evidence says, just flag the confidence.\n"
            "5. For your claims, cite the evidence link(s) in 'citations' and in each item's "
            "'evidence_urls'.\n"
            "6. The behavioral read must be cautious, fact-based, and clearly labelled as based on "
            "limited public information — not a clinical judgement.\n"
            "7. Plain language, no jargon.\n"
            "8. OUTPUT FORMAT: return ONLY the raw JSON object below. No markdown code fences, no "
            "explanation before or after, no trailing commentary. The first character must be '{'.\n"
            "JSON shape:\n"
            "{\n"
            '  "disambiguation_confidence": "low|medium|high",\n'
            '  "biography": str,\n'
            '  "professional_summary": str,\n'
            '  "company_affiliations": [{"company": str, "role": str|null, "status": str|null, '
            '"performance_summary": str|null, "evidence_urls": [str]}],\n'
            '  "adverse_media": [{"category": "fraud|litigation|regulatory|reputational|other", '
            '"summary": str, "severity": "low|medium|high", "url": str|null}],\n'
            '  "pep_indicators": [str],\n'
            '  "wealth_summary": str|null,\n'
            '  "source_of_wealth_signal": str|null,\n'
            '  "source_of_wealth_corroborated": bool|null,\n'
            '  "relationships": [{"name": str, "relation": str, "details": str|null, '
            '"is_pep": bool|null, "evidence_urls": [str]}],\n'
            '  "behavioral": {"summary": str, "traits": [str], "risk_indicators": [str], '
            '"public_sentiment": "positive|mixed|negative|null", "confidence": "low|medium|high"},\n'
            '  "citations": [str],\n'
            '  "data_gaps": [str]\n'
            "}"
        )
    )
    user = HumanMessage(
        content=(
            f"Seed details:\n{json.dumps(seed_blob, indent=2)}\n\n"
            f"Public evidence ({len(evidence)} items):\n{_evidence_block(evidence)}\n\n"
            "Go through EVERY numbered snippet above. Pull out: (a) every named person related to "
            "the subject (spouse/ex/partner, siblings, parents, children, co-founders, associates) "
            "into 'relationships'; (b) every company/fund into 'company_affiliations'; (c) any "
            "negative news, political links, and wealth signals. Then write the profile.\n"
            "Return ONLY the JSON object, starting with '{'."
        )
    )

    dbg("-" * 72)
    dbg(f"PROFILE synthesis: feeding {len(evidence)} evidence item(s) to the LLM")
    if not evidence:
        dbg(
            "!! No evidence to work with — the profile will be essentially empty "
            "regardless of the model. Fix collection first (see COLLECT trace above)."
        )

    raw = get_llm().invoke([system, user]).content
    parsed: dict[str, Any] = _parse_profile_json(raw)

    companies = _parse_companies(parsed)
    adverse = _parse_adverse_findings(parsed)
    relationships = _parse_relationship_findings(parsed)
    behavioral = _parse_behavioral(parsed)
    pep_indicators = _string_list(parsed, "pep_indicators")
    citations = _string_list(parsed, "citations")
    data_gaps = _string_list(parsed, "data_gaps")

    dbg(
        f"PROFILE extracted: disambiguation={parsed.get('disambiguation_confidence', 'low')}, "
        f"companies={len(companies)}, adverse={len(adverse)}, "
        f"pep_indicators={len(pep_indicators)}, relationships={len(relationships)}"
    )
    if not relationships:
        dbg(
            "   note: 0 relationships. Either the relationship searches returned nothing "
            "(check the [relationships] angle above) or the evidence had no named family/partners."
        )

    return Profile360(
        seed=seed,
        disambiguation_confidence=str(parsed.get("disambiguation_confidence", "low")),
        biography=parsed.get("biography", "") or "",
        professional_summary=parsed.get("professional_summary", "") or "",
        company_affiliations=companies,
        adverse_media=adverse,
        pep_indicators=pep_indicators,
        wealth_summary=parsed.get("wealth_summary"),
        source_of_wealth_signal=parsed.get("source_of_wealth_signal"),
        source_of_wealth_corroborated=parsed.get("source_of_wealth_corroborated"),
        relationships=relationships,
        behavioral=behavioral,
        evidence=evidence,
        citations=citations,
        data_gaps=data_gaps,
    )


def _parse_profile_json(raw_response: str) -> dict[str, Any]:
    try:
        parsed = json.loads(_extract_json_object(raw_response))
    except (json.JSONDecodeError, TypeError) as parse_error:
        dbg(
            f"!! PROFILE JSON parse FAILED ({type(parse_error).__name__}: {parse_error}). "
            "Every field will come back empty. First 300 chars of model output:"
        )
        dbg(f"   {(raw_response or '')[:300]!r}")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_list(parsed: dict[str, Any], key: str) -> list[str]:
    return [str(item) for item in (parsed.get(key) or []) if item]


def _parse_companies(parsed: dict[str, Any]) -> list[CompanyAffiliation]:
    return [
        CompanyAffiliation(
            company=str(company.get("company", "")).strip(),
            role=company.get("role"),
            status=company.get("status"),
            performance_summary=company.get("performance_summary"),
            evidence_urls=[str(url) for url in (company.get("evidence_urls") or []) if url],
        )
        for company in (parsed.get("company_affiliations") or [])
        if isinstance(company, dict) and company.get("company")
    ]


def _parse_adverse_findings(parsed: dict[str, Any]) -> list[AdverseFinding]:
    return [
        AdverseFinding(
            category=str(finding.get("category", "other")),
            summary=str(finding.get("summary", "")).strip(),
            severity=str(finding.get("severity", "medium")),
            url=finding.get("url"),
        )
        for finding in (parsed.get("adverse_media") or [])
        if isinstance(finding, dict) and finding.get("summary")
    ]


def _parse_relationship_findings(parsed: dict[str, Any]) -> list[RelationshipFinding]:
    return [
        RelationshipFinding(
            name=str(finding.get("name", "")).strip(),
            relation=str(finding.get("relation", "associate")),
            details=finding.get("details"),
            is_pep=finding.get("is_pep"),
            evidence_urls=[str(url) for url in (finding.get("evidence_urls") or []) if url],
        )
        for finding in (parsed.get("relationships") or [])
        if isinstance(finding, dict) and finding.get("name")
    ]


def _parse_behavioral(parsed: dict[str, Any]) -> BehavioralProfile:
    behavioral_blob = parsed.get("behavioral") or {}
    return BehavioralProfile(
        summary=str(behavioral_blob.get("summary", "")),
        traits=[str(trait) for trait in (behavioral_blob.get("traits") or []) if trait],
        risk_indicators=[
            str(indicator)
            for indicator in (behavioral_blob.get("risk_indicators") or [])
            if indicator
        ],
        public_sentiment=behavioral_blob.get("public_sentiment"),
        confidence=str(behavioral_blob.get("confidence", "low")),
    )

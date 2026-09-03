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
from typing import Any, Dict, List

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


def _evidence_block(evidence: List[EvidenceItem], max_items: int = 120) -> str:
    """Compact, numbered evidence list grouped by research angle (token-bounded).

    Sorted by research angle so related items sit together, and capped generously so the
    later angles (relationships, wealth) are not truncated before the model sees them.
    """
    ordered = sorted(evidence, key=lambda e: e.dimension.value)
    lines: List[str] = []
    for i, e in enumerate(ordered[:max_items]):
        snip = (e.snippet or "")[:280]
        lines.append(
            f"[{i}] ({e.dimension.value}/{e.source}) {e.title or ''} — {snip} "
            f"<{e.url or 'no-url'}>"
        )
    return "\n".join(lines) if lines else "(no public evidence was found)"


def _strip_json(text: str) -> str:
    """Extract JSON from LLM output, handling markdown code blocks and malformed JSON."""
    text = text.strip()
    
    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        # Skip first line (```json or ```)
        if len(lines) > 1:
            text = "\n".join(lines[1:])
        # Remove trailing ```
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    
    text = text.strip()
    
    # Handle case where JSON is still malformed - try to fix common issues
    # Find the first { and last }
    start_idx = text.find('{')
    end_idx = text.rfind('}')
    
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        text = text[start_idx:end_idx + 1]
    
    return text.strip()


def synthesize_profile(seed: SubjectSeed, evidence: List[EvidenceItem]) -> Profile360:
    """Build a grounded Profile360 from the collected public evidence."""
    seed_blob = {
        "full_name": seed.full_name, "aliases": seed.aliases,
        "approx_age": seed.approx_age, "location": seed.location,
        "nationality": seed.nationality, "education": seed.education,
        "work_history": seed.work_history, "known_companies": seed.known_companies,
    }

    system = SystemMessage(content=(
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
    ))
    user = HumanMessage(content=(
        f"Seed details:\n{json.dumps(seed_blob, indent=2)}\n\n"
        f"Public evidence ({len(evidence)} items):\n{_evidence_block(evidence)}\n\n"
        "Go through EVERY numbered snippet above. Pull out: (a) every named person related to "
        "the subject (spouse/ex/partner, siblings, parents, children, co-founders, associates) "
        "into 'relationships'; (b) every company/fund into 'company_affiliations'; (c) any "
        "negative news, political links, and wealth signals. Then write the profile.\n"
        "Return ONLY the JSON object, starting with '{'."
    ))

    dbg("-" * 72)
    dbg(f"PROFILE synthesis: feeding {len(evidence)} evidence item(s) to the LLM")
    if not evidence:
        dbg("!! No evidence to work with — the profile will be essentially empty "
            "regardless of the model. Fix collection first (see COLLECT trace above).")

    raw = get_llm().invoke([system, user]).content
    parsed: Dict[str, Any] = {}
    try:
        parsed = json.loads(_strip_json(raw))
    except Exception as e:
        dbg(f"!! PROFILE JSON parse FAILED ({type(e).__name__}: {e}). "
            "Every field will come back empty. First 300 chars of model output:")
        dbg(f"   {(raw or '')[:300]!r}")
        parsed = {}

    def _str_list(key: str) -> List[str]:
        return [str(x) for x in (parsed.get(key) or []) if x]

    companies = [
        CompanyAffiliation(
            company=str(c.get("company", "")).strip(),
            role=c.get("role"), status=c.get("status"),
            performance_summary=c.get("performance_summary"),
            evidence_urls=[str(u) for u in (c.get("evidence_urls") or []) if u],
        )
        for c in (parsed.get("company_affiliations") or [])
        if isinstance(c, dict) and c.get("company")
    ]
    adverse = [
        AdverseFinding(
            category=str(a.get("category", "other")),
            summary=str(a.get("summary", "")).strip(),
            severity=str(a.get("severity", "medium")),
            url=a.get("url"),
        )
        for a in (parsed.get("adverse_media") or [])
        if isinstance(a, dict) and a.get("summary")
    ]
    rels = [
        RelationshipFinding(
            name=str(r.get("name", "")).strip(),
            relation=str(r.get("relation", "associate")),
            details=r.get("details"), is_pep=r.get("is_pep"),
            evidence_urls=[str(u) for u in (r.get("evidence_urls") or []) if u],
        )
        for r in (parsed.get("relationships") or [])
        if isinstance(r, dict) and r.get("name")
    ]
    b = parsed.get("behavioral") or {}
    behavioral = BehavioralProfile(
        summary=str(b.get("summary", "")),
        traits=[str(t) for t in (b.get("traits") or []) if t],
        risk_indicators=[str(t) for t in (b.get("risk_indicators") or []) if t],
        public_sentiment=b.get("public_sentiment"),
        confidence=str(b.get("confidence", "low")),
    )

    dbg(f"PROFILE extracted: disambiguation={parsed.get('disambiguation_confidence', 'low')}, "
        f"companies={len(companies)}, adverse={len(adverse)}, "
        f"pep_indicators={len(_str_list('pep_indicators'))}, relationships={len(rels)}")
    if not rels:
        dbg("   note: 0 relationships. Either the relationship searches returned nothing "
            "(check the [relationships] angle above) or the evidence had no named family/partners.")

    return Profile360(
        seed=seed,
        disambiguation_confidence=str(parsed.get("disambiguation_confidence", "low")),
        biography=parsed.get("biography", "") or "",
        professional_summary=parsed.get("professional_summary", "") or "",
        company_affiliations=companies,
        adverse_media=adverse,
        pep_indicators=_str_list("pep_indicators"),
        wealth_summary=parsed.get("wealth_summary"),
        source_of_wealth_signal=parsed.get("source_of_wealth_signal"),
        source_of_wealth_corroborated=parsed.get("source_of_wealth_corroborated"),
        relationships=rels,
        behavioral=behavioral,
        evidence=evidence,
        citations=_str_list("citations"),
        data_gaps=_str_list("data_gaps"),
    )

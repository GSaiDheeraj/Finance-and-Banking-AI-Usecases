# Customer Onboarding Risk-Scoring Agent — HNI / UHNI & Complex Structures

## In plain English (no jargon)

When a wealthy person (or their family trust / company) wants to open an account, the bank
has to check **who they really are**, **where their money comes from**, and **whether
there is anything worrying about them** before saying yes. Today an analyst does this by
hand — reading documents and searching the web — which is slow and not always consistent.

This tool does that first pass automatically. You can start it **two ways**:

1. **Upload their paperwork** (application form, trust deed, ownership chart). The tool
   reads it, works out **who ultimately owns and controls the company/trust** (even when
   it is hidden behind several other companies), checks every person against
   "bad-actor" lists, and gives the case a **risk rating** with a clear reason for it.

2. **Just type their name** and a few details (city, job, education). The tool then
   **searches the public internet for you** — news, Wikipedia, public profiles — and pulls
   together a full picture: their companies and how those companies are doing, any bad
   press, political connections, their public behaviour, and partner/family where it's
   public. It then gives the same kind of risk rating.

The important design rule: a computer model (the "AI") only **reads and writes words** —
it never decides the score. The **score and the yes/no/escalate decision are calculated by
fixed rules**, so the **same case always gets the same answer**, and every part of the
answer can be traced back to where it came from. The internet search uses **free tools
only** — no paid subscriptions — and only looks at **information anyone can find publicly**
(it never logs into anyone's private accounts).

---


> **Business Scope.** Customer-onboarding risk scoring for **High- and Ultra-High-Net-Worth
> individuals** and the related structures they onboard through — **family offices, trusts,
> foundations, holding companies, SPVs and layered vehicles**. The agent combines
> **grounded extraction** of parties and ownership from the onboarding pack,
> **deterministic beneficial-ownership resolution**, **deterministic screening** (PEP /
> sanctions / adverse media / high-risk geography), a **deterministic risk scorecard**, and
> a grounded **Enhanced Due Diligence (EDD) rationale** — with **page-level source
> traceability** end to end.
>
> It covers the four pillars of the mandate:
> 1. **Identity verification & beneficial-ownership mapping** — who the parties are, and who
>    ultimately owns/controls the structure (effective ownership through every layer).
> 2. **EDD triggers** — PEP exposure, opaque sources of wealth, adverse media, high-risk
>    geographies, and opaque/complex structures.
> 3. **Related-party onboarding** — spouses, trustees, settlors, protectors, signatories,
>    directors and beneficial owners are onboarded as first-class parties, each screened.
> 4. **Legal-structure onboarding** — trusts, foundations, offshore companies and layered
>    entities are parsed into an ownership graph, not a flat applicant record.
>
> **The single most important reliability decision:** the LLM does only *language* work
> (read the deed/form, name the parties, read the percentages, classify the source of
> wealth, write the EDD narrative). Every *decision-bearing* computation — effective
> ownership %, UBO resolution, list matching, the risk score, the EDD/decline decision — runs
> in **deterministic Python**, so the **same case always produces the same risk rating** and
> every number is auditable back to a rule and a page.

---

## 1. Functional Requirements

### 1.1 Onboarding-pack ingestion & parsing
- **FR-1.1** Accept **one or more** text-based PDFs that make up an onboarding pack
  (application/KYC form, trust deed, foundation charter, register of members, ownership
  chart, source-of-wealth declaration, certified IDs), with case metadata: applicant name,
  product/booking entity, relationship purpose.
- **FR-1.2** Parse each PDF **page by page**, preserving 1-based page numbers and reading
  order; persist a `(page_number, text)` record per page. *No OCR* — text PDFs only.
- **FR-1.3** Build a per-pack **semantic page index** (embeddings) so any clause, register
  entry, or declaration can be located by meaning and **cited by page**.

### 1.2 Party & legal-structure extraction (grounded)
- **FR-2.1** Extract every **party** — natural persons and legal entities — as a flat record
  with a stable `party_id`: `{name, party_type, country, nationality,
  date_of_birth_or_incorporation, identifiers, declared_pep, page, source_snippet}`.
  `party_type ∈ {individual, trust, foundation, holding_company, operating_company, spv,
  partnership, fund, nominee, other}`.
- **FR-2.2** Extract **ownership edges** between parties — `{owner_id, owned_id, percentage,
  interest_type, page}` — so the legal structure becomes a graph, not a single applicant row.
  `interest_type ∈ {equity, voting, beneficial, control}`.
- **FR-2.3** Extract **related-party roles** — `{party_id, role, related_to, page}` —
  `role ∈ {applicant, settlor, trustee, protector, beneficiary, director, shareholder,
  signatory, spouse, nominee, authorized_person, partner, ubo}`.
- **FR-2.4** Extract **source-of-wealth / source-of-funds** declarations per principal party:
  `{party_id, declared_source, narrative, corroborating_evidence_present, page}`.
- **FR-2.5** Extract **identity-verification** evidence per party:
  `{party_id, document_type, document_number, verified, gaps, page}`.
- **FR-2.6** Detect **case metadata** — the applicant/primary onboarding subject, the
  booking product, and the stated relationship purpose.

### 1.3 Beneficial-ownership resolution (deterministic — core)
- **FR-3.1 Effective ownership.** For every natural person, compute **effective ownership
  %** of the applicant entity as the **sum over all ownership paths** of the **product of
  the percentages** along each path. No LLM arithmetic.
- **FR-3.2 UBO determination.** Flag a person as a **UBO** when effective ownership ≥ the
  configured threshold (default **25%**), **or** they hold a control role (settlor /
  trustee / protector / sole signatory). If no one meets the threshold, fall back to the
  **senior managing official**, and record that the basis was control, not ownership.
- **FR-3.3 Structure metrics.** Deterministically compute **layering depth** (longest
  ownership chain), **entity/layer counts**, presence of **nominee arrangements**,
  **bearer shares**, and **circular ownership** — the opacity signals that drive EDD.
- **FR-3.4 Provenance.** Each UBO records the **control path(s)** and the **pages** the
  edges were read from, so the graph is auditable.

### 1.4 Screening (deterministic)
- **FR-4.1 PEP / sanctions / adverse-media matching.** Match every party's normalized name
  against the configured **PEP**, **sanctions**, and **adverse-media** reference lists and
  return `WatchlistHit`s with a match score and the source list. Sanctions matching is a
  **hard stop**.
- **FR-4.2 Geography risk.** Resolve every party **country** (residence / incorporation /
  asset location) against the configured **high-risk-jurisdiction** list (FATF-style), each
  tiered `low | high | prohibited`.
- **FR-4.3 Reproducibility.** All matching is deterministic name/jurisdiction lookup — the
  *presence* of a risk signal never depends on a model sampling decision.

### 1.5 Risk scoring & onboarding decision (deterministic)
- **FR-5.1 Risk factor catalog.** Assemble `RiskFactorResult`s from the ownership graph,
  screening hits, source-of-wealth opacity, identity gaps and geography, each with a fixed
  **weight**, a **severity** (low/medium/high), a numeric **contribution**, **evidence**, and
  **pages**.
- **FR-5.2 Scorecard.** Sum contributions into a normalized **0–100** risk score, assign a
  **risk band** (`low | medium | high | prohibited`), and set **EDD-required**. Sanctions or
  a prohibited jurisdiction force **prohibited**.
- **FR-5.3 EDD triggers.** Surface the explicit EDD triggers fired (PEP, adverse media,
  high-risk geography, opaque source of wealth, complex structure, nominee, bearer shares).
- **FR-5.4 Decision.** Map band + triggers to an **onboarding decision** —
  `approve_standard_cdd | approve_with_edd | escalate_mlro | decline` — deterministically.

### 1.6 EDD rationale & Q&A (grounded LLM)
- **FR-6.1 EDD rationale.** Synthesize a **balanced EDD narrative** — risk summary, the
  factors that drove the band, EDD measures to apply, and information still required —
  **grounded strictly in the computed scorecard and graph**, quoting their figures. The
  deterministic triggers and decision are included verbatim; the LLM never re-rates.
- **FR-6.2 Case Q&A.** Natural-language Q&A over the assessment ("Who are the UBOs above
  25%?", "Why was EDD triggered?"), answered **only** from the computed assessment — never
  from model priors; if the case doesn't contain the answer, say so.

### 1.7 Governance & human-in-the-loop
- **FR-7.1** Log every query, retrieved context, extraction and final output for audit, with
  latency and LLM-call usage.
- **FR-7.2** Let an analyst/MLRO **review, correct and approve** extracted parties, edges and
  the final rating before the case proceeds; version the record (agent → human correction).
- **FR-7.3** Surface a full **reasoning / evidence trace** for each run.

### 1.8 Name-driven 360° research (second input path)
Instead of uploading paperwork, a user can just give a **name plus a few details**, and
the agent goes and finds the public information itself.

- **FR-8.1 Starting details.** Accept a **subject seed**: full name (required) plus any of
  aliases, approximate age / date of birth, location, nationality, education, work history,
  known companies, and the reason for the relationship. The extra details are used to make
  sure the results are about the **right person** (many people share a name) and to
  fact-check what was claimed.
- **FR-8.2 Free public search.** Search **free, public** sources only — DuckDuckGo web and
  news search, Wikipedia, and the readable text of public web pages — using free Python
  libraries with **no API keys**. **No logins, no private accounts, nothing behind a
  sign-in wall.**
- **FR-8.3 360° angles.** Gather information from eight angles: **who they are** (biography),
  **their work** (jobs, directorships), **their companies' performance**, **negative news**
  (fraud, lawsuits, investigations), **political connections**, **public behaviour**
  (posts, interviews), **family/partner and associates**, and **wealth / where the money
  came from**.
- **FR-8.4 Grounded profile.** Turn the collected results into a single readable profile.
  Every statement must come from the collected evidence and **link back to its source**;
  anything not found is listed as a **gap** rather than guessed.
- **FR-8.5 Right-person check.** Report a **confidence level** (low/medium/high) that the
  results are actually about the intended person, and warn clearly when it is low.
- **FR-8.6 Cautious behaviour read.** Provide a **plain-language, fact-based** read of how
  the person presents in public, explicitly labelled as based on limited public information
  and **not** a clinical or definitive judgement.
- **FR-8.7 Same rating engine.** Feed the findings (negative news, political exposure,
  geography, source-of-wealth gaps, related parties) into the **same deterministic
  scorecard and decision** used by the paperwork flow, so both paths produce a comparable,
  reproducible risk rating.

---

## 2. Non-Functional Requirements

- **NFR-1 Reproducibility (paramount).** Identical onboarding packs yield an **identical
  risk band and decision** — all scoring, ownership math and list matching are deterministic
  Python with fixed weights and thresholds; the LLM contributes no number to the score.
- **NFR-2 Grounding / anti-hallucination.** Extraction uses only retrieved page text; the
  EDD narrative quotes only computed values; low-confidence or conflicting cases are flagged.
- **NFR-3 Traceability.** Every party, edge, hit and risk factor links back to a **page**;
  the decision links back to the **rules** that fired.
- **NFR-4 Auditability & explainability.** The scorecard exposes each factor's weight,
  severity and contribution, and the decision logic is a transparent, inspectable mapping —
  defensible to an MLRO, internal audit and the regulator.
- **NFR-5 Security & compliance.** Encryption in transit/at rest, RBAC, per-client data
  isolation; VPC-/on-prem-first deployment; no PII leaving the customer boundary (OSS /
  gateway models only). Sensitive identity data is handled under data-minimization.
- **NFR-6 Modularity & extensibility.** Swappable models, embedders and **reference feeds**
  (replace the sample PEP/sanctions/adverse/FATF lists with World-Check / Dow Jones / live
  OFAC) without touching the pipeline; thresholds and weights are config-driven.
- **NFR-7 Usability.** Simple multi-upload + case view with an ownership table, scorecard,
  screening hits, the EDD narrative, and a trace; clear low-confidence messages.
- **NFR-8 Performance.** Handle multi-document packs; documents are indexed independently and
  extraction is structured, so larger packs scale roughly linearly.
- **NFR-9 Consistency of meaning.** A `party_id` means the same party across edges, roles,
  SoW and screening; ownership percentages, thresholds and band cut-offs are fixed and shared.

---

## 3. Business Requirements

- **BR-1** Replace manual KYC/EDD **structure unpacking**: turn an onboarding pack into a
  resolved ownership graph, screened party list and a defensible risk rating in minutes.
- **BR-2** Improve **consistency** of onboarding decisions across analysts and booking
  centres — the same structure scores the same way every time.
- **BR-3** Produce **auditable trails** for how each risk rating was derived (rule + page),
  ready for MLRO sign-off and regulatory review.
- **BR-4** **Surface EDD cases automatically** — PEP, opaque wealth, adverse media,
  high-risk geography and complex/layered structures — so analysts focus effort where risk is.
- **BR-5** Produce a **first-cut EDD rationale** for analyst review, accelerating high-value
  HNI/UHNI onboarding without sacrificing grounding or traceability.

---

## 4. Impact & End Users

**Primary users**
- **Onboarding / KYC analysts** — party extraction, ownership resolution, first-cut rating.
- **EDD / financial-crime analysts** — PEP/adverse-media context, source-of-wealth gaps.
- **Relationship managers (Private Bank / Wealth)** — faster, more predictable onboarding.

**Secondary users**
- **MLRO & compliance** — review, approve, escalate, and decline with a documented rationale.
- **Internal audit & regulators** — challenge how each rating and UBO determination was made.

**Impact**
- An onboarding pack becomes a resolved ownership graph + screened party list + rated case in
  minutes instead of days.
- Consistent, reproducible risk ratings reduce analyst-to-analyst variability and audit findings.
- Automatic EDD-trigger surfacing focuses scarce financial-crime resource on genuine risk.
- A defensible audit trail from each decision back to the rule and the source page.

---

## 5. Evaluation Metrics

### 5.1 Business metrics
- **Time-to-decision** — pack → rated case (pre/post).
- **EDD precision** — % of EDD-triggered cases an analyst confirms as genuinely warranting EDD.
- **Decision consistency** — agreement / re-run stability of band & decision (must be 100%).
- **Analyst productivity** — HNI/UHNI cases onboarded per analyst per week (pre/post).
- **Audit-finding rate** — KYC/EDD findings per 100 files (pre/post).
- **EDD-rationale acceptance** — % of first-cut rationales accepted with minor edits.

### 5.2 Technical metrics
- **Party extraction accuracy** — precision/recall/F1 vs. curated ground truth.
- **Ownership-edge accuracy** — correct owner/owned/percentage triples.
- **UBO resolution accuracy** — correct effective % and UBO set vs. analyst-verified.
- **Screening recall / false-positive rate** — list-match quality vs. labeled hits.
- **Scorecard reproducibility** — identical band & decision on re-run (must be 100%).
- **Grounding / hallucination rate** — EDD narrative claims contradicting or inventing facts.
- **Coverage** — % of (party × required-field) cells populated.
- **System health** — uptime, error rate, latency, resource utilization.

---

## 6. Architecture (this build)

```
Onboarding pack
  PDF #1 (KYC form) ─┐
  PDF #2 (trust deed)┼─► doc_index (pages + embeddings)
  PDF #N (ownership) ┘        │
                             ▼
        GROUNDED EXTRACTION (LLM — language only)
          ├─ extract_structure   → Party[] + OwnershipEdge[] + Relationship[]
          ├─ extract_source_of_wealth → SourceOfWealth[]
          ├─ extract_identity    → IdentityCheck[]
          └─ detect_case_metadata→ applicant / product / purpose
                             │
                             ▼
        DETERMINISTIC ENGINE (Python — every decision-bearing number)
          ├─ ownership.resolve_ubos     → effective %, UBOs, layering, nominee/circular
          ├─ screening.screen_parties   → PEP / sanctions / adverse / geography hits
          ├─ scoring.score_case         → RiskScorecard (factors, 0–100, band, triggers)
          └─ scoring.decide             → onboarding decision (CDD / EDD / MLRO / decline)
                             │
                             ▼
        synthesize_edd_rationale (LLM, grounded)   +   case Q&A (LLM, grounded)
```

**Design choice — deterministic + probabilistic hybrid.** The LLM reads language; all
ownership math, list matching, scoring and the decision are plain Python over the extracted
records, so the risk rating is exact, reproducible and auditable.

### Second input path — name-driven 360° (free OSINT)

```
SubjectSeed (name + a few details)
      -> osint.collect   → free web/news/Wikipedia search per angle → EvidenceItem[]
      -> osint.profile   → Profile360 (LLM, grounded, cited)        (language only)
      -> profile_to_parties  → subject + spouse + companies          (deterministic)
            + OSINT-derived adverse-media / PEP signals
      -> screen_parties / resolve_ubos / score_case / decide         (deterministic)
      -> synthesize_edd_rationale (LLM, grounded) + case Q&A
```

Both input paths converge on the **same** deterministic scoring/EDD engine, so a paperwork
case and a name-only case produce comparable, reproducible ratings.

> **A note on reproducibility for the name flow.** The *scoring* is deterministic given a
> fixed set of findings. The *search* step reads the live internet, so re-running on a
> different day can surface different evidence — that is simply how open research works. The
> evidence trace records exactly what was used each time.

### File layout
```
Fraud_Detection_Agent/
├── REQUIREMENTS.md                 ← this document
├── requirements.txt
├── .env.example
├── onboarding_risk/
│   ├── __init__.py
│   ├── config.py                   ← LLM + embedder clients (gateway-agnostic)
│   ├── reference.py                ← sample PEP / sanctions / adverse / FATF lists + thresholds
│   ├── schemas.py                  ← parties, ownership, scorecard + OSINT profile records
│   ├── doc_index.py                ← PDF → pages + semantic index
│   ├── tools.py                    ← agent tools (search_pages / get_page)
│   ├── extraction.py               ← grounded structure / SoW / identity extraction (PDF flow)
│   ├── ownership.py                ← deterministic UBO graph engine (effective %, layering)
│   ├── screening.py                ← deterministic PEP / sanctions / adverse / geography matching
│   ├── scoring.py                  ← deterministic risk scorecard + onboarding decision
│   ├── edd.py                      ← grounded EDD-rationale synthesis (shared)
│   ├── agent_graph.py              ← run_onboarding_assessment (PDF-pack orchestration)
│   ├── subject_graph.py            ← run_subject_360 (name-driven OSINT orchestration)
│   └── osint/
│       ├── __init__.py
│       ├── search.py               ← FREE search wrappers (ddgs / wikipedia / requests+bs4)
│       ├── collect.py              ← per-angle query building + evidence collection
│       └── profile.py              ← grounded Profile360 synthesis + behavioural read
└── app_streamlit/
    ├── ui.py                       ← paperwork flow: ownership, scorecard, screening, EDD
    └── ui_subject.py               ← name flow: 360° profile + behaviour + risk read
```

### Run
```bash
pip install -r requirements.txt
cp .env.example .env          # fill in gateway endpoint + keys; point reference feeds at live lists

# Paperwork flow (upload an onboarding pack):
streamlit run app_streamlit/ui.py
python -m onboarding_risk.agent_graph kyc_form.pdf trust_deed.pdf ownership_chart.pdf -- "Who are the UBOs above 25%?"

# Name-driven 360° flow (free public web search):
streamlit run app_streamlit/ui_subject.py
python -m onboarding_risk.subject_graph "Jane A. Smith" --location "London, UK" --nationality "British" --ask "Summarize the main risks found."
```

> **Free search stack.** The name flow uses `googlesearch-python` (free Google scrape,
> region-aware — primary), `ddgs` (DuckDuckGo — fallback + news), `wikipedia`, and
> `requests` + `beautifulsoup4` — all free, no API keys. Web search aggregates Google then
> tops up with DuckDuckGo, de-duplicated, so well-known names resolve reliably. Set the
> region per case (UI field) or globally via `OSINT_COUNTRY` (e.g. `in`, `us`). If a library
> is missing or the network is down, search returns nothing and the rest of the pipeline
> still runs. Only publicly available information is collected; nothing behind a login.

> **Reference data note.** `reference.py` ships **illustrative sample** PEP / sanctions /
> adverse-media / high-risk-jurisdiction lists so the system runs end-to-end out of the box.
> In production, point these at your licensed feeds (World-Check, Dow Jones, live OFAC/UN/EU
> sanctions, current FATF grey/black lists). The matching logic does not change.

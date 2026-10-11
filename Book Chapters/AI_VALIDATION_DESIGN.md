# AI System Design — Business Understanding + LLM-Output Validation

This repo contains **four BFSI agentic-AI systems**. Each follows the same
design principle:

> **LLM does language work only. Python decides numbers, risk, and persistence.**

| Agent | Business problem | LLM tasks (language) | Deterministic tasks (Python) |
|---|---|---|---|
| `Credit_Risk_Assesment_Agent` | Corporate/SME lending: spread financials, rate credit, price facility | Extract metadata/line-items/notes, classify sentiment, draft memo | Ratios, benchmarks, trends, scorecard (AAA–D, PD/LGD/EL), decision + pricing + covenants |
| `Fraud_Detection_Agent` (onboarding_risk) | HNI/UHNI KYC onboarding: who owns what, PEP/sanctions/adverse-media, EDD | Extract parties/edges/roles, SoW, identity gaps, profile-360, EDD narrative | UBO resolution, ownership opacity, list+geo screening, fixed-weight scorecard (LOW/MEDIUM/HIGH/PROHIBITED → CDD/EDD/MLRO/DECLINE) |
| `FinDoc_IIntelligence_Agent` | Fundamentals extraction from bank/issuer PDFs (10-K/annual/quarterly) | Classify tables (BS/IS/CF/equity/other, consolidated vs standalone), extract line-item facts | Sign/period/duplication checks, flattening, dedup persistence, bounded validate→correct→re-extract loop |
| `Investment_Research_Copilot` | Portfolio monitoring + construction for PMs/advisors | Transcribe holdings PDF, classify news/geo/sector, draft briefing/narrative | Allocation/drift/rebalance/cashflow/risk/limits/alerts/score, committee scores (VLRT/VCST/SQL/OPCO-FINCO/BMV), guardrails |

## Validation philosophy (all four agents)

Schema validation (Pydantic / `with_structured_output`) is **necessary but not
sufficient**. A well-formed JSON can still be *wrong*: wrong label, invented
party, ungrounded figure, hallucinated ticker, overstated severity.

Hence every agent now ships an `llm_validation.py` module implementing:

1. **Correctness rules** (deterministic, no LLM trust):
   - *Classification* → `score_classification()`: enum membership, evidence
     grounding (keyword overlap), consistency (e.g. severity matches category).
     Reports **estimated % correct** = weighted pass-rate over per-item checks.
   - *Extraction* → `score_extraction()`: required-field completeness,
     value-in-evidence grounding, numeric plausibility (ranges, signs),
     referential integrity (ids resolve, no self-edges, percentages 0–100).
   - *Suggestion* → `score_suggestion()`: whitelist grounding (tickers/parties
     from supplied universe), no invented figures, risk acknowledgement,
     actionability.
2. **Confidence score** `0.0–1.0` = `1 − Σ penalties` (clamped). Each failed
   rule contributes a named penalty so the score is explainable.
3. **Threshold gate** `CONFIDENCE_THRESHOLD = 0.50`:
   - `confidence >= 0.50` → accept (persist, optionally with warnings).
   - `confidence < 0.50` → collect `failures: [rule + detail]`, render them via
     `build_retry_addendum()` and **reinvoke the LLM with the failures appended
     to the prompt** (bounded: `max_attempts = 3`).
4. **Generic retry driver** `validate_with_retry(llm_fn, score_fn)` so every
   call-site shares the same loop instead of ad-hoc `try/except → []`.

```
LLM output → schema parse → correctness rules → confidence
   ├── >= 0.50 → accept (+ warnings)
   └── < 0.50 → failures → addendum → reinvoke (≤3) → accept-best or fail-soft
```

## Database hygiene (this change)

No table was literally zero-column, but several tables/columns were
**defined yet never written or never read**. Policy applied: *use it if it
makes sense, else drop it; use sensible names.*

- **Fraud (`onboarding_risk`)** — `CaseDocument`, `PageChunk`, `Party`,
  `OwnershipEdge` existed but `api/main.py:save_assessment_to_db` only wrote
  `Case/UBO/WatchlistHit/EDDMeasure/Review`. Fixed by **using** them: new
  `onboarding_risk/db/persist_graph.py::persist_case_graph()` writes
  documents → parties → edges → page-chunks; EDD `category/severity` now
  populated via keyword classifier instead of left NULL.
- **FinDoc (`fin_doc_intel`)** — `LineItemRecord.unit` was hardcoded `None`
  in `extraction.py:128`; `Project.model_name/embed_model_name/reasoning`
  never written; `Document.s3_uri` written-never-read; issuer `currency`
  detected then dropped. Fixed by **using**: unit derived from
  scale/currency, issuer currency persisted, project model/reasoning fields
  populated in `persistence.run_and_persist`; `s3_uri` kept (sensible — real
  S3 mirror) but now has a documented reader fallback.
- **Investment (`portfolio_monitor`)** — `holdings` dropped
  `price/cost_basis/price_source`; `briefings` collapsed three commentary
  fields into `key_findings`; `reviews.corrected_fields` never consumed.
  Fixed by **using**: columns added (`price`, `cost_basis`, `price_source`),
  briefing row expanded (`risk_commentary`, `cashflow_commentary`,
  `watch_items`), review corrections now applied to `raw_result` audit log.
- **Credit (`credit_risk`)** — `ReviewRow` was write-only. Fixed by **using**:
  new `db/review_feedback.py::apply_review_corrections()` reads corrections
  back for audit display; no table dropped (all carry queryable fields).

## Per-agent wiring

- Credit: `credit_risk/llm_validation.py` + retry wrappers in
  `extraction.py` (`extract_company_metadata`, `extract_line_items`) and
  `osint/sentiment.py` (`synthesize_sentiment`).
- Fraud: `onboarding_risk/llm_validation.py` + retry in `extraction.py`
  (`extract_structure`, `extract_source_of_wealth`, `extract_identity`) and
  `osint/profile.py` (`synthesize_profile`).
- FinDoc: `fundamentals_pipeline/llm_validation.py` (new accounting-identity,
  currency/scale, confidence-gate checks) wired into `validators.run_all`
  chain + `extraction.py` retry loop via `build_correction_addendum`.
- Investment: `portfolio_monitor/llm_validation.py` + retry in
  `statement_extract.py` (`extract_holdings_from_statement`) and
  `osint/scan.py` (`_synthesise_llm`) and `construct/llm_advisor.py` proposal
  check.

## How to verify

Each agent has `tests/test_llm_validation.py` (pure-Python, no network):
classification %correct, extraction grounding, suggestion whitelist, retry
addendum content, and the `< 0.50 → reinvoke` loop with a stub LLM.
Run with `python -m pytest tests/test_llm_validation.py -q` inside each agent
dir (or the repo-root runner if present).

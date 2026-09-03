# Financial Document Intelligence Agent — Investment Research & Portfolio Monitoring

> **Business Scope.** Financial statement and disclosure analysis for banks, asset
> managers, and investment firms across **corporates, banks, NBFCs, funds, and SPVs**.
> The agent combines **automated extraction**, **normalization into a structured
> fundamentals schema**, **ratio/trend analysis**, and **page-level source
> traceability**.
>
> **This build targets one slice of that scope end-to-end:**
> **Investment research & portfolio monitoring** — structured **issuer-level
> fundamentals time series** built by merging multiple filings, with **growth /
> CAGR / YoY**, **equity & fixed-income ratios**, a balanced **research note**
> (bull/bear + "what changed"), and cross-issuer **screening** for idea generation
> and surveillance watchlists.
>
> The LLM does only *language* work (locate statements, read line items, classify
> risk, write the note). All *arithmetic* — ratios, YoY, CAGR, "what changed",
> screening — runs in deterministic Python, so a number means the same thing on every
> run. This is the single most important reliability decision in the system.

---

## 1. Functional Requirements

### 1.1 Document ingestion & parsing
- **FR-1.1** Accept **one or more** text-based PDFs (annual / interim reports, filings,
  rating reports) with metadata: issuer name, reporting period, report type, currency.
- **FR-1.2** Parse each PDF **page by page**, preserving page numbers and reading order.
  Persist a `(page_number, text)` record per page. *No OCR* — text PDFs only.
- **FR-1.3** Build a per-document **semantic page index** (embeddings) so any statement,
  note, or disclosure can be located by meaning, not just keyword.

### 1.2 Statement location & structured extraction
- **FR-2.1** Locate the four primary statements: **Balance Sheet**, **Income
  Statement (P&L)**, **Cash Flow Statement**, **Statement of Changes in Equity**.
- **FR-2.2** Extract every line item as a **flat structured record**:
  `{statement, label, section_path, value, unit, currency, period, consolidated, page, source_snippet}`.
  `section_path` captures enclosing headers/sub-totals **without** a parent-child math tree.
- **FR-2.3** Map each line item to a **canonical key** from a controlled vocabulary
  (`revenue`, `ebit`, `net_income`, `total_debt`, `total_equity`, `cfo`, `capex`, …)
  so heterogeneous issuer labels normalize into one fundamentals schema.
- **FR-2.4** Support **per-share / equity-research metrics**: `eps`,
  `dividends_per_share`, `shares_outstanding`, plus a derived **Free Cash Flow**
  (`CFO − Capex`).
- **FR-2.5** Capture **risk disclosures** (debt maturity, covenants, contingent
  liabilities, related-party, going-concern, credit quality/NPLs, concentration) to
  contextualize the fundamentals trend.

### 1.3 Normalization
- **FR-3.1** Assemble a per-document **normalized fundamentals snapshot** keyed by
  canonical line item and period (the structured schema downstream code reads from).
- **FR-3.2** Support **multi-period** values when a statement presents comparatives,
  each tagged with its own period.

### 1.4 Issuer-level fundamentals time series (core)
- **FR-4.1 Multi-document merge.** **Merge** per-document snapshots into one
  issuer series, **unioning periods** across documents and ordering them
  **chronologically**. When two documents disclose the same period, prefer the
  later/restated figure.
- **FR-4.2 Growth analytics (deterministic).** For every fundamental and ratio,
  compute **YoY %** and multi-period **CAGR %** in code. Undefined cases (sign flips,
  non-positive bases) return null rather than a misleading number.
- **FR-4.3 Ratio trends.** Re-compute the full deterministic ratio set **per period**
  (leverage, coverage, liquidity, profitability) and expose each as a time series.
- **FR-4.4 "What changed".** Deterministically surface the **largest YoY moves**
  (latest vs prior period) across fundamentals and ratios to drive surveillance.
- **FR-4.5 Provenance across documents.** Each period records its **source document**
  and the **page** behind each canonical value, so traceability survives the merge.

### 1.5 Research note & screening
- **FR-5.1 Research note.** Synthesize a **balanced research note** — thesis, **bull
  case**, **bear case**, key trends, watch items — grounded strictly in the computed
  series and quoting its figures; the deterministic "what changed" is included verbatim.
- **FR-5.2 Cross-issuer screening.** Evaluate a universe of issuers against
  **user-defined criteria** (any canonical fundamental or ratio, with `> >= < <= ==`
  operators and weights) at the latest period; **rank** by weighted score / pass count
  for idea generation and watchlist construction.

### 1.6 Question answering
- **FR-6.1** Natural-language Q&A over the issuer series ("How has EPS compounded over
  3 years?", "Is leverage trending up?"), answered **only** from the computed series —
  never from model priors.
- **FR-6.2** Reference the relevant **periods**; if the series doesn't contain the
  answer, say so rather than guess.

### 1.7 Governance & human-in-the-loop
- **FR-7.1** Log every query, retrieved context, extraction, and final output for
  audit/debug, with latency and token/LLM-call usage.
- **FR-7.2** Let an analyst **review, correct, and approve** extracted facts before they
  flow downstream; version the record (agent extraction → human correction).
- **FR-7.3** Surface a full **reasoning / evidence trace** for each run.

---

## 2. Non-Functional Requirements

- **NFR-1 Accuracy.** Target ≥95% raw precision on key numeric facts; ≥98% after
  human-in-the-loop correction. Growth/ratios are computed deterministically to remove
  LLM arithmetic error.
- **NFR-2 Grounding / anti-hallucination.** Answers and notes quote only computed
  figures / retrieved content; low-confidence or conflicting cases are flagged.
- **NFR-3 Traceability.** Every value links back to a source document and page; the
  per-period provenance survives the cross-document merge.
- **NFR-4 Performance.** Handle long documents (500–1,000 pages); multi-document runs
  extract documents independently (embarrassingly parallel), so adding issuers/years
  scales roughly linearly.
- **NFR-5 Security & compliance.** Encryption in transit/at rest, RBAC, per-client data
  isolation; on-prem / VPC-first deployment; no PII leaving the customer boundary
  (OSS / gateway models only).
- **NFR-6 Modularity & extensibility.** Swappable models and tools (better table
  extractor, new LLM) without rewriting the pipeline; config-driven templates per
  document type (10-K, annual report, rating report).
- **NFR-7 Usability.** Simple multi-upload + chat UX with shortcuts (`/fundamentals`,
  `/ratios`, `/trends`, `/screen`) and clear low-confidence error messages.
- **NFR-8 Cross-document consistency.** The same canonical key means the same thing
  across documents/periods; period labels are normalized to a common chronological
  ordering. Restatements resolve deterministically (later document wins), traceably.
- **NFR-9 Reproducibility.** Growth/CAGR semantics are fixed (null on undefined cases)
  and screening ranking is deterministic — identical inputs yield identical output.

---

## 3. Business Requirements

- **BR-1** Replace manual spreadsheet **history-building**: turn a folder of filings
  into a clean, comparable **multi-year fundamentals dataset** in minutes.
- **BR-2** Improve **coverage and consistency** of extracted fundamentals versus manual
  work, reducing analyst-to-analyst variability.
- **BR-3** Produce **auditable trails** for how every number was derived (document + page).
- **BR-4** Enable **systematic screening and surveillance** — rank a coverage universe by
  fundamental/ratio criteria and flag material YoY changes automatically.
- **BR-5** Produce **first-cut research notes** (bull/bear, "what changed") for analyst
  review, accelerating idea generation without sacrificing grounding/traceability.

---

## 4. Impact & End Users

**Primary users**
- **Equity research analysts** — multi-year fundamentals history, growth/CAGR, bull/bear notes.
- **Fixed-income / credit research analysts** — issuer leverage & coverage trends, surveillance.
- **Buy-side PMs & screening / quant teams** — rank a universe by fundamental/ratio criteria.

**Secondary users**
- **Model risk management & internal audit** — challenge how figures were derived.
- **Compliance & regulators** — documentation, oversight, traceability.

**Impact**
- A folder of filings becomes a comparable multi-year dataset in minutes.
- Systematic screening and automated "what changed" surveillance across a coverage list.
- Lower rework and fewer data-quality findings via deterministic math + provenance.
- Defensible audit trail from each metric back to the source disclosure.

---

## 5. Evaluation Metrics

### 5.1 Business metrics
- **History build time** — time to turn N filings into a comparable multi-year series.
- **Universe coverage** — # issuers / periods under automated monitoring per analyst.
- **Analyst productivity** — issuers analyzed per analyst per week (pre/post).
- **Screen precision** — % of screened-in names an analyst confirms as genuinely qualifying.
- **Surveillance lead time** — how early "what changed" flags a material move vs. manual review.
- **Note acceptance rate** — % of first-cut research notes accepted with minor edits.

### 5.2 Technical metrics
- **Extraction accuracy** — cell-level precision / recall / F1 vs. curated ground truth.
- **Period alignment accuracy** — % of periods correctly normalized & ordered across docs.
- **Cross-document merge correctness** — restatement/duplicate-period resolution accuracy.
- **Growth/CAGR correctness** — computed growth vs. analyst-verified (exact, deterministic).
- **Time-series completeness** — % of (metric × period) cells populated across the series.
- **QA quality** — answer correctness (human-label or LLM-as-judge), incl. numeric reasoning.
- **Hallucination rate** — answers/notes contradicting or inventing facts.
- **Screen reproducibility** — identical ranking on re-run (must be 100%, deterministic).
- **System health** — uptime, error rate, resource utilization.

---

## 6. Architecture (this build)

```
PDF #1 ─┐
PDF #2 ─┼─► per-document extraction (grounded; fan-out)
PDF #N ─┘        │  pdf_index (pages + embeddings)
                 │  extract_financials → flat LineItems + canonical keys
                 │  extract_risk       → RiskDisclosures
                 │  normalize          → FundamentalsSnapshot (per doc)
                 ▼
        merge_snapshots ─► IssuerTimeSeries   (union periods, chronological)
                 │
                 ▼
        DETERMINISTIC engine
          ├─ ratios per period        (ratios.py)
          ├─ YoY / CAGR growth        (ratios.py)
          ├─ what_changed             (timeseries.py)
          └─ screen_universe          (screening.py)
                 │
                 ▼
        synthesize_research_note (LLM, grounded)   +   time-series Q&A (LLM, grounded)
```

**Design choice — deterministic + probabilistic hybrid.** The LLM reads language; all
arithmetic (ratios, YoY, CAGR, "what changed", screening) is plain Python over the
normalized series, so numbers are exact and reproducible.

### File layout
```
FinDoc_IIntelligence_Agent/
├── REQUIREMENTS.md                 ← this document
├── requirements.txt
├── .env.example
├── fin_doc_intel/
│   ├── __init__.py
│   ├── config.py                   ← LLM + embedder clients
│   ├── schemas.py                  ← fundamentals / ratio / time-series / screen schema
│   ├── pdf_index.py                ← PDF → pages + semantic index
│   ├── tools.py                    ← agent tools (search / get_page)
│   ├── extraction.py               ← grounded structured extraction (BS/PL/CF/Eq + risk)
│   ├── ratios.py                   ← deterministic ratio engine + FCF / growth / CAGR
│   ├── timeseries.py               ← merge multi-doc snapshots → issuer time series + trends
│   ├── screening.py                ← cross-issuer screening / ranking engine
│   ├── research.py                 ← grounded research-note + "what changed" synthesis
│   └── agent_graph.py              ← run_research_analysis (multi-document orchestration)
└── app_streamlit/
    └── ui.py                       ← upload, time series + charts, research note, screening
```

### Run
```bash
pip install -r requirements.txt
cp .env.example .env          # fill in gateway endpoint + keys
streamlit run app_streamlit/ui.py

# CLI (one issuer across years, or a universe to screen):
python -m fin_doc_intel.agent_graph FY2022.pdf FY2023.pdf FY2024.pdf -- "3-yr trend?"
```

# Credit Risk Assessment Agent — SMEs through Large Corporates

## In plain English (no jargon)

When a company (from a small business to a large corporation) applies for a loan, a credit
line, or any debt facility, the bank has to understand **how likely they are to repay** and
**how much the bank stands to lose if they don't**. Today a credit analyst does this by hand
— reading three to five years of audited financials, computing dozens of ratios, comparing
against sector peers, and writing a credit memo — which can take days.

This tool does that first pass automatically. You upload the company's **financial
statements** (balance sheet, profit-and-loss account, cash flow statement), and the tool:

1. **Reads and parses** every financial line item across all periods — revenue, EBITDA,
   debt, equity, cash position, capex — with the page each figure came from.
2. **Computes all standard credit ratios** (leverage, liquidity, profitability, debt-service
   coverage, cash-flow quality, efficiency) in deterministic Python — the same input always
   yields the same ratio.
3. **Benchmarks the company** against its sector: above or below median on each ratio, and
   flags any material underperformance.
4. **Analyses multi-period trends** — is the business improving, stable, or deteriorating?
   Are margin or leverage trends pointing the wrong way?
5. **Scores the case** using a fixed-weight factor scorecard and assigns an **internal
   credit rating** (AAA through D), a **probability of default** estimate, and a **lending
   decision** (approve / approve-with-conditions / refer to credit committee / decline).
6. **Writes a first-cut credit memo** — executive summary, financial analysis, risk factors
   and mitigants, covenants, and monitoring triggers — grounded strictly in the computed
   scorecard, never in model priors.

The important design rule: the LLM does only **language** work (read the filing, name the
line items, write the memo). **Every ratio, score, rating and lending decision is computed
by fixed deterministic Python rules**, so the **same financials always produce the same
rating** and every number traces back to a rule and a page.

---

> **Business Scope.** Automated internal credit-rating generation for **SMEs through large
> corporates**, covering balance-sheet lending, revolving credit facilities, term loans,
> capex financing and bond/MTN issuances. The agent processes **audited financial
> statements** (balance sheet, P&L, cash-flow statement, notes) to produce:
>
> - **Grounded financial statement extraction** with page-level traceability across up to
>   five fiscal years.
> - **Deterministic ratio computation** — leverage, liquidity, profitability, coverage,
>   efficiency, and cash-flow quality — with no LLM arithmetic.
> - **Deterministic sector benchmarking** against configurable industry medians.
> - **Deterministic multi-period trend detection** — direction, magnitude, and trajectory.
> - **Deterministic credit scorecard** — fixed-weight factors mapped to an internal rating
>   grade (AAA–D), a risk band, a probability of default (PD), and a loss given default
>   (LGD).
> - **Deterministic lending decision** (approve / approve-with-conditions / refer to credit
>   committee / decline) with a suggested pricing band.
> - **Grounded credit memo** (LLM, grounded in the scorecard) — never re-rates.
>
> The four pillars of the mandate:
> 1. **Financial extraction & normalisation** — read the filed accounts, map each line item
>    to a standardised label, preserve multi-period data for trend work.
> 2. **Ratio engine** — compute every credit-relevant ratio in deterministic Python from the
>    extracted line items; flag missing inputs so the analyst knows what data was absent.
> 3. **Benchmarking & trend** — compare each ratio to the sector median and quartiles;
>    detect and classify trend direction across periods.
> 4. **Scorecard & memo** — assign a rating and a decision with a fully auditable factor
>    breakdown and a first-cut credit memo for analyst sign-off.
>
> **The single most important reliability decision:** the LLM does only *language* work
> (read the accounts, name the line items, write the memo narrative). Every
> *decision-bearing* computation — ratios, trend direction, sector percentile, the credit
> score, the rating grade, the PD/LGD estimates, and the lending decision — runs in
> **deterministic Python**, so the **same financials always produce the same rating** and
> every number is auditable back to a rule and a page.

---

## 1. Functional Requirements

### 1.1 Financial-document ingestion & parsing
- **FR-1.1** Accept **one or more** text-based PDFs that make up a credit submission:
  audited annual accounts, management accounts, interim statements, or an information
  memorandum, with case metadata: company name, requested facility type, amount, purpose,
  and relationship manager.
- **FR-1.2** Parse each PDF **page by page**, preserving 1-based page numbers and reading
  order; persist a `(page_number, text)` record per page. *No OCR* — text PDFs only.
- **FR-1.3** Build a per-pack **semantic page index** (embeddings) so any financial table,
  ratio, management discussion or note can be located by meaning and **cited by page**.

### 1.2 Financial statement extraction (grounded)
- **FR-2.1** Extract every **financial line item** across all presented periods as a flat
  record: `{label, standardised_label, value, currency, unit, period, period_type,
  statement_type, statement_basis, page, source_snippet}`.
  `statement_type ∈ {balance_sheet, income_statement, cash_flow_statement, notes}`.
  `standardised_label` maps to a fixed vocabulary so ratio computation works across
  different filing formats.
- **FR-2.2** Extract **company metadata**: name, legal entity, industry / sub-industry,
  country, registration number, years in operation, employee count, auditor name, audit
  opinion (`clean | qualified | adverse | disclaimer`), and management commentary.
- **FR-2.3** Extract **off-balance-sheet items** and **contingent liabilities** declared
  in the notes: operating lease commitments, guarantees, pending litigation, related-party
  exposures.
- **FR-2.4** Detect the **credit facility request**: facility type, requested amount and
  currency, stated purpose.
- **FR-2.5** Support **multi-period extraction**: extract up to five fiscal years (or
  quarters) from a single pack; record which period each line item belongs to.
- **FR-2.6 Market-data fallback (yfinance).** When a **ticker** is supplied and a required
  line item or whole period is **not present in the documents**, fetch the company's public
  financial statements from Yahoo Finance (free `yfinance` library) and fill only the
  missing cells. The documents are the **primary, audited source and always take
  precedence**; yfinance only fills gaps. The mapping from a Yahoo row label to the
  standardised vocabulary is a **fixed dictionary** (deterministic — no LLM, no
  arithmetic), and each fallback figure is tagged with provenance
  `source = "yfinance:<TICKER>"` so the audit trail distinguishes filing-sourced from
  market-data-sourced numbers. With a ticker, a case can run with **no documents at all**
  (assessed on yfinance fundamentals). If `yfinance` is unavailable or the lookup fails,
  the step is skipped and the assessment proceeds on the documents alone.

### 1.3 Ratio computation (deterministic — core)
- **FR-3.1 Leverage.** Compute from extracted line items: Debt/Equity, Debt/Assets,
  Net Debt/EBITDA, Total Liabilities/Equity. Log missing inputs as `None` so the
  analyst knows what data was absent.
- **FR-3.2 Liquidity.** Current Ratio, Quick Ratio (excl. inventory), Cash Ratio.
- **FR-3.3 Profitability.** EBITDA Margin, EBIT Margin, Net Margin, ROE, ROA, ROCE.
  Derive EBITDA = EBIT + D&A when neither is directly stated.
- **FR-3.4 Debt-service coverage.** Interest Coverage (EBITDA / Interest), DSCR
  ((EBITDA - Capex) / (Principal + Interest)), OCF-to-Debt.
- **FR-3.5 Cash-flow quality.** Cash Conversion (OCF / Net Income), FCF Margin.
- **FR-3.6 Efficiency.** Asset Turnover, Inventory Days, Days Sales Outstanding.
- **FR-3.7 Provenance.** Each ratio links to the line-item labels used in the calculation
  and the pages they were read from.

### 1.4 Sector benchmarking (deterministic)
- **FR-4.1** Compare each computed ratio against the configured **sector median**,
  25th-percentile (weak) and 75th-percentile (strong) for the company's industry.
- **FR-4.2** Classify each ratio as `above_75th | above_median | median | below_median |
  below_25th | not_available`.
- **FR-4.3** Flag ratios that fall in the bottom quartile of the sector as material gaps.
- **FR-4.4** Aggregate into an **overall sector position**: `strong | average | weak`.
- **FR-4.5** Benchmarks are config-driven; the matching logic does not change when the
  data is updated.

### 1.5 Trend analysis (deterministic)
- **FR-5.1** For each key ratio, compute the multi-period **direction** (`improving |
  stable | deteriorating | volatile`) and **magnitude** (`minor | moderate | significant`)
  from the time series of computed values — no LLM judgement.
- **FR-5.2** Classify overall **trajectory** across revenue, EBITDA, leverage, liquidity,
  and coverage.
- **FR-5.3** Surface forward-looking signals present in the extracted management commentary
  (extracted as text by the LLM; classified into risk / neutral / positive).

### 1.6 Credit scoring & rating (deterministic)
- **FR-6.1 Risk-factor catalog.** Assemble `RatingFactorResult`s covering: leverage risk,
  liquidity risk, profitability weakness, debt-service coverage weakness, cash-flow quality,
  deteriorating trend, sector underperformance, size/scale constraint, jurisdiction risk,
  management quality flag, concentration risk, off-balance-sheet risk — each with a fixed
  **weight**, a **severity** (`low | medium | high | critical`), a numeric **score
  contribution**, and **evidence**.
- **FR-6.2 Scorecard.** Sum contributions into a normalized **0–100** risk score; assign an
  **internal credit rating grade** (AAA / AA / A / BBB / BB / B / CCC / CC / C / D) and a
  **risk band** (`investment_grade_strong | investment_grade | sub_investment_grade |
  speculative | distressed | default`).
- **FR-6.3 PD / LGD.** Map the rating grade to a **probability of default** (0–1) and a
  **loss given default** (sector-adjusted, 0–1) for expected-loss calculation.
- **FR-6.4 Lending decision.** Map band + flags to a deterministic **lending decision**
  (`approve | approve_with_conditions | refer_credit_committee | decline`) and a suggested
  pricing band (e.g., "SOFR + 200–250 bps").

### 1.7 Web-sentiment enrichment (second, optional input)
Alongside the financial documents, a user can supply the **company name** (and optionally
country / industry / search region). The agent then searches **free, public** web and news
sources for the company's recent footprint and folds the result into the same rating.

- **FR-7a.1 Company seed.** Accept a `CompanySeed`: company name (required) plus optional
  aliases, country, industry, ticker and search region. If no name is supplied, fall back to
  the name read from the filing. The search can be disabled per run.
- **FR-7a.2 Free public search.** Search **free, public** sources only — DuckDuckGo web and
  news search via the free `ddgs` library, with `requests` + `beautifulsoup4` available to
  read public page text. **No API keys, no logins, nothing behind a sign-in wall.**
- **FR-7a.3 Sentiment angles.** Gather evidence from six angles: **general news**,
  **financial performance**, **adverse** (fraud / litigation / regulatory / default),
  **credit distress** (restructuring / covenant breach / layoffs / insolvency),
  **governance** (management / board / audit), and **market view** (analyst / rating /
  investor commentary).
- **FR-7a.4 Grounded synthesis.** The LLM (language only) reads the collected evidence and
  produces a grounded `WebSentimentResult`: a **disambiguation confidence** that the results
  are about the right company, an **overall sentiment** (positive / neutral / mixed /
  negative), classified **adverse findings** (category + severity + source url), positive
  highlights, citations, and data gaps. Every statement links back to its evidence.
- **FR-7a.5 Deterministic scoring of web signals.** The findings feed the **same
  deterministic scorecard** via two factors — **adverse media** and **market sentiment**.
  The LLM classifies findings; **Python decides their severity and score contribution**,
  applying a **credibility discount** (web evidence is weaker than audited accounts: each
  finding is discounted one notch, a single finding caps at medium, two corroborating
  high-severity findings restore high, and low disambiguation confidence caps the factor at
  medium). These factors carry **deliberately lower weight** than the audited financials so
  news never dominates a fundamentals-driven rating.
- **FR-7a.6 Graceful degradation.** If the search library is missing or the network is
  unavailable, the web step is skipped and the financial assessment proceeds unchanged (the
  two web factors stay 'low').

> **A note on reproducibility for the web flow.** The *scoring* is deterministic given a
> fixed set of findings. The *search* reads the live internet, so re-running on a different
> day can surface different evidence — that is simply how open research works. The evidence
> trail records exactly what was used each time, and the financial rating is unaffected when
> the web step is disabled or returns nothing.

### 1.8 Credit memo & Q&A (grounded LLM)
- **FR-7.1 Credit memo.** Synthesize an analyst-grade credit memo — executive summary,
  financial analysis narrative, risk factors, mitigants, recommended covenants, and
  monitoring triggers — **grounded strictly in the computed scorecard**, quoting its
  figures. The LLM never recomputes or overrides the rating.
- **FR-7.2 Case Q&A.** Natural-language Q&A over the assessment ("What drove the BBB
  rating?", "How does leverage compare to the sector?"), answered **only** from the
  computed assessment — never from model priors.

### 1.8 Governance & human-in-the-loop
- **FR-8.1** Log every query, extraction and final output for audit, with latency and
  LLM-call usage.
- **FR-8.2** Let a credit analyst / credit committee **review, correct and approve** the
  extracted line items and the final rating before the case proceeds; version the record
  (agent → human correction).
- **FR-8.3** Surface a full **reasoning / evidence trace** for each run — which ratios
  drove the score, which factor fired, and why.

---

## 2. Non-Functional Requirements

- **NFR-1 Reproducibility (paramount).** Identical financial statements yield an
  **identical rating grade and lending decision** — all ratio computation, scoring,
  benchmarking and trend classification are deterministic Python with fixed weights and
  thresholds; the LLM contributes no number to the score.
- **NFR-2 Grounding / anti-hallucination.** Extraction uses only retrieved page text; the
  credit memo quotes only computed values; missing or inconsistent inputs are flagged.
- **NFR-3 Traceability.** Every line item links back to a **page**; every ratio links to
  its inputs; every factor links to the ratios that fired it; the decision links to the
  rules that fired.
- **NFR-4 Auditability & explainability.** The scorecard exposes each factor's weight,
  severity and contribution — defensible to credit committee, internal audit, and the
  regulator. PD/LGD estimates cite the mapping table.
- **NFR-5 Security & compliance.** Encryption in transit / at rest, RBAC, per-client data
  isolation; VPC-/on-prem-first deployment; no sensitive financial data leaving the
  customer boundary. On-prem / private cloud models only.
- **NFR-6 Modularity & extensibility.** Swappable models, embedders and **sector benchmark
  feeds** (replace illustrative quartiles with Dun & Bradstreet / Refinitiv / internal
  portfolio data) without touching the pipeline; weights, thresholds and band cut-offs are
  config-driven.
- **NFR-7 Usability.** Upload financial statements, see the ratio table, benchmark
  comparison, trend chart, scorecard, rating, and a formatted credit memo in one view.
  Clear flags for missing inputs.
- **NFR-8 Performance.** Multi-document packs (five years of accounts = five PDFs) are
  indexed independently; extraction is structured, so packs scale roughly linearly.
- **NFR-9 Consistency of meaning.** A `standardised_label` means the same line item
  across all periods and documents; ratios, band cut-offs and PD/LGD tables are fixed and
  shared across all cases.

---

## 3. Business Requirements

- **BR-1** Replace manual financial-statement review: turn a multi-year filing pack into a
  computed ratio table, benchmarked scorecard, and a defensible credit rating in minutes.
- **BR-2** Improve **consistency** of credit decisions across analysts and booking centres
  — the same financials score the same way every time.
- **BR-3** Produce **auditable trails** for how each rating was derived (rule + ratio +
  page), ready for credit-committee sign-off and regulatory review (ICAAP, Basel).
- **BR-4** **Surface deteriorating cases automatically** — negative trends, sector
  underperformance, coverage weakness, or off-balance-sheet risk — so analysts focus effort
  where risk is.
- **BR-5** Produce a **first-cut credit memo** for analyst review, accelerating high-volume
  SME and mid-market credit processing without sacrificing grounding or traceability.

---

## 4. Impact & End Users

**Primary users**
- **Credit analysts** — ratio computation, scorecard, first-cut rating, memo draft.
- **Relationship managers** — faster, more predictable credit decisions for clients.
- **Mid-market / SME lending teams** — high-volume processing without analyst bottlenecks.

**Secondary users**
- **Credit committee / senior underwriters** — review, challenge and approve with a
  documented rationale and full factor breakdown.
- **Risk / regulatory reporting** — extract PD/LGD estimates and rating grades for
  ICAAP, Basel capital reporting, and stress testing.
- **Internal audit & regulators** — challenge how each rating and decision was derived.

**Impact**
- A multi-year filing pack becomes a computed ratio table + benchmarked scorecard + rated
  case + credit memo in minutes instead of days.
- Consistent, reproducible credit ratings reduce analyst-to-analyst variability and
  back-testing findings.
- Automatic early-warning trigger surfacing focuses scarce credit resource on genuine risk.
- A defensible audit trail from each decision back to the rule, ratio, and source page.

---

## 5. Evaluation Metrics

### 5.1 Business metrics
- **Time-to-decision** — submission → rated case + memo (pre/post).
- **Analyst acceptance rate** — % of first-cut memos accepted with minor edits only.
- **Rating consistency** — agreement / re-run stability of grade & decision (must be 100%).
- **Credit analyst productivity** — cases processed per analyst per week (pre/post).
- **Audit-finding rate** — credit-quality findings per 100 files (pre/post).
- **Early-warning hit rate** — % of flagged deteriorating cases confirmed by analyst.

### 5.2 Technical metrics
- **Line-item extraction accuracy** — precision/recall/F1 vs. curated ground truth per
  statement type.
- **Ratio accuracy** — computed values vs. analyst-verified or externally sourced ratios.
- **Trend classification accuracy** — direction correct vs. analyst-labeled.
- **Benchmark accuracy** — correct sector assignment and percentile classification.
- **Scorecard reproducibility** — identical grade & decision on re-run (must be 100%).
- **Grounding / hallucination rate** — memo claims contradicting or inventing computed
  facts.
- **Coverage** — % of required line items successfully extracted per period.
- **Web disambiguation accuracy** — % of web-sentiment runs where the surfaced evidence is
  confirmed to be about the correct company (guards same-name false positives).
- **Adverse-finding precision** — % of web adverse findings an analyst confirms as genuine.
- **System health** — uptime, error rate, latency, resource utilization.

---

## 6. Architecture (this build)

```
Financial filing pack                       company name (optional second input)
  PDF #1 (FY2023 audited accounts) ─┐                │
  PDF #2 (FY2022 audited accounts) ─┼─► doc_index    ▼
  PDF #N (interim / IM)             ┘   (pages+embed) osint.collect (free DuckDuckGo
                                            │          web + news, no API keys)
                                            │                │
                                            ▼                ▼
         GROUNDED EXTRACTION (LLM)              osint.sentiment (LLM, grounded)
           ├─ extract_company_metadata            → WebSentimentResult
           ├─ extract_line_items                    (adverse findings + overall tone,
           └─ extract_notes / facility               disambiguation confidence)
                                            │                │
                                            ▼                │
         marketdata.fetch_fundamentals (yfinance FALLBACK, deterministic)
           └─ fills only (line item, period) cells the documents lack; docs always win
                                            │                │
                                            ▼                │
         DETERMINISTIC ENGINE (Python — every decision-bearing number)
           ├─ ratios.compute_ratios      → FinancialRatios[] (one per period)
           ├─ benchmarks.benchmark       → SectorBenchmarkResult
           ├─ benchmarks.analyse_trends  → TrendAnalysis
           └─ scoring.score_case  ◄───────── web sentiment (adverse_media + market_sentiment,
                 → CreditScorecard (factors, 0–100, grade, PD/LGD, decision)   credibility-discounted)
                                            │
                                            ▼
         narrative.synthesize_credit_memo (LLM, grounded)   +   case Q&A (LLM, grounded)
```

**Design choice — deterministic + probabilistic hybrid.** The LLM reads language (filing
text and web snippets); all ratio arithmetic, benchmarking, trend classification, the
severity of web findings, scoring and the lending decision are plain Python over the
extracted records, so the credit rating is exact, reproducible and auditable. The web
sentiment is a **corroborating** input carried at low weight — it never overrides the
fundamentals-driven rating, and the financial assessment is unchanged when the web step is
disabled or returns nothing.

### File layout
```
Credit_Risk_Assesment_Agent/
├── REQUIREMENTS.md                 ← this document
├── requirements.txt
├── .env.example
├── credit_risk/
│   ├── __init__.py
│   ├── config.py                   ← LLM + embedder clients (gateway-agnostic)
│   ├── reference.py                ← sector benchmarks, rating-grade tables, thresholds
│   ├── schemas.py                  ← line items, ratios, scorecard + assessment records
│   ├── doc_index.py                ← PDF → pages + semantic index
│   ├── tools.py                    ← agent tools (search_pages / get_page)
│   ├── extraction.py               ← grounded line-item, metadata & notes extraction
│   ├── marketdata.py               ← yfinance fallback (fixed label map; fills doc gaps)
│   ├── ratios.py                   ← deterministic ratio computation from line items
│   ├── benchmarks.py               ← deterministic sector benchmarking + trend analysis
│   ├── scoring.py                  ← deterministic credit scorecard + lending decision
│   │                                  (incl. adverse-media + market-sentiment factors)
│   ├── narrative.py                ← grounded credit-memo synthesis
│   ├── agent_graph.py              ← run_credit_assessment (pack + web orchestration)
│   └── osint/
│       ├── __init__.py
│       ├── search.py               ← FREE DuckDuckGo web/news wrappers (ddgs, no keys)
│       ├── collect.py              ← per-angle query building + evidence collection
│       └── sentiment.py            ← grounded WebSentimentResult synthesis
└── app_streamlit/
    └── ui.py                       ← ratios, benchmarks, trend, web sentiment, memo, trace
```

### Run
```bash
pip install -r requirements.txt
cp .env.example .env          # fill in gateway endpoint + keys; point benchmarks at live data

# Streamlit UI (upload filing pack; optional company name for web sentiment):
streamlit run app_streamlit/ui.py

# CLI — financials only:
python -m credit_risk.agent_graph fy2023.pdf fy2022.pdf fy2021.pdf -- "What drove the BBB rating?"

# CLI — financials + free web sentiment (DuckDuckGo):
python -m credit_risk.agent_graph fy2023.pdf fy2022.pdf \
    --company "Acme Manufacturing Ltd" --region uk-en -- "Does the web sentiment change the picture?"

# CLI — documents topped up from yfinance where figures are missing:
python -m credit_risk.agent_graph partial_accounts.pdf --ticker AAPL -- "What drove the rating?"

# CLI — no documents at all: assess straight from yfinance fundamentals:
python -m credit_risk.agent_graph --ticker TSLA --company "Tesla Inc" -- "Summarize the credit profile."
```

> **Free search stack.** The web-sentiment step uses `ddgs` (DuckDuckGo web + news) plus
> `requests` + `beautifulsoup4` — all free, no API keys. Set the region per case (UI field)
> or globally via `OSINT_REGION` (e.g. `uk-en`, `us-en`, `in-en`). If the library is missing
> or the network is down, the search returns nothing and the financial assessment still
> runs. Only publicly available information is collected; nothing behind a login.

> **Market-data fallback.** The yfinance step uses the free `yfinance` library (Yahoo
> Finance, no API key). It only fills financial-statement cells the uploaded documents do
> not contain — the audited documents always win — and each fallback figure is labelled
> `yfinance:<TICKER>` in the line-item table and trace so it is never confused with a
> filed number. Yahoo's row labels are mapped to the standardised vocabulary by a fixed
> dictionary; coverage and recency depend on what Yahoo publishes for the ticker. For a
> private company with no listing, omit the ticker and rely on the documents.

> **Reference data note.** `reference.py` ships **illustrative sector benchmarks** and
> **sample rating-grade PD tables** so the system runs end-to-end out of the box. In
> production, point these at your internal portfolio data, Dun & Bradstreet / Refinitiv
> sector benchmarks, and your IRB-validated PD/LGD tables. The scoring logic does not
> change.

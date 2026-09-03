# Investment Research Copilot — Portfolio & Risk Monitoring (+ Construction)

## In plain English (no jargon)

A portfolio manager has to answer two questions, over and over:

1. **"Is my portfolio still where it should be — and what should I do about it?"** Markets
   move, so the mix of stocks / bonds / cash drifts away from the plan, one holding quietly
   grows too large, a dividend doesn't show up, volatility creeps past what the client signed
   up for, and bad news breaks on a name you own. Catching all of that by hand, across many
   accounts, every day, is slow and error-prone.
2. **"If I were starting today, what would I buy — and why?"** Building a sensible portfolio
   from scratch for a given risk profile, then being able to justify every holding.

This tool does both. You give it a **portfolio as structured data** (a list of holdings and a
transactions ledger — a CSV/JSON export, *not* a document), pick the **mandate** (the agreed
risk plan), and it:

1. **Values** every position from **free live market data** (Yahoo Finance via `yfinance`).
2. **Tracks allocation** — the current mix by asset class, sector, region, currency and
   position — and measures **drift** against the plan's target allocation and tolerance bands.
3. **Proposes a rebalancing plan** — the exact buys/sells to get back on target, each with a
   reason.
4. **Identifies cash-flow variances** — compares the income you *should* have received
   (dividends/coupons/interest) against the **actual** ledger, and flags shortfalls, missing
   distributions and unexpected outflows.
5. **Computes portfolio risk** — volatility, max drawdown, Value-at-Risk, beta, tracking
   error and concentration — and checks each against your **risk limits**.
6. **Scans free public news** for **emerging risks** on your largest holdings, sectors and the
   macro backdrop.
7. **Raises prioritised alerts** for rebalancing, concentration, limit breaches, cash-flow
   variances and emerging risks — each with a recommended action — and rolls everything into a
   **portfolio health score** and a **monitoring briefing**.

It can also **construct a portfolio from scratch** for a mandate, choosing securities from a
universe fetched live from `yfinance`, with **the reason for every choice**.

The important design rule, shared with the other agents in this book: the **LLM does only
language work** (classify news, write the briefing, explain a construction, answer questions).
**Every decision-bearing number** — allocation, drift, rebalancing trades, cash-flow
variances, risk metrics, limit breaches, alert severities, the health score, and every hard
constraint — is computed by **deterministic Python**, so the **same inputs always produce the
same alerts** and the **same guardrailed portfolio**.

---

> **Business Scope.** A continuously-runnable **portfolio & risk monitoring** copilot for
> discretionary and advisory portfolios, plus a **from-scratch portfolio construction**
> capability. Inputs are **structured and live** — a holdings snapshot and a cash-flow ledger
> (CSV/JSON; an optional PDF statement path is supported), enriched with **free live market
> data** — checked against an **Investment Policy Statement** (target allocation, tolerance
> bands, risk limits). The agent produces:
>
> - **Allocation tracking** across asset class, sector, region, currency and position.
> - **Deterministic drift detection** vs the policy, with a **rebalancing trade plan** (each
>   trade explained).
> - **Deterministic cash-flow variance** — expected vs actual income, with shortfall / missing
>   / unexpected flags.
> - **Deterministic risk metrics** — volatility, max drawdown, VaR, beta, tracking error,
>   concentration (HHI / effective N) — and **risk-limit checks**.
> - **Free emerging-risk news scan** (DuckDuckGo), LLM-classified and credibility-discounted.
> - **Prioritised alerts** + a **portfolio health score / band** + a **monitoring briefing**.
> - **Portfolio construction from scratch** for a mandate, in a **deterministic** and an
>   **LLM-augmented** mode that can be **compared side-by-side**, with a stated **rationale**
>   for every position.
>
> **The single most important reliability decision:** the LLM does only *language* work. Every
> *decision-bearing* computation — valuation, allocation, drift, rebalancing sizing, cash-flow
> variance, every risk metric, the limit checks, the alert severities, the health score, and
> all hard portfolio constraints — runs in **deterministic Python**, so monitoring is
> **reproducible** and the LLM can **never** breach a constraint when constructing a portfolio.

---

## 1. Functional Requirements

### 1.1 Structured ingestion (the input model)
- **FR-1.1** Accept a **holdings snapshot** as CSV or JSON: per position `symbol`, optional
  `name`, `asset_class`, `sector`, `region`, `currency`, `quantity`, `cost_basis`, and an
  optional manual `price`. Column names are matched flexibly (`ticker`/`symbol`,
  `qty`/`quantity`/`shares`, …).
- **FR-1.2** Accept a **transactions / cash-flow ledger** as CSV or JSON: per entry `date`,
  `type` (dividend / coupon / interest / contribution / withdrawal / fee / tax / trade / …),
  optional `symbol`, signed `amount`, `currency`, `note`.
- **FR-1.3** Accept a **mandate** (conservative / balanced / growth / aggressive) and a base
  currency. The mandate selects the IPS target allocation, tolerance band, benchmark and
  volatility ceiling.
- **FR-1.4 Optional PDF ingest.** When only a PDF brokerage/custodian statement is available,
  a grounded LLM extractor transcribes the holdings table into the **same** `Holding` records
  (numbers transcribed exactly as printed — no arithmetic), which then flow through the
  identical deterministic engine. Text PDFs only (no OCR).

### 1.2 Market-data enrichment (free, yfinance only)
- **FR-2.1** For each held / candidate symbol fetch a **live price**, **light classification**
  (quote type, sector, dividend rate) and **daily price history** from Yahoo Finance via the
  free `yfinance` library. **No API keys; yfinance is the only market-data source.**
- **FR-2.2** Price priority is **manual price on input > market-data price > derived from cost
  basis**; an unpriceable position is flagged and excluded from allocation/risk (a data-quality
  alert). The fraction of market value with usable price history is reported as **data
  coverage**.
- **FR-2.3 Graceful degradation.** If `yfinance` is unavailable or the network is down, prices
  fall back to manual/cost-basis values, risk metrics report reduced coverage, and the rest of
  the pipeline runs unchanged.

### 1.3 Allocation tracking (deterministic)
- **FR-3.1** Compute each position's market value and weight, and the allocation breakdown by
  **asset class, sector, region, currency and position**.

### 1.4 Drift & rebalancing (deterministic — core)
- **FR-4.1** Compare the actual asset-class weights against the mandate's **strategic asset
  allocation** and **tolerance bands**, classifying each sleeve as within-band / overweight /
  underweight / **breached** (over or under).
- **FR-4.2** For each breached sleeve, propose a **rebalancing trade** sized to return it to
  target (buy/sell value and % of book), with a **rationale** naming the breached band. Report
  one-way **turnover** and an estimated transaction cost.
- **FR-4.3** Sleeves inside their band generate no trades (no needless turnover).

### 1.5 Cash-flow variance (deterministic)
- **FR-5.1** Derive **expected income** per holding over the ledger's date span: from the live
  yfinance dividend rate when available, else an **assumed asset-class yield** (offline basis),
  prorated to the period.
- **FR-5.2** Aggregate **actual income** (dividends / coupons / interest) per symbol from the
  ledger and compute the variance, classifying each as **on_track / shortfall / excess /
  missing**, with a deterministic severity (excess income is informational, not alerted).
- **FR-5.3** Flag **unexpected** flows: income for a symbol not held, and large non-income
  outflows (fees / withdrawals / tax) above a configurable absolute threshold.

### 1.6 Risk metrics & limits (deterministic)
- **FR-6.1** Compute portfolio **annualised volatility, max drawdown, 1-day 95% historical
  VaR, beta and tracking error** (vs the mandate benchmark), **dividend yield**, and
  **concentration** (HHI, effective N, top-1 / top-5 weights) from weights + price history.
- **FR-6.2** Check each metric and weight against configured **risk limits** — single-name
  cap, sector cap, minimum cash, volatility ceiling (mandate-specific), drawdown alert, VaR
  limit, tracking-error limit — classifying each as ok / warn / breach.

### 1.7 Emerging-risk news scan (free; LLM classifies, Python discounts)
- **FR-7.1** Build news queries for the **largest single names**, the **most-concentrated
  sectors**, and the **macro/market/geopolitical** backdrop, and collect public results from
  **free DuckDuckGo** (no keys).
- **FR-7.2** The **LLM classifies** the snippets into `EmergingRiskFinding`s (category +
  severity + summary) and an overall **risk tone** — grounded strictly in the snippets. A
  **deterministic keyword fallback** runs if the LLM is unavailable.
- **FR-7.3** Findings fold into the alerts and the score under a **credibility discount** (a
  single public-news item never outranks a hard limit breach). The scan **fails soft** (no
  library / no network ⇒ no findings).

### 1.8 Alerts & portfolio health score (deterministic)
- **FR-8.1** Raise a **prioritised alert list** from drift breaches, single-name / sector
  concentration, risk-limit breaches, drawdown, cash-flow variances, emerging-risk findings and
  data-quality issues — each with a **severity**, **evidence** and a **recommended action**.
- **FR-8.2** Roll the deterministic facts into a fixed-weight **portfolio risk score** (0–100,
  higher = less healthy) and a **health band** (healthy / watch / elevated / critical), with a
  fully auditable factor breakdown.

### 1.9 Portfolio construction from scratch (deterministic + LLM, comparable)
- **FR-9.1 Universe.** Assemble candidates from a curated liquid-ticker pool (or a
  user-supplied ticker list) and **enrich each live from yfinance** (price, classification,
  volatility, dividend). yfinance is the only data source.
- **FR-9.2 Deterministic constructor.** Take the mandate's strategic asset allocation, map
  candidates to sleeves, weight **within each sleeve** by the chosen method (**equal-weight**
  default; inverse-volatility; risk-parity-lite), and size positions to the investable amount.
  **Every weight and exclusion carries a rationale.**
- **FR-9.3 LLM-augmented constructor.** The LLM freely selects securities, tilts and weights
  **with a reason for each**, grounded in the candidates' deterministic stats — and is then
  passed through the **same hard guardrails** (see FR-9.4), so it can never breach a constraint.
- **FR-9.4 Hard guardrails (deterministic).** Enforce exclusions, the single-name cap
  (water-filled so no name exceeds it and no weight is lost), the sector cap, and the minimum
  position size; renormalise; convert to share quantities at live prices; compute expected
  risk metrics. **Every override of a proposal is recorded.**
- **FR-9.5 Comparison.** When run in **both** modes, diff the two portfolios: per-sleeve
  allocation deltas, selected-name overlap (Jaccard), and expected-metric deltas.

### 1.9a Investor profile (drives construction)
- **FR-9a.1 Geography.** The investor chooses the geography mode — **global**, a **single
  country**, or a **list of countries** — and the equity sleeve is built from the **most
  trusted curated names of those countries**, ranked **by expected return**. With explicit
  countries, the equity sleeve is split across them so each named country is represented.
- **FR-9a.2 Style & market cap.** Support **large-cap / mid-cap / small-cap / multi-cap**
  (with a configurable large/mid/small **weightage**), **fund-of-funds**, **thematic** (a
  free-text theme maps to representative baskets), and **ELSS** (tax-advantaged equity).
  Market cap is classified from the live yfinance market-cap value (threshold-based), with the
  curated tier as a fallback.
- **FR-9a.3 Equity/Debt split.** Accept an explicit **equity:debt ratio** that overrides the
  mandate's equity/fixed-income proportion.
- **FR-9a.4 Instrument preference.** Optionally filter the universe to **stocks / ETFs /
  mutual funds**.
- **FR-9a.5 Time frame & return objective.** Accept the **horizon** and a **return objective**
  expressed either as an **annualised %** (e.g. 12% p.a.) **or** as a **target end value**
  (e.g. 100k → 150k over the horizon, converted to an implied annual return).
- **FR-9a.6 Feasibility / unrealistic-target check.** Deterministically compare the objective
  to the book's expected return and the universe's best single-asset return, and **flag
  unrealistic targets** with a plain-English message (which the LLM phrases in the briefing).

### 1.9b Per-stock pros & cons (geopolitical / sector aware)
- **FR-9b.1** For **each suggested security**, produce concise **pros and cons of holding that
  specific name**, grounded in its stats (expected return, volatility, market cap, country)
  and in the **sector/domain geopolitical events & sentiment** surfaced by the emerging-risk
  news scan for that symbol or sector. The LLM writes these (language only); a deterministic
  fallback runs when the LLM is unavailable. Pros/cons are **annotations** — they never change
  the selection or the weights.

### 1.10 Briefing, Q&A & explainability
- **FR-10.1 Dual-mode briefing.** Produce a **deterministic (templated)** briefing from the
  facts (fully offline) and/or a **grounded LLM** briefing that quotes the computed numbers and
  never recomputes them. **Both** mode shows them side-by-side.
- **FR-10.2 Q&A.** Answer natural-language questions **only** from the computed result.
- **FR-10.3 Explainability (first-class).** Every drift line, rebalancing trade, alert, and
  constructed position carries an explicit **rationale / recommended action** — the agent
  always states the reason for its choices.

### 1.11 Governance & traceability
- **FR-11.1** Emit a step-by-step **reasoning trace** and run **metrics** (latency, LLM-call
  count, data coverage) for every run.
- **FR-11.2** Log market-data fetches, drift, limit breaches, construction selections and every
  guardrail correction for audit.

---

## 2. Non-Functional Requirements

- **NFR-1 Reproducibility (paramount).** Identical inputs yield identical allocation, drift,
  rebalancing, cash-flow variances, risk metrics, limit breaches, alerts, health score, and the
  same guardrailed construction — all decision-bearing logic is deterministic Python with fixed
  weights and thresholds; the LLM contributes no number. (The only variability is which day's
  market history / news the free sources return, recorded in the trace.)
- **NFR-2 Grounding / anti-hallucination.** The LLM sees only the computed facts (briefing /
  Q&A) or the collected snippets (news) or the candidate stats (construction); it is instructed
  to quote, not invent, and never to change a number, an alert, or the health band.
- **NFR-3 Hard-constraint safety.** No LLM-proposed portfolio can violate a single-name cap,
  sector cap, exclusion or minimum position — the deterministic guardrails enforce them and
  record every correction.
- **NFR-4 Traceability & auditability.** Every position links to a price source; every metric
  to its inputs; every alert to its evidence and recommended action; the score exposes each
  factor's weight, severity and contribution.
- **NFR-5 Cost & openness.** Market data and news use **only free sources** (`yfinance`,
  DuckDuckGo) with **no API keys**. The LLM targets a configurable OpenAI-compatible gateway.
- **NFR-6 Graceful degradation.** Missing market data, missing news library, missing LLM
  gateway, or a missing ledger each degrade a feature without breaking the run; the fully
  deterministic, offline path always works.
- **NFR-7 Modularity & extensibility.** The IPS targets, tolerance bands, risk limits, assumed
  yields and construction universe are **config-driven** (JSON via `*_PATH` env vars) and swap
  without touching the engine. Weighting methods and the LLM/model are pluggable.
- **NFR-8 Usability.** One screen to pick the task (monitor / construct) and the engine mode
  (deterministic / LLM / both), upload the inputs, and read allocation, drift, rebalancing,
  cash flow, risk, alerts, emerging risk, the briefing and the trace.
- **NFR-9 Security & deployment.** No portfolio data leaves the customer boundary except the
  free market-data / news lookups (symbols and public queries); the LLM gateway is
  VPC-/on-prem-deployable. No trade execution.

---

## 3. Business Requirements

- **BR-1** Replace manual portfolio checking: turn a holdings file into a valued book,
  drift-vs-policy view, rebalancing plan, cash-flow reconciliation, risk dashboard and a
  prioritised alert list in seconds.
- **BR-2** **Improve consistency** of monitoring across managers and accounts — the same
  portfolio raises the same alerts every time.
- **BR-3** **Surface problems early and automatically** — drift, concentration, limit breaches,
  income shortfalls and emerging news risk — so managers focus where the risk is.
- **BR-4** Produce **defensible, explainable output** — every trade, alert and constructed
  holding states its reason — ready for an investment-committee or compliance review.
- **BR-5** Accelerate **portfolio construction** with a reproducible deterministic baseline and
  an LLM advisor, comparable side-by-side, both constrained by the same hard rules.

---

## 4. Impact & End Users

**Primary users**
- **Portfolio managers / discretionary investment managers** — daily monitoring, rebalancing
  decisions, and from-scratch construction.
- **Financial advisors / wealth managers** — keep many client portfolios on-mandate and explain
  recommendations.

**Secondary users**
- **Risk & compliance** — limit-breach surveillance, concentration monitoring, an auditable
  trail from each alert to its evidence and rule.
- **Investment committee** — review constructed portfolios and rebalancing proposals with a
  stated rationale and a comparison of approaches.
- **Operations** — cash-flow variance flags (missing dividends, unexpected outflows) for
  reconciliation against the custodian.

**Impact**
- A holdings file becomes a valued, benchmarked, risk-assessed, alerted portfolio in seconds
  instead of a manual spreadsheet pass.
- Reproducible alerts reduce manager-to-manager variability and missed breaches.
- Early-warning surfacing (drift, concentration, income shortfalls, emerging risk) focuses
  scarce attention on genuine risk.
- A defensible audit trail from each decision back to the rule, the metric and the source.

---

## 5. Evaluation Metrics

### 5.1 Business metrics
- **Time-to-monitor** — holdings file → full alerted assessment (pre/post).
- **Breach-catch rate** — % of true allocation / limit breaches surfaced vs a manual review.
- **Alert precision** — % of raised alerts a manager confirms as actionable (alert fatigue
  guard).
- **Rebalancing adoption** — % of proposed rebalancing plans accepted with minor edits.
- **Cash-flow exception hit rate** — % of flagged income variances confirmed as genuine
  (missing dividend, fee error, unauthorised flow).
- **Construction acceptance** — % of constructed portfolios accepted / lightly edited.

### 5.2 Technical metrics
- **Monitoring reproducibility** — identical alerts & score on re-run with the same prices
  (must be 100% given fixed market data).
- **Constraint-safety** — 0 hard-constraint violations in any constructed portfolio (single-name
  / sector / exclusion / min size), including LLM-proposed ones.
- **Valuation / data coverage** — fraction of market value successfully priced and with usable
  history.
- **Risk-metric accuracy** — computed volatility / VaR / beta vs an independent calculation.
- **Drift / rebalancing correctness** — sleeve classification and trade sizing vs hand
  calculation.
- **Grounding / hallucination rate** — briefing or Q&A claims that contradict or invent a
  computed fact (target 0).
- **News disambiguation / adverse-finding precision** — % of emerging-risk findings confirmed
  relevant to the held name (guards same-name false positives).
- **System health** — latency, error rate, free-source availability, LLM-call count.

---

## 6. Architecture (this build)

```
            ┌──────────────── INPUTS (structured, live) ─────────────────┐
 holdings.csv/json   transactions.csv/json   mandate/IPS   (optional PDF statement)
            └───────────────────────────────┬─────────────────────────────┘
                                             ▼
        MARKET DATA — yfinance ONLY (free): price · 1y history · classification · dividend
                                             ▼
        DETERMINISTIC ENGINE (no LLM — every decision-bearing number)
          allocation ─► drift vs IPS + rebalancing plan ─► cash-flow variance
          ─► risk metrics + limit checks ─► alert engine ─► portfolio risk score
                                             ▼
   ┌──────────── TASK A: CONSTRUCT ───────────┐   ┌────────── TASK B: MONITOR ───────────┐
   │ universe (yfinance-enriched)             │   │ deterministic templated briefing      │
   │ deterministic optimizer + rationale ─┐   │   │ LLM grounded briefing + Q&A           │
   │ LLM advisor (free pick) ─► GUARDRAILS ┘   │   │ emerging-risk news scan (LLM, free)   │
   │ ─► ConstructionComparison (side-by-side) │   │ (numbers identical in both modes)     │
   └───────────────────────────────────────────┘   └───────────────────────────────────────┘
                                             ▼
                    Streamlit dashboard (task × mode × compare)   /   CLI
```

**Design choice — deterministic + probabilistic hybrid.** The LLM reads language (news
snippets, the computed facts) and exercises selection judgement (construction); all valuation,
allocation, drift, rebalancing sizing, cash-flow variance, risk metrics, limit checks, alert
severities, the health score and every hard constraint are plain Python over the structured
records, so monitoring is exact, reproducible and auditable, and the LLM can never breach a
constraint. The emerging-risk news is a **corroborating** input carried at a discount.

### File layout
```
Investment_Research_Copilot/
├── REQUIREMENTS.md                 ← this document
├── requirements.txt
├── .env.example
├── portfolio_monitor/
│   ├── __init__.py
│   ├── config.py                   ← LLM gateway client (language only); SSL bootstrap
│   ├── logconf.py                  ← named logger + reasoning trace
│   ├── schemas.py                  ← inputs + construction + monitoring outputs (rationale fields)
│   ├── reference.py                ← IPS targets/bands, risk limits, assumed yields, universe seed, scoring
│   ├── ingest.py                   ← load holdings/transactions from CSV/JSON (primary input)
│   ├── statement_extract.py        ← OPTIONAL grounded PDF statement → Holdings (LLM)
│   ├── marketdata.py               ← yfinance prices/history/classification/dividends (fail-soft, cached)
│   ├── analytics.py                ← deterministic primitives: returns, vol, drawdown, VaR, beta, HHI
│   ├── allocation.py               ← valuation + weights + breakdowns
│   ├── drift.py                    ← drift vs IPS bands + rebalancing plan (each trade explained)
│   ├── cashflow.py                 ← expected-vs-actual income variance + severity
│   ├── risk.py                     ← portfolio risk metrics + risk-limit checks
│   ├── alerts.py                   ← deterministic alert engine
│   ├── scoring.py                  ← portfolio risk score + health band
│   ├── universe.py                 ← construction universe (yfinance-enriched)
│   ├── compare.py                  ← diff two constructed portfolios
│   ├── construct/
│   │   ├── deterministic.py        ← rules/optimizer constructor + rationale
│   │   ├── llm_advisor.py          ← LLM-augmented constructor (free pick) + guardrails
│   │   ├── guardrails.py           ← hard-constraint enforcement (water-fill caps, min size, quantities)
│   │   └── common.py               ← shared: positions → expected metrics / sleeve allocation
│   ├── monitor/
│   │   ├── deterministic.py        ← templated briefing (offline)
│   │   └── llm_briefing.py         ← grounded LLM briefing + Q&A
│   ├── osint/
│   │   ├── search.py               ← free DuckDuckGo web/news wrappers (no keys)
│   │   ├── collect.py              ← emerging-risk query building per holding/sector/macro
│   │   └── scan.py                 ← grounded MarketRiskScan (LLM) + keyword fallback
│   └── agent_graph.py              ← run_monitoring(...) + run_construction(...) + CLI
├── app_streamlit/
│   └── ui.py                       ← task (Monitor/Construct) × mode (Deterministic/LLM/Both)
└── sample_data/
    ├── holdings.csv                ← sample drifted multi-asset portfolio
    └── transactions.csv            ← sample cash-flow ledger
```

### Run
```bash
pip install -r requirements.txt
cp .env.example .env          # fill in the LLM gateway for the LLM/Both modes (optional)

# Streamlit UI (pick task + mode, upload files or use the bundled sample):
streamlit run app_streamlit/ui.py

# CLI — monitor the sample portfolio, deterministic & offline (no LLM, no news):
python -m portfolio_monitor.agent_graph monitor sample_data/holdings.csv \
    --transactions sample_data/transactions.csv --mandate balanced \
    --mode deterministic --no-news

# CLI — monitor with the grounded LLM briefing, news scan and a question:
python -m portfolio_monitor.agent_graph monitor sample_data/holdings.csv \
    --transactions sample_data/transactions.csv --mandate balanced --mode both \
    -- "What are the top two actions I should take and why?"

# CLI — construct a portfolio from scratch and compare deterministic vs LLM:
python -m portfolio_monitor.agent_graph construct --mandate growth \
    --amount 1000000 --method equal_weight --mode both

# CLI — ingest a PDF brokerage statement instead of a CSV (needs the LLM):
python -m portfolio_monitor.agent_graph monitor --statement account.pdf --mandate balanced
```

> **Free data stack.** Market data is **yfinance** (Yahoo Finance) only — no API key, no paid
> feed. The emerging-risk scan uses **DuckDuckGo** via `ddgs` (free). If a library is missing
> or the network is down, that feature is skipped and the deterministic assessment still runs.

> **Reference data note.** `reference.py` ships **illustrative** IPS targets, tolerance bands,
> risk limits, assumed yields and a construction universe so the system runs out of the box. In
> production, point these at your firm's Investment Policy Statement, risk framework and
> approved universe via the `*_PATH` environment variables — the engine logic does not change.

> **Reproducibility note.** Given a fixed set of prices, monitoring is fully deterministic and
> re-runs identically. Because prices and news come from live free sources, the absolute
> numbers move with the market between runs; the trace records exactly what was used. The
> construction guardrails are deterministic and constraint-safe regardless of what the LLM
> proposes.

> **Scope / non-goals.** No trade execution or order routing; no OCR for scanned PDFs; single
> base currency in v1 (FX normalisation is a documented extension point); illustrative PD-free
> risk model (historical, not a forward forecast).

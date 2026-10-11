# Credit Risk Assessment Agent

**SME through Large-Corporate internal rating from financial statements** — deterministic scorecard, grounded LLM extraction, credit memo, and audit trail.

---

## Overview

This agent automates the end-to-end credit assessment workflow:

1. **Ingest** — Upload one or more PDFs (audited accounts, interims, info memo)
2. **Extract** — LLM (Gemini `gemini-3.5-flash-lite` via plain `requests`) reads statements, transcribes line items, maps to standard labels
3. **Compute** — Deterministic ratios (`ratios.py`), sector benchmarks (`benchmarks.py`), trend analysis
4. **Score** — Fixed-weight scorecard → internal rating (AAA…D) → PD/LGD/EL → lending decision
5. **Memo** — Grounded narrative memo quoting the deterministic facts
6. **Review** — Human-in-the-loop: approve / correct / reject with full audit trail

All arithmetic is **reproducible** — the same inputs always produce the same rating.

---

## Quick Start

```bash
# 1) Clone & enter
cd Credit_Risk_Assesment_Agent

# 2) Install deps
pip install -r requirements.txt

# 3) Configure (copy .env.example → .env and fill in)
cp .env.example .env
# Required: GEMINI_API_KEY, PG_*, REDIS_* (optional)

# 4) Start Postgres (with pgvector) and Redis, then init schema
python -c "from credit_risk.db.models import init_schema; init_schema()"

# 5a) Run API
uvicorn credit_risk.api.main:app --reload --host 0.0.0.0 --port 8000

# 5b) Run Streamlit UI (in another terminal)
streamlit run app_streamlit/ui.py
```

**Open** `http://localhost:8501` — the UI talks to the API at `http://localhost:8000`.

---

## Project Structure

```
Credit_Risk_Assesment_Agent/
├── credit_risk/
│   ├── api/              # FastAPI routes: POST /assessments, GET /assessments/{id}, /status, /ask, /review
│   ├── db/               # SQLAlchemy + pgvector models, session, persistence
│   ├── osint/            # Free web-sentiment (DuckDuckGo search + LLM synthesis)
│   ├── extraction.py     # Grounded LLM extraction (line items, metadata, notes, facility)
│   ├── ratios.py         # Deterministic financial ratios from LineItems
│   ├── benchmarks.py     # Sector quartiles + multi-period trend classification
│   ├── scoring.py        # Fixed-weight scorecard → grade, band, PD/LGD, decision
│   ├── narrative.py      # Grounded credit memo synthesis
│   ├── agent_graph.py    # Orchestration: extract → compute → score → memo
│   ├── tool_loop.py      # Bounded LangChain tool-calling loop (search_pages/get_page)
│   ├── tools.py          # LangChain tools bound to per-assessment PgPageIndex
│   ├── llm_json.py       # JSON parsing + product-extraction demo (prompt|Gemini|JsonOutputParser)
│   ├── config.py         # Gemini (requests) + OpenAIEmbeddings from env
│   ├── llm_validation.py # Correctness gates (classification/extraction/suggestion)
│   ├── config.py         # LLM / embedding config
│   └── schemas.py        # Pydantic domain models (LineItem, FinancialRatios, CreditScorecard, …)
├── app_streamlit/ui.py   # Streamlit UI (pure HTTP client of the API)
├── tests/                # pytest suites
├── examples/
│   └── parse_product_gemini.py   # Standalone Gemini product-extraction demo
├── requirements.txt
├── pyproject.toml
├── .env.example
├── CASE_STUDY.md         # Full product spec
└── DOCUMENTATION.md      # Architecture deep-dive
```

---

## Key Commands

| Task | Command |
|------|---------|
| Install deps | `pip install -r requirements.txt` |
| Run API | `uvicorn credit_risk.api.main:app --reload --host 0.0.0.0 --port 8000` |
| Run Streamlit UI | `streamlit run app_streamlit/ui.py` |
| Init DB schema | `python -c "from credit_risk.db.models import init_schema; init_schema()"` |
| Run tests | `pytest tests/ -v` |
| Lint (ruff) | `ruff check . && ruff format --check .` |
| Lint fix | `ruff check --fix . && ruff format .` |
| Product demo | `python examples/parse_product_gemini.py` (reads `GEMINI_API_KEY` from env) |

---

## Environment Variables (`.env`)

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | **Required** — Gemini API key for chat LLM | — |
| `LLM_MODEL_NAME` | Gemini model | `gemini-3.5-flash-lite` |
| `LLM_TEMPERATURE` | Determinism (0.0 for credit) | `0.0` |
| `LLM_MAX_TOKENS` | Max output tokens | `4096` |
| `EMBED_MODEL_NAME` | Embedding model (OpenAI-compatible gateway) | `openai.text-embedding-3-small` |
| `LLM_ENDPOINT` | OpenAI-compatible base URL (for embeddings) | `""` |
| `PG_HOST/PORT/USER/PASSWORD/DATABASE` | Postgres connection | `127.0.0.1:5433` |
| `REDIS_HOST/PORT/DB` | Redis for live phase tracking (optional) | `127.0.0.1:6379/0` |
| `CREDIT_RISK_API_BASE_URL` | Streamlit → API base URL | `http://localhost:8000` |
| `OSINT_MAX_RESULTS` | Web-sentiment results per query | `5` |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/assessments` | Create assessment (upload PDFs, optional company seed) |
| `GET` | `/assessments/{id}` | Full assessment (scorecard, memo, ratios, trace) |
| `GET` | `/assessments/{id}/status` | Live phase (Redis) or durable status (Postgres) |
| `POST` | `/assessments/{id}/rerun` | Rerun with same docs (creates new version) |
| `POST` | `/assessments/{id}/ask` | Ask a follow-up question (grounded RAG) |
| `POST` | `/assessments/{id}/review` | Submit review: approve / correct / reject |
| `GET` | `/assessments/{id}/audit` | Full step trace |
| `GET` | `/health` | Liveness probe |

---

## Docker

```bash
# Build
docker build -t credit-risk-api:latest .

# Run (requires external Postgres + Redis; pass via --env-file .env)
docker run -p 8000:8000 --env-file .env credit-risk-api:latest
```

---

## Tech Stack

| Layer | Tech |
|-------|------|
| LLM | Gemini via plain `requests` (`gemini-3.5-flash-lite`) |
| Embeddings | `langchain_openai.OpenAIEmbeddings` (OpenAI-compatible gateway) |
| Framework | `langchain-core`, `langgraph` (light), custom tool loop |
| DB | PostgreSQL + `pgvector` (via `psycopg[binary]`, `sqlalchemy`) |
| Cache/Phase | Redis (optional, degrades gracefully) |
| API | FastAPI, Uvicorn |
| UI | Streamlit (HTTP client) |
| PDF | PyMuPDF (`fitz`) |
| Search | `ddgs` (DuckDuckGo), `requests` + `beautifulsoup4` |
| Logging | JSON + OpenTelemetry trace correlation |

---

## Related Docs

- `CASE_STUDY.md` — Product requirements, NFRs, data flows
- `DOCUMENTATION.md` — Architecture, module guide, extension points
- `NEW_CASE_STUDY.md` — Updated spec with review workflow

---

## License

MIT — see `LICENSE` in repo root.
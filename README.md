# Finance & Banking AI Use-Cases

Four production-grade **agentic AI** systems for credit risk, document intelligence, KYC onboarding, and investment research — sharing a common grounded-LLM + deterministic-analytics architecture.

---

## Projects

| Agent | Purpose | Key Tech | Port |
|-------|---------|----------|------|
| **Credit Risk Assessment** | SME → Large-Corp internal rating from financial statements | Gemini LLM, pgvector, deterministic scorecard | 8000 |
| **FinDoc Intelligence** | PDF ingestion → extraction → normalized ratios → RAG index | PyMuPDF, grounded extraction, pgvector | 8000 |
| **HNI Onboarding & KYC** | Beneficial-ownership graph, sanctions/PEP screening, EDD memo | Ownership DAG, DuckDuckGo OSINT, risk scorecard | 8000 |
| **Investment Research Copilot** | Portfolio monitoring, factor risk, drift detection, LLM briefings | yfinance, Celery, factor models, grounded chat | 8000 |

Each agent runs as an independent **FastAPI + Streamlit** stack with its own Postgres/pgvector database.

---

## Shared Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Gemini (gemini-3.5-flash-lite) + OpenAI-compatible gateway  ──►  Prompt | LLM | Parser │
│         │                                    (JsonOutputParser)│
│         ▼                                                       │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Tool Loop  │───►│ PgPageIndex  │───►│  Postgres/pgvector│  │
│  │ (search_pages,│    │ (per-assess) │    │  (chunks + embed) │  │
│  │  get_page)   │    └──────────────┘    └──────────────────┘  │
│  └─────────────┘         │                      │              │
│         │                ▼                      ▼              │
│         │        ┌──────────────┐    ┌──────────────────┐    │
│         └───────►│ Deterministic│    │  Persistence     │    │
│                  │  Analytics   │    │  (SQLAlchemy)    │    │
│                  │ (ratios,     │    │  (audit trail,   │    │
│                  │  scorecard,  │    │   versioning)    │    │
│                  │  drift, etc) │    └──────────────────┘    │
│                  └──────────────┘                              │
└─────────────────────────────────────────────────────────────┘
```

**Key Principles**

- **Grounded LLM only** — model *reads* via tools, never hallucinates numbers
- **Deterministic core** — ratios, scorecards, drift math are pure Python (reproducible)
- **Versioned assessments** — rerun creates new version, original immutable
- **Audit trail** — every step logged with OpenTelemetry trace correlation
- **Graceful degradation** — Redis optional, free DuckDuckGo search, no paid APIs required

---

## Quick Start (Any Agent)

```bash
# 1) Pick an agent
cd Credit_Risk_Assesment_Agent        # or FinDoc_Intelligence_Agent, etc.

# 2) Install
pip install -r requirements.txt

# 3) Configure
cp .env.example .env
# Required: GEMINI_API_KEY (get at https://aistudio.google.com/apikey)
# Required: PG_HOST/PORT/USER/PASSWORD/DATABASE (Postgres + pgvector)
# Optional: REDIS_HOST/PORT/DB (live phase tracking)

# 4) Init DB
python -c "from <module>.db.models import init_schema; init_schema()"
# Credit Risk:      credit_risk.db.models
# FinDoc:           fin_doc_intel.db.session
# KYC:              onboarding_risk.db.session
# Investment:       portfolio_monitor.db.models

# 5a) Run API
uvicorn <module>.api.main:app --reload --port 8000

# 5b) Run Streamlit UI (separate terminal)
streamlit run app_streamlit/ui.py
```

| Agent | Module for `init_schema` | API Module |
|-------|--------------------------|------------|
| Credit Risk | `credit_risk.db.models` | `credit_risk.api.main` |
| FinDoc | `fin_doc_intel.db.session` | `fin_doc_intel.api.main` |
| KYC | `onboarding_risk.db.session` | `onboarding_risk.api.main` |
| Investment | `portfolio_monitor.db.models` | `portfolio_monitor.api.main` |

---

## Common Commands (via Makefile)

```bash
make help          # List targets
make install       # Install deps
make run           # Start FastAPI
make run-streamlit # Start Streamlit
make test          # pytest -v
make lint          # ruff check + format --check
make lint-fix      # Auto-fix
make init-db       # Create tables + pgvector extension
make build         # Docker image
make clean         # Remove caches
```

---

## Environment Variables (Shared Pattern)

| Variable | Purpose | Example |
|----------|---------|---------|
| `GEMINI_API_KEY` | **Required** — Gemini chat LLM (via plain `requests`, no SDK; see `gemini_requests.py`) | `AIza...` |
| `LLM_MODEL_NAME` | Chat model — Gemini id, or gateway model when `LLM_ENDPOINT` is set | `gemini-3.5-flash-lite` |
| `GEMINI_MODEL_NAME` | Gemini fallback model (used when `LLM_MODEL_NAME` is a gateway model) | `gemini-3.5-flash-lite` |
| `LLM_TEMPERATURE` | Determinism (0.0 = credit) | `0.0` |
| `LLM_MAX_TOKENS` | Max output | `4096` |
| `LLM_ENDPOINT` / `LLM_API_KEY` | Optional OpenAI-compatible gateway (tried before Gemini) | `https://your-gateway.example.com/` |
| `EMBED_MODEL_NAME` / `EMBED_LLM_API_KEY` | Embedding model via gateway (falls back to local HF) | `openai.text-embedding-3-large` |
| `HF_EMBED_MODEL` / `EMBED_DIM` | Local HuggingFace fallback (no key needed) | `sentence-transformers/all-MiniLM-L6-v2` / `384` |
| `PG_HOST/PORT/USER/PASSWORD/DATABASE` | Postgres + pgvector | `127.0.0.1/5433/postgres/.../credit_risk` |
| `REDIS_HOST/PORT/DB` | Optional live phase tracking | `127.0.0.1/6379/0` |

---

## Testing

```bash
# All agents use pytest; run from agent root:
pytest tests/ -v

# Skip conftest if DB deps not installed:
pytest tests/ -v --noconftest -p no:cacheprovider
```

---

## Docker (Per Agent)

```bash
cd <agent-dir>
docker build -t <agent>-api:latest .
docker run -p 8000:8000 --env-file .env <agent>-api:latest
```

For Investment Research, also run the Celery worker:

```bash
docker run --env-file .env investment-research-api:latest \
  celery -A portfolio_monitor.celery_app worker --loglevel=info
```

---

## Documentation

Each agent has deep-dive docs:

- `Credit_Risk_Assesment_Agent/CASE_STUDY.md` & `DOCUMENTATION.md`
- `FinDoc_Intelligence_Agent/CASE_STUDY.md` & `DOCUMENTATION.md`
- `HNI_Customer_Onboard_KYC_Agent/CASE_STUDY.md` & `DOCUMENTATION.md`
- `Investment_Research_Copilot/CASE_STUDY.md` & `DOCUMENTATION.md`

---

## License

MIT — see individual agent directories.
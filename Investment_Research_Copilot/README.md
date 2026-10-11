# Investment Research Copilot

**Portfolio monitoring & research assistant** — ingest holdings, fetch fundamentals, compute risk analytics, detect drift, generate grounded briefings.

---

## Overview

Designed for buy-side analysts and portfolio managers:

1. **Ingest** — Portfolio CSV / custodian feed → positions, cost basis, metadata
2. **Enrich** — yfinance / SEC filings → fundamentals, ratios, estimates
3. **Monitor** — Deterministic analytics: factor exposures, VaR, concentration, ESG flags
4. **Drift Detect** — Style/ factor / sector drift vs. mandate thresholds
5. **Brief** — Grounded LLM (Gemini `gemini-3.5-flash-lite` via plain `requests`, OpenAI-compatible gateway first) daily/weekly research briefings citing data
6. **Alert** — Threshold breaches → email / webhook / Streamlit inbox
7. **Chat** — Ask follow-up questions grounded in latest data (RAG over pgvector)

---

## Quick Start

```bash
cd Investment_Research_Copilot

# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in: GEMINI_API_KEY, LLM_ENDPOINT, PG_*, REDIS_*, CELERY_BROKER_URL

# Init DB
python -c "from portfolio_monitor.db.models import init_schema; init_schema()"

# Run API
uvicorn portfolio_monitor.api.main:app --reload --host 0.0.0.0 --port 8000

# Run Celery worker (background jobs: ingest, enrich, brief)
celery -A portfolio_monitor.celery_app worker --loglevel=info

# Run Streamlit UI
streamlit run app_streamlit/ui.py
```

---

## Project Structure

```
Investment_Research_Copilot/
├── portfolio_monitor/
│   ├── api/                    # FastAPI: portfolios, positions, alerts, briefings, chat
│   ├── db/                     # SQLAlchemy + pgvector: Portfolio, Position, FactorModel, Briefing
│   ├── monitor/                # Core analytics
│   │   ├── deterministic.py    # Factor risk, VaR, concentration, ESG
│   │   ├── llm_briefing.py     # Grounded briefing synthesis
│   │   └── __init__.py
│   ├── research/               # Deep-dive research graph
│   │   ├── collect.py          # SEC, earnings, news collection
│   │   ├── deterministic.py    # Valuation models, scenario analysis
│   │   ├── graph.py            # LangGraph research workflow
│   │   └── nodes.py
│   ├── osint/                  # Web sentiment (shared pattern)
│   ├── celery_app.py           # Background tasks (enrich, brief, alert)
│   ├── marketdata.py           # yfinance / fundamentals fetch
│   ├── allocation.py           # Portfolio construction helpers
│   ├── drift.py                # Style/factor/sector drift detection
│   ├── scoring.py              # Idea scoring (quality, value, momentum)
│   ├── config.py               # Gemini (requests) + embedding config
│   └── schemas.py              # Pydantic domain models
├── app_streamlit/ui.py         # Dashboard: holdings, risk, briefings, chat
├── sample_data/                # Example portfolio CSVs
├── tests/
├── requirements.txt
├── pyproject.toml
├── .env.example
├── Makefile
└── Dockerfile
```

---

## Key Commands

```bash
make help           # All targets
make install        # pip install -r requirements.txt
make run            # uvicorn portfolio_monitor.api.main:app --reload --port 8000
make run-worker     # celery -A portfolio_monitor.celery_app worker --loglevel=info
make run-streamlit  # streamlit run app_streamlit/ui.py
make test           # pytest tests/ -v
make lint           # ruff check . && ruff format --check .
make init-db        # python -c "from portfolio_monitor.db.models import init_schema; init_schema()"
make build          # docker build -t investment-research-api:latest .
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/portfolios` | Create portfolio (upload CSV or JSON) |
| `GET` | `/portfolios/{id}` | Holdings, analytics, latest briefing |
| `POST` | `/portfolios/{id}/enrich` | Trigger fundamentals refresh |
| `POST` | `/portfolios/{id}/brief` | Generate LLM briefing (async via Celery) |
| `GET` | `/briefings/{id}` | Briefing with citations |
| `POST` | `/chat` | Grounded Q&A over portfolio data |
| `GET` | `/alerts` | Active threshold breaches |
| `GET` | `/health` | Liveness |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Gemini API key (via plain `requests`, no SDK) |
| `LLM_MODEL_NAME` | Chat model — Gemini id, or gateway model when `LLM_ENDPOINT` is set (`gemini-3.5-flash-lite` fallback) |
| `LLM_ENDPOINT` | OpenAI-compatible base URL for embeddings |
| `EMBED_MODEL_NAME` | Embedding model |
| `PG_*` | Postgres + pgvector |
| `REDIS_HOST/PORT/DB` | Celery broker + result backend |
| `CELERY_BROKER_URL` | Redis/RabbitMQ URL (e.g. `redis://localhost:6379/0`) |

---

## Docker

```bash
docker build -t investment-research-api:latest .

# API
docker run -p 8000:8000 --env-file .env investment-research-api:latest

# Worker (separate container)
docker run --env-file .env investment-research-api:latest \
  celery -A portfolio_monitor.celery_app worker --loglevel=info
```

---

## Background Jobs (Celery)

| Task | Trigger | Description |
|------|---------|-------------|
| `enrich_portfolio` | POST `/portfolios/{id}/enrich` | Fetch fundamentals, compute ratios |
| `generate_briefing` | POST `/portfolios/{id}/brief` | LLM briefing with citations |
| `check_drift` | Daily cron | Factor/style drift vs. mandate |
| `emit_alerts` | On threshold breach | Email/webhook/Streamlit inbox |

---

## Related Docs

- `CASE_STUDY.md` — Product spec
- `DOCUMENTATION.md` — Architecture, factor models, briefing pipeline
- `NEW_CASE_STUDY.md` — Updated spec

---

## License

MIT
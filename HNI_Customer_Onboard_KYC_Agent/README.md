# HNI Customer Onboarding & KYC Agent

**High-Net-Worth Individual onboarding** — document ingestion, beneficial-ownership graph, sanctions/PEP screening, risk scoring, and EDD memo.

---

## Overview

End-to-end KYC/onboarding for private-bank / wealth-management clients:

1. **Ingest** — PDFs (passport, proof of address, source-of-wealth docs, corporate structures)
2. **Extract** — Grounded LLM → entities, ownership edges, IDs, addresses, SOF narrative
3. **Graph** — Build beneficial-ownership DAG (natural persons → legal entities → UBOS)
4. **Screen** — Sanctions lists, PEP databases, adverse media (DuckDuckGo + LLM synthesis)
5. **Score** — Deterministic risk scorecard (jurisdiction, structure opacity, SOF plausibility, screening hits)
6. **EDD** — Enhanced Due Diligence memo for elevated-risk cases
7. **Review** — Case-officer queue: approve / request-info / decline with audit trail

---

## Quick Start

```bash
cd HNI_Customer_Onboard_KYC_Agent

# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in: GEMINI_API_KEY, LLM_ENDPOINT, PG_*, REDIS_*, screening API keys (optional)

# Init DB
python -c "from onboarding_risk.db.session import init_db; init_db()"

# Run API
uvicorn onboarding_risk.api.main:app --reload --host 0.0.0.0 --port 8000

# Run Streamlit UI
streamlit run app_streamlit/ui.py
```

---

## Project Structure

```
HNI_Customer_Onboard_KYC_Agent/
├── onboarding_risk/
│   ├── api/                    # FastAPI: cases, screening, graph, review
│   ├── db/                     # SQLAlchemy: Case, Subject, OwnershipEdge, ScreeningHit, Review
│   ├── osint/                  # Sanctions/PEP + adverse-media (DuckDuckGo)
│   ├── extraction.py           # Grounded LLM: entities, ownership, SOF
│   ├── ownership.py            # Beneficial-ownership graph build & traversal
│   ├── screening.py            # List matching, fuzzy name matching
│   ├── scoring.py              # KYC risk scorecard (deterministic)
│   ├── edd.py                  # EDD memo synthesis
│   ├── config.py               # Gemini (requests) + embedding config
│   ├── schemas.py              # Pydantic: Subject, OwnershipEdge, ScreeningResult, KYCScore, …
│   └── tools.py                # LangChain tools for document search
├── app_streamlit/
│   ├── ui.py                   # Main onboarding dashboard
│   └── ui_subject.py           # Subject-detail drill-down
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
make help          # All targets
make install       # pip install -r requirements.txt
make run           # uvicorn onboarding_risk.api.main:app --reload --port 8000
make run-streamlit # streamlit run app_streamlit/ui.py
make test          # pytest tests/ -v
make lint          # ruff check . && ruff format --check .
make init-db       # python -c "from onboarding_risk.db.session import init_db; init_db()"
make build         # docker build -t fraud-detection-api:latest .
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/cases` | Create onboarding case (upload docs) |
| `GET` | `/cases/{id}` | Full case: subjects, graph, screening, score, EDD |
| `POST` | `/cases/{id}/screen` | Re-run sanctions/PEP/adverse-media |
| `POST` | `/cases/{id}/review` | Case-officer decision: approve / info-request / decline |
| `GET` | `/subjects/{id}/graph` | Beneficial-ownership DAG (JSON for vis) |
| `GET` | `/health` | Liveness |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Gemini API key (via plain `requests`, no SDK) |
| `LLM_MODEL_NAME` | Chat model — Gemini id, or gateway model when `LLM_ENDPOINT` is set (`gemini-3.5-flash-lite` fallback) |
| `LLM_ENDPOINT` | OpenAI-compatible base URL for embeddings |
| `EMBED_MODEL_NAME` | Embedding model |
| `PG_*` | Postgres connection |
| `REDIS_*` | Optional Redis |
| `SCREENING_API_KEY` | Optional: commercial sanctions/PEP feed |

---

## Docker

```bash
docker build -t kyc-api:latest .
docker run -p 8000:8000 --env-file .env kyc-api:latest
```

---

## Related Docs

- `CASE_STUDY.md` — Product spec
- `DOCUMENTATION.md` — Architecture, graph model, screening logic
- `NEW_CASE_STUDY.md` — Updated spec

---

## License

MIT
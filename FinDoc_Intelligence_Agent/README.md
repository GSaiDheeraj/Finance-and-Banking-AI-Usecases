# FinDoc Intelligence Agent

**Financial document ingestion, extraction, and structured fundamentals pipeline** — PDF → semantic chunks → grounded LLM extraction → normalized ratios → persistent pgvector index.

---

## Overview

This agent focuses on the **document intelligence** layer shared by downstream credit, onboarding, and research workflows:

1. **Ingest** — Text PDFs → per-page chunks (PyMuPDF)
2. **Embed** — OpenAI-compatible embeddings → pgvector index (per-assessment scope)
3. **Extract** — Grounded LLM (Gemini `gemini-3.5-flash-lite` via plain `requests`, OpenAI-compatible gateway first) + tool loop: model searches pages, cites sources, outputs structured `LineItem`s
4. **Normalize** — Unit-aware (`thousands`/`millions`/`absolute`), period ordering, standard-label mapping
5. **Ratios** — Deterministic credit ratios from extracted line items
6. **Persist** — Chunks, embeddings, line items, ratios in Postgres/pgvector for audit & RAG

---

## Quick Start

```bash
cd FinDoc_Intelligence_Agent

# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in: GEMINI_API_KEY, LLM_ENDPOINT, EMBED_MODEL_NAME, PG_*, REDIS_* (optional)

# Init DB (Postgres + pgvector)
python -c "from fin_doc_intel.db.session import init_db; init_db()"

# Run API
uvicorn fin_doc_intel.api.main:app --reload --host 0.0.0.0 --port 8000

# Run Streamlit UI (separate terminal)
streamlit run app_streamlit/ui.py
```

---

## Project Structure

```
FinDoc_Intelligence_Agent/
├── fin_doc_intel/
│   ├── api/                    # FastAPI: upload, extract, search, ratios
│   ├── db/                     # SQLAlchemy + pgvector (chunks, embeddings)
│   ├── input_pipeline/         # PDF classification, extraction, table post-processing
│   ├── fundamentals_pipeline/  # Graph-based ratio extraction + validation
│   ├── extraction.py           # Grounded LLM extraction (shared logic)
│   ├── config.py               # Gemini (requests) + embedding config
│   ├── schemas.py              # Pydantic: LineItem, FinancialRatios, PageChunk, …
│   └── tools.py                # LangChain search_pages/get_page tools
├── app_streamlit/ui.py         # Streamlit: upload → extract → review ratios
├── tests/
├── requirements.txt
├── pyproject.toml
├── .env.example
├── Makefile                    # make run, run-streamlit, test, lint, init-db
└── Dockerfile
```

---

## Key Commands

```bash
make help          # Show all targets
make install       # pip install -r requirements.txt
make run           # uvicorn fin_doc_intel.api.main:app --reload --port 8000
make run-streamlit # streamlit run app_streamlit/ui.py
make test          # pytest tests/ -v
make lint          # ruff check . && ruff format --check .
make init-db       # python -c "from fin_doc_intel.db.session import init_db; init_db()"
make build         # docker build -t findoc-api:latest .
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/documents/upload` | Upload PDF(s), returns `document_id`s |
| `POST` | `/extractions` | Run grounded extraction on uploaded docs |
| `GET` | `/extractions/{id}` | Structured line items + ratios |
| `POST` | `/search` | Semantic search over pgvector chunks |
| `GET` | `/health` | Liveness |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Gemini API key for chat LLM (via plain `requests`, no SDK) |
| `LLM_MODEL_NAME` | Chat model — Gemini id, or gateway model when `LLM_ENDPOINT` is set (default `gemini-3.5-flash-lite` fallback) |
| `LLM_ENDPOINT` | OpenAI-compatible base URL for embeddings |
| `EMBED_MODEL_NAME` | Embedding model (e.g. `openai.text-embedding-3-small`) |
| `PG_HOST/PORT/USER/PASSWORD/DATABASE` | Postgres + pgvector |
| `REDIS_HOST/PORT/DB` | Optional Redis for phase tracking |

---

## Docker

```bash
docker build -t findoc-api:latest .
docker run -p 8000:8000 --env-file .env findoc-api:latest
```

---

## Related Docs

- `CASE_STUDY.md` — Product spec
- `DOCUMENTATION.md` — Architecture, pipeline stages
- `NEW_CASE_STUDY.md` — Updated spec

---

## License

MIT
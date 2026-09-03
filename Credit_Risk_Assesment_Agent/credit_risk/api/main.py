"""
FastAPI entry point.

Run locally with:
    uvicorn credit_risk.api.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..db.models import init_schema
from ..logconf import get_logger
from ..tracing import setup_tracing
from .exceptions import AssessmentNotFoundError, AssessmentNotReadyError
from .routes import router

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"

_log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()  # CREATE EXTENSION vector + create_all — idempotent, no Alembic (see plan)
    _log.info("credit_risk API: schema ready")
    yield


app = FastAPI(
    title="Credit Risk Assessment API",
    description="CASE_STUDY.md §7.2's assessment operations over the credit_risk pipeline.",
    lifespan=lifespan,
)

# Instrumented before any other middleware so the resulting span wraps the full request
# (CORS handling included), not just what's left after other middleware has run first.
setup_tracing(app)

# Local Streamlit UI runs on a different port; this is a local dev tool, not a public
# service, so a permissive local origin list is appropriate (contrast with NFR-5's
# VPC-/on-prem-first production stance, which would pin this to the real UI's origin).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(AssessmentNotFoundError)
async def handle_not_found(request: Request, exc: AssessmentNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AssessmentNotReadyError)
async def handle_not_ready(request: Request, exc: AssessmentNotReadyError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # The pipeline's real failure (LLM gateway error, malformed PDF, ...) is logged in
    # full server-side; the client gets a generic message rather than an internal
    # traceback (per clean-code's logging guidance: don't leak internals to the caller).
    _log.error("unhandled error on %s %s: %s: %s",
               request.method, request.url.path, type(exc).__name__, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal error processing the request."})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Registered last, deliberately: Starlette matches routes in registration order, so the
# specific API routes and /health above are always matched first — this mount only
# catches whatever's left (/, /css/*, /js/*), serving the vanilla-JS UI in `web/` as the
# same origin as the API itself (no CORS needed for that UI to call it). `html=True`
# makes `/` resolve to `web/index.html`.
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
else:
    _log.warning("web/ directory not found at %s — the static UI will not be served", _WEB_DIR)

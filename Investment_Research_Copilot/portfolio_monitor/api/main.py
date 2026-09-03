"""
FastAPI entry point for Investment Research Copilot.

Run locally:
    uvicorn portfolio_monitor.api.main:app --host 0.0.0.0 --port 8000 --reload
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
from .exceptions import PortfolioNotFoundError, PortfolioNotReadyError
from .routes import router

_WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
_log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    _log.info("portfolio_monitor API: schema ready")
    yield


app = FastAPI(
    title="Investment Research Copilot API",
    description="Portfolio monitoring and investment research — CASE_STUDY.md §7.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.exception_handler(PortfolioNotFoundError)
async def handle_not_found(request: Request, exc: PortfolioNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(PortfolioNotReadyError)
async def handle_not_ready(request: Request, exc: PortfolioNotReadyError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    _log.error(
        "unhandled error on %s %s: %s: %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal error processing the request."})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
else:
    _log.warning("web/ directory not found at %s — static UI will not be served", _WEB_DIR)

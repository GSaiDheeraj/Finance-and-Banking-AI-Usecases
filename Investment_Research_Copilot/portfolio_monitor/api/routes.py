"""
Portfolio monitoring REST operations — start, status, result, rerun, question, review, audit.

Start/rerun return immediately (202); the pipeline runs via BackgroundTasks or Celery.
Clients poll GET /portfolios/{id}/status, then GET /portfolios/{id} for the full result.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import PortfolioRun, ReviewRow
from ..db.persist import persist_documents
from ..db.session import get_session
from ..jobs import (
    celery_enabled,
    dispatch_construction_job,
    dispatch_monitoring_job,
    execute_construction_job,
    execute_monitoring_job,
)
from ..logconf import get_logger
from ..monitor.llm_briefing import answer_question as answer_monitoring_question
from ..schemas import ConstructionRequest, MonitoringResult
from ..status_tracker import get_phase, set_phase
from .exceptions import PortfolioNotFoundError, PortfolioNotReadyError
from .schemas import (
    AuditTraceResponse,
    PortfolioResponse,
    PortfolioSummary,
    QuestionRequest,
    QuestionResponse,
    RerunRequest,
    ReviewRequest,
    ReviewResponse,
    StatusResponse,
)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])
_log = get_logger()
_TERMINAL_REVIEW_STATUS = {"approved": "approved", "rejected": "rejected"}


def _get_run_or_404(session: Session, portfolio_id: str) -> PortfolioRun:
    try:
        uuid.UUID(portfolio_id)
    except ValueError:
        raise PortfolioNotFoundError(portfolio_id) from None
    run = session.get(PortfolioRun, portfolio_id)
    if run is None:
        raise PortfolioNotFoundError(portfolio_id)
    return run


def _save_uploads(files: list[UploadFile], doc_type: str = "holdings") -> list[str]:
    if not files:
        return []
    base = Path(os.getenv("DATA_STORAGE_DIR", "./data/uploads"))
    batch_dir = base / str(uuid.uuid4())
    batch_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for upload in files:
        dest = batch_dir / upload.filename
        with dest.open("wb") as f:
            f.write(upload.file.read())
        paths.append(str(dest))
    return paths


def _to_summary(run: PortfolioRun) -> PortfolioSummary:
    return PortfolioSummary(
        portfolio_id=run.id,
        parent_run_id=run.parent_run_id,
        status=run.status,
        result_type=run.result_type,
        portfolio_name=run.portfolio_name,
        client_name=run.client_name,
        mandate=run.mandate,
        health_score=run.health_score,
        health_band=run.health_band,
        requested_at=run.requested_at,
        completed_at=run.completed_at,
    )


def _to_response(run: PortfolioRun) -> PortfolioResponse:
    return PortfolioResponse(
        **_to_summary(run).model_dump(),
        base_currency=run.base_currency,
        mode=run.mode,
        enable_news=run.enable_news,
        question=run.question,
        answer=run.answer,
        instruction=run.instruction,
        construction_params=run.construction_params,
        latency_ms=run.latency_ms,
        llm_call_count=run.llm_call_count,
        model_name=run.model_name,
        result=run.raw_result,
        reasoning=run.reasoning or [],
        requested_by=run.requested_by,
        documents=[d.filename for d in run.documents],
    )


def _schedule_job(
    background_tasks: BackgroundTasks,
    run_id: str,
    holdings_path: str,
    transactions_path: str,
    statement_path: str,
    construction_params: Optional[dict],
    mandate: str,
    base_currency: str,
    mode: str,
    enable_news: bool,
    question: str,
) -> None:
    kwargs = dict(
        run_id=run_id,
        holdings_path=holdings_path,
        transactions_path=transactions_path,
        statement_path=statement_path,
        construction_params=construction_params,
        mandate=mandate,
        base_currency=base_currency,
        mode=mode,
        enable_news=enable_news,
        question=question,
    )
    if celery_enabled():
        dispatch_monitoring_job(**kwargs)
    else:
        background_tasks.add_task(execute_monitoring_job, **kwargs)


def _schedule_construction_job(
    background_tasks: BackgroundTasks,
    run_id: str,
    construction_params: dict,
    mandate: str,
    base_currency: str,
    mode: str,
    enable_news: bool,
) -> None:
    kwargs = dict(
        run_id=run_id,
        construction_params=construction_params,
        mandate=mandate,
        base_currency=base_currency,
        mode=mode,
        enable_news=enable_news,
    )
    if celery_enabled():
        dispatch_construction_job(**kwargs)
    else:
        background_tasks.add_task(execute_construction_job, **kwargs)


@router.get("", response_model=list[PortfolioSummary])
def list_portfolios(session: Session = Depends(get_session)) -> list[PortfolioSummary]:
    stmt = select(PortfolioRun).order_by(PortfolioRun.requested_at.desc())
    return [_to_summary(r) for r in session.scalars(stmt).all()]


@router.post("", response_model=PortfolioResponse, status_code=202)
def create_portfolio(
    background_tasks: BackgroundTasks,
    holdings_files: list[UploadFile] = File(default=[]),
    transactions_file: UploadFile | None = File(default=None),
    statement_file: UploadFile | None = File(default=None),
    construction_params: str | None = Form(
        default=None,
        description="JSON-encoded ConstructionRequest — build a portfolio from scratch "
                    "(mandate, invest_amount, base_currency, geography_mode, countries, "
                    "style, etc.) when no holdings/statement is uploaded.",
    ),
    construct_only: bool = Form(
        default=False,
        description="When true (with construction_params, no holdings/statement): run "
                    "construction directly and keep its full result (rationale, pros/cons, "
                    "feasibility, country research) instead of flattening it into a monitored "
                    "book, which discards all of that.",
    ),
    portfolio_name: str = Form("Portfolio"),
    client_name: str = Form(""),
    mandate: str = Form("balanced"),
    base_currency: str = Form("USD"),
    mode: str = Form("both"),
    enable_news: bool = Form(True),
    question: str = Form(""),
    requested_by: str = Form("local-advisor"),
    session: Session = Depends(get_session),
) -> PortfolioResponse:
    holdings_paths = _save_uploads(holdings_files, "holdings")
    tx_paths = _save_uploads([transactions_file], "transactions") if transactions_file else []
    stmt_paths = _save_uploads([statement_file], "statement") if statement_file else []

    parsed_construction: Optional[ConstructionRequest] = None
    if construction_params:
        try:
            parsed_construction = ConstructionRequest.model_validate_json(construction_params)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid construction_params: {exc}") from exc

    if not holdings_paths and not stmt_paths and parsed_construction is None:
        raise HTTPException(
            status_code=400,
            detail="Upload a holdings CSV/JSON or a PDF brokerage statement, or supply "
                   "construction_params to build a portfolio from scratch.",
        )
    if construct_only and parsed_construction is None:
        raise HTTPException(
            status_code=400,
            detail="construct_only requires construction_params.",
        )
    if construct_only and (holdings_paths or stmt_paths):
        raise HTTPException(
            status_code=400,
            detail="construct_only builds from scratch — don't also upload holdings/a statement.",
        )

    # An explicit construction_params is authoritative for mandate/currency — it's what the
    # book actually gets built to, so the persisted columns must match it rather than
    # whatever the separate mandate/base_currency form fields happened to say.
    if parsed_construction is not None:
        mandate = parsed_construction.mandate
        base_currency = parsed_construction.base_currency

    run = PortfolioRun(
        status="created",
        result_type="construction" if construct_only else "monitoring",
        portfolio_name=portfolio_name,
        client_name=client_name or None,
        mandate=mandate,
        base_currency=base_currency,
        mode=mode,
        enable_news=enable_news,
        question=(question or None) if not construct_only else None,
        requested_by=requested_by,
        construction_params=(
            parsed_construction.model_dump(mode="json") if parsed_construction else None
        ),
    )
    session.add(run)
    session.flush()
    all_paths = holdings_paths + tx_paths + stmt_paths
    persist_documents(session, run.id, all_paths)
    session.commit()

    set_phase(run.id, "queued")
    if construct_only:
        _schedule_construction_job(
            background_tasks, run.id, run.construction_params, mandate, base_currency,
            mode, enable_news,
        )
    else:
        _schedule_job(
            background_tasks,
            run.id,
            holdings_paths[0] if holdings_paths else "",
            tx_paths[0] if tx_paths else "",
            stmt_paths[0] if stmt_paths else "",
            run.construction_params,
            mandate,
            base_currency,
            mode,
            enable_news,
            question,
        )
    return _to_response(run)


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
def get_portfolio(portfolio_id: str, session: Session = Depends(get_session)) -> PortfolioResponse:
    return _to_response(_get_run_or_404(session, portfolio_id))


@router.get("/{portfolio_id}/status", response_model=StatusResponse)
def get_portfolio_status(portfolio_id: str, session: Session = Depends(get_session)) -> StatusResponse:
    live = get_phase(portfolio_id)
    if live is not None and live["phase"] not in ("done", "failed"):
        return StatusResponse(
            portfolio_id=portfolio_id,
            status="processing",
            phase=live["phase"],
            phase_detail=live.get("detail"),
        )
    run = _get_run_or_404(session, portfolio_id)
    return StatusResponse(
        portfolio_id=run.id,
        status=run.status,
        phase=live["phase"] if live else None,
        phase_detail=live.get("detail") if live else None,
    )


@router.post("/{portfolio_id}/rerun", response_model=PortfolioResponse, status_code=202)
def rerun_portfolio(
    portfolio_id: str,
    body: RerunRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> PortfolioResponse:
    parent = _get_run_or_404(session, portfolio_id)
    holdings_doc = next((d for d in parent.documents if d.doc_type == "holdings"), None)
    tx_doc = next((d for d in parent.documents if d.doc_type == "transactions"), None)
    stmt_doc = next((d for d in parent.documents if d.doc_type == "statement"), None)

    effective_mandate = body.mandate or parent.mandate
    # An auto-constructed parent has no holdings/statement document to re-read — carry its
    # construction_params forward instead (with the effective mandate applied), so a rerun
    # re-constructs rather than hitting the same "no holdings" error this feature exists to
    # avoid.
    construction_params = None
    if holdings_doc is None and stmt_doc is None and parent.construction_params:
        construction_params = {**parent.construction_params, "mandate": effective_mandate}

    new_run = PortfolioRun(
        parent_run_id=parent.id,
        status="created",
        result_type=parent.result_type,
        portfolio_name=parent.portfolio_name,
        client_name=parent.client_name,
        mandate=effective_mandate,
        base_currency=parent.base_currency,
        mode=parent.mode,
        enable_news=body.enable_news if body.enable_news is not None else parent.enable_news,
        question=(body.question or parent.question) if parent.result_type != "construction" else None,
        instruction=body.instruction,
        requested_by=parent.requested_by,
        construction_params=construction_params,
    )
    session.add(new_run)
    session.flush()
    for doc in parent.documents:
        persist_documents(session, new_run.id, [doc.storage_uri], doc.doc_type)
    session.commit()

    set_phase(new_run.id, "queued")
    if parent.result_type == "construction":
        _schedule_construction_job(
            background_tasks, new_run.id, new_run.construction_params, new_run.mandate,
            new_run.base_currency, new_run.mode, new_run.enable_news,
        )
    else:
        _schedule_job(
            background_tasks,
            new_run.id,
            holdings_doc.storage_uri if holdings_doc else "",
            tx_doc.storage_uri if tx_doc else "",
            stmt_doc.storage_uri if stmt_doc else "",
            new_run.construction_params,
            new_run.mandate,
            new_run.base_currency,
            new_run.mode,
            new_run.enable_news,
            new_run.question or "",
        )
    return _to_response(new_run)


@router.post("/{portfolio_id}/question", response_model=QuestionResponse)
def ask_question(
    portfolio_id: str, body: QuestionRequest, session: Session = Depends(get_session)
) -> QuestionResponse:
    run = _get_run_or_404(session, portfolio_id)
    if run.result_type == "construction":
        raise HTTPException(
            status_code=409,
            detail="Q&A isn't available for construction-only runs yet.",
        )
    if run.raw_result is None:
        raise PortfolioNotReadyError(portfolio_id, run.status)
    result = MonitoringResult.model_validate(run.raw_result)
    answer = answer_monitoring_question(result, body.question)
    run.answer = answer
    session.commit()
    return QuestionResponse(answer=answer)


@router.post("/{portfolio_id}/review", response_model=ReviewResponse)
def submit_review(
    portfolio_id: str, body: ReviewRequest, session: Session = Depends(get_session)
) -> ReviewResponse:
    run = _get_run_or_404(session, portfolio_id)
    review = ReviewRow(
        portfolio_run_id=run.id,
        reviewer=body.reviewer,
        action=body.action,
        corrected_fields=body.corrected_fields,
        reason=body.reason,
    )
    session.add(review)
    if body.action in _TERMINAL_REVIEW_STATUS:
        run.status = _TERMINAL_REVIEW_STATUS[body.action]
    session.commit()
    return ReviewResponse(
        id=review.id,
        reviewer=review.reviewer,
        action=review.action,
        reason=review.reason,
        reviewed_at=review.reviewed_at,
    )


@router.get("/{portfolio_id}/audit", response_model=AuditTraceResponse)
def get_audit_trace(portfolio_id: str, session: Session = Depends(get_session)) -> AuditTraceResponse:
    run = _get_run_or_404(session, portfolio_id)
    return AuditTraceResponse(
        portfolio_id=run.id,
        parent_run_id=run.parent_run_id,
        status=run.status,
        model_name=run.model_name,
        requested_by=run.requested_by,
        requested_at=run.requested_at,
        completed_at=run.completed_at,
        documents=[
            {"filename": d.filename, "sha256_hash": d.sha256_hash, "doc_type": d.doc_type}
            for d in run.documents
        ],
        holdings=[
            {"symbol": h.symbol, "name": h.name, "quantity": h.quantity, "market_value": h.market_value}
            for h in run.holdings
        ],
        alerts=[
            {
                "alert_type": a.alert_type,
                "severity": a.severity,
                "title": a.title,
                "message": a.message,
            }
            for a in run.alerts
        ],
        reviews=[
            ReviewResponse(
                id=r.id, reviewer=r.reviewer, action=r.action, reason=r.reason, reviewed_at=r.reviewed_at
            )
            for r in run.reviews
        ],
    )

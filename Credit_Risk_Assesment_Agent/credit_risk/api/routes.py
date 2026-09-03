"""
The six assessment operations from CASE_STUDY.md §7.2 (start, status/result, rerun,
question, review, audit), plus a list endpoint the analyst workspace needs to show a
case list — CASE_STUDY.md doesn't name that as a separate operation, but a workspace
with no way to see past assessments isn't a workspace.

Start/rerun are async: `assess_pack` genuinely takes minutes (multiple sequential/
parallel LLM + embedding calls). Holding one HTTP connection open for that long is
exactly what browser idle timeouts, and any reverse proxy/load balancer in front of this
service (nginx, an ALB, Cloudflare — all default well under a minute), are built to kill
— the backend would keep working, but the client would never receive the version_id it
needs to check on it. So `POST /assessments` and `POST /assessments/{id}/rerun` do only
the fast part synchronously (save uploads, create the version row, commit) and return in
under a second; the actual pipeline runs via FastAPI's `BackgroundTasks` — still one
process, no broker — and the client polls `GET /assessments/{id}/status` (backed by
Redis for a fast, DB-free read; see `status_tracker.py`) until it's done, then
`GET /assessments/{id}` for the full result. `/question` stays synchronous — it's one
LLM call, a few seconds, not worth a poll loop.

The one honest gap: `BackgroundTasks` runs in-process, so a job survives neither a
uvicorn crash nor a restart (no durable retry) — the trade-off of not running a real
queue (Celery+Redis was considered and deliberately deferred; Redis here is used only
for cheap ephemeral progress, not as a job broker).

Route handlers are plain `def`, not `async def`: `assess_pack`'s LangChain/`yfinance`/
`ddgs` calls are all synchronous, blocking I/O. FastAPI (via Starlette) runs a sync `def`
route in its worker thread pool automatically, so a blocking call here doesn't block the
event loop the way it would inside an `async def` — this is what lets the whole service
stay synchronous end-to-end (matching `DOCUMENTATION.md`'s HLD) without introducing
`asyncio` anywhere in `credit_risk/`. The background task function passed to
`BackgroundTasks.add_task` is also a plain function for the same reason.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent_graph import answer_question, run_credit_assessment
from ..db.models import AssessmentVersion, ReviewRow
from ..db.session import get_session
from ..logconf import get_logger
from ..schemas import CreditAssessment
from ..status_tracker import get_phase, set_phase
from .exceptions import AssessmentNotFoundError, AssessmentNotReadyError
from .schemas import (
    AssessmentResponse,
    AssessmentSummary,
    AuditTraceResponse,
    DocumentAudit,
    LineItemAudit,
    QuestionRequest,
    QuestionResponse,
    RatingFactorAudit,
    RerunRequest,
    ReviewRequest,
    ReviewResponse,
    StatusResponse,
)

router = APIRouter(prefix="/assessments", tags=["assessments"])
_log = get_logger()

_TERMINAL_REVIEW_STATUS = {"approved": "approved", "rejected": "rejected"}


def _get_version_or_404(session: Session, version_id: str) -> AssessmentVersion:
    try:
        uuid.UUID(version_id)
    except ValueError:
        # Not a syntactically valid UUID, so it can never exist — treat it as "not
        # found" rather than letting Postgres reject the type cast with a raw DB error.
        raise AssessmentNotFoundError(version_id) from None
    version = session.get(AssessmentVersion, version_id)
    if version is None:
        raise AssessmentNotFoundError(version_id)
    return version


def _save_uploads(files: list[UploadFile]) -> list[str]:
    """Persist uploads to a real, version-scoped directory — never a bare temp file the
    old Streamlit UI wrote via `tempfile.NamedTemporaryFile(delete=False, ...)` and left
    on disk forever. One batch directory per request; `assess_pack` reads from here."""
    if not files:
        return []
    base = Path(os.getenv("DOCUMENT_STORAGE_DIR", "./data/documents"))
    batch_dir = base / str(uuid.uuid4())
    batch_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for upload in files:
        dest = batch_dir / upload.filename
        with dest.open("wb") as f:
            f.write(upload.file.read())
        paths.append(str(dest))
    return paths


def _to_summary(version: AssessmentVersion) -> AssessmentSummary:
    return AssessmentSummary(
        version_id=version.id,
        parent_version_id=version.parent_version_id,
        status=version.status,
        company_name=version.company_name,
        ticker=version.ticker,
        grade=version.grade,
        band=version.band,
        decision=version.decision,
        requested_at=version.requested_at,
        completed_at=version.completed_at,
    )


def _to_response(version: AssessmentVersion) -> AssessmentResponse:
    return AssessmentResponse(
        **_to_summary(version).model_dump(),
        question=version.question,
        answer=version.answer,
        instruction=version.instruction,
        probability_of_default=version.probability_of_default,
        loss_given_default=version.loss_given_default,
        expected_loss_pct=version.expected_loss_pct,
        hard_stop_reason=version.hard_stop_reason,
        latency_ms=version.latency_ms,
        llm_call_count=version.llm_call_count,
        model_name=version.model_name,
        embed_model_name=version.embed_model_name,
        assessment=version.raw_result,
        reasoning=version.reasoning or [],
        requested_by=version.requested_by,
        enable_web_sentiment=version.enable_web_sentiment,
        search_region=version.search_region,
        documents=[d.filename for d in version.documents],
    )


@router.get("", response_model=list[AssessmentSummary])
def list_assessments(session: Session = Depends(get_session)) -> list[AssessmentSummary]:
    """The analyst workspace's case list, newest first."""
    stmt = select(AssessmentVersion).order_by(AssessmentVersion.requested_at.desc())
    versions = session.scalars(stmt).all()
    return [_to_summary(v) for v in versions]


def _run_assessment_job(
    version_id: str,
    pdf_paths: list[str],
    question: str,
    company_name: str,
    ticker: str,
    search_region: Optional[str],
    enable_web_sentiment: bool,
) -> None:
    """The actual pipeline, run by `BackgroundTasks` after the route has already
    responded. Opens its own session — the request's session is gone by the time this
    runs — and commits/rolls back itself, since nothing else will."""
    session = next(get_session())
    try:
        version = session.get(AssessmentVersion, version_id)
        if version is None:
            _log.error("background assessment job: version %s vanished before it could run", version_id)
            return
        run_credit_assessment(
            pdf_paths, question=question, company_name=company_name, ticker=ticker,
            search_region=search_region, enable_web_sentiment=enable_web_sentiment,
            session=session, version=version,
        )
        session.commit()
    except Exception:
        # run_credit_assessment already set version.status = "validation_review" on the
        # in-memory object before re-raising — commit that here rather than rolling it
        # back, so a poller sees the failure instead of a permanently "processing" case.
        _log.error("background assessment job failed for version=%s", version_id, exc_info=True)
        session.commit()
    finally:
        session.close()


@router.post("", response_model=AssessmentResponse, status_code=202)
def create_assessment(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(default=[]),
    ticker: str = Form(""),
    company_name: str = Form(""),
    question: str = Form(""),
    search_region: str | None = Form(None),
    enable_web_sentiment: bool = Form(True),
    requested_by: str = Form("local-analyst"),
    session: Session = Depends(get_session),
) -> AssessmentResponse:
    """Start a new assessment. Returns immediately (202) with a `version_id` and
    `status="created"` — the pipeline runs in the background; see the module docstring
    for why. Poll `GET /assessments/{id}/status`, then `GET /assessments/{id}` once done."""
    pdf_paths = _save_uploads(files)
    version = AssessmentVersion(
        status="created", company_name=company_name or None, ticker=ticker.strip() or None,
        question=question or None, enable_web_sentiment=enable_web_sentiment,
        search_region=search_region, requested_by=requested_by,
    )
    session.add(version)
    session.commit()
    set_phase(version.id, "queued")
    background_tasks.add_task(
        _run_assessment_job, version.id, pdf_paths, question, company_name, ticker,
        search_region, enable_web_sentiment,
    )
    return _to_response(version)


@router.get("/{version_id}", response_model=AssessmentResponse)
def get_assessment(version_id: str, session: Session = Depends(get_session)) -> AssessmentResponse:
    return _to_response(_get_version_or_404(session, version_id))


@router.get("/{version_id}/status", response_model=StatusResponse)
def get_assessment_status(version_id: str, session: Session = Depends(get_session)) -> StatusResponse:
    """Cheap poll target while a job is in flight — reads Redis first (no DB query at
    all in the common case); falls back to the durable Postgres `status` column if Redis
    has nothing (expired, never written, unreachable) OR if the live phase has itself
    reached a terminal state ("done"/"failed") rather than reporting a fixed "processing"
    forever. `set_phase(..., "done"/"failed")` runs slightly before the pipeline's own
    final `commit()`, so there's a brief window where Redis already says done but
    Postgres hasn't landed the real status yet — falling through to Postgres here means a
    client sees one more "still whatever it was before" poll in that window rather than a
    `status` that never actually matches a fetchable result, which self-corrects on the
    very next poll rather than being a real bug."""
    live = get_phase(version_id)
    if live is not None and live["phase"] not in ("done", "failed"):
        return StatusResponse(
            version_id=version_id, status="processing",
            phase=live["phase"], phase_detail=live.get("detail"),
        )
    version = _get_version_or_404(session, version_id)
    return StatusResponse(
        version_id=version.id, status=version.status,
        phase=live["phase"] if live else None,
        phase_detail=live.get("detail") if live else None,
    )


@router.post("/{version_id}/rerun", response_model=AssessmentResponse, status_code=202)
def rerun_assessment(
    version_id: str, body: RerunRequest, background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> AssessmentResponse:
    """Rerun the parent's own documents with a correction/new instruction. Creates a new
    version — CASE_STUDY.md §7.1: a rerun is new evidence layered onto the case, never
    permission to discard what came before, so the parent row is untouched. Same
    background-task treatment as `create_assessment` and for the same reason: this runs
    the identical multi-minute pipeline."""
    parent = _get_version_or_404(session, version_id)
    pdf_paths = [doc.storage_uri for doc in parent.documents]
    question = body.question or parent.question or ""
    enable_web_sentiment = (
        body.enable_web_sentiment if body.enable_web_sentiment is not None else parent.enable_web_sentiment
    )
    search_region = body.search_region or parent.search_region
    new_version = AssessmentVersion(
        parent_version_id=parent.id, status="created",
        company_name=parent.company_name, ticker=parent.ticker,
        question=question or None, instruction=body.instruction,
        enable_web_sentiment=enable_web_sentiment, search_region=search_region,
        requested_by=parent.requested_by,
    )
    session.add(new_version)
    session.commit()
    set_phase(new_version.id, "queued")
    background_tasks.add_task(
        _run_assessment_job, new_version.id, pdf_paths, question,
        parent.company_name or "", parent.ticker or "", search_region, enable_web_sentiment,
    )
    return _to_response(new_version)


@router.post("/{version_id}/question", response_model=QuestionResponse)
def ask_question(
    version_id: str, body: QuestionRequest, session: Session = Depends(get_session)
) -> QuestionResponse:
    """Grounded Q&A over a finished case — answered only from the computed assessment."""
    version = _get_version_or_404(session, version_id)
    if version.raw_result is None:
        raise AssessmentNotReadyError(version_id, version.status)
    assessment = CreditAssessment.model_validate(version.raw_result)
    answer = answer_question(assessment, body.question)
    version.answer = answer
    session.commit()
    return QuestionResponse(answer=answer)


@router.post("/{version_id}/review", response_model=ReviewResponse)
def submit_review(
    version_id: str, body: ReviewRequest, session: Session = Depends(get_session)
) -> ReviewResponse:
    """Analyst/committee approve, correct, or reject (FR-8.2). A correction is recorded
    alongside the original values, not applied silently — reprocessing it is a separate,
    explicit `rerun` call."""
    version = _get_version_or_404(session, version_id)
    review = ReviewRow(
        assessment_version_id=version.id,
        reviewer=body.reviewer,
        action=body.action,
        corrected_fields=body.corrected_fields,
        reason=body.reason,
    )
    session.add(review)
    if body.action in _TERMINAL_REVIEW_STATUS:
        version.status = _TERMINAL_REVIEW_STATUS[body.action]
    session.commit()
    return ReviewResponse(
        id=review.id, reviewer=review.reviewer, action=review.action,
        reason=review.reason, reviewed_at=review.reviewed_at,
    )


@router.get("/{version_id}/audit", response_model=AuditTraceResponse)
def get_audit_trace(version_id: str, session: Session = Depends(get_session)) -> AuditTraceResponse:
    """Full evidence/reasoning trace — a read composed from the same tables everything
    else lives in, not a separately maintained store (CASE_STUDY.md §7.1)."""
    version = _get_version_or_404(session, version_id)
    return AuditTraceResponse(
        version_id=version.id,
        parent_version_id=version.parent_version_id,
        status=version.status,
        model_name=version.model_name,
        embed_model_name=version.embed_model_name,
        requested_by=version.requested_by,
        requested_at=version.requested_at,
        completed_at=version.completed_at,
        documents=[
            DocumentAudit(filename=d.filename, sha256_hash=d.sha256_hash, page_count=d.page_count)
            for d in version.documents
        ],
        line_items=[
            LineItemAudit(
                label=li.label, standardised_label=li.standardised_label,
                value=li.value, period=li.period, page=li.page,
            )
            for li in version.line_items
        ],
        rating_factors=[
            RatingFactorAudit(
                factor_type=f.factor_type, present=f.present, severity=f.severity,
                weight=f.weight, contribution=f.contribution, evidence=f.evidence,
            )
            for f in version.rating_factors
        ],
        reviews=[
            ReviewResponse(
                id=r.id, reviewer=r.reviewer, action=r.action,
                reason=r.reason, reviewed_at=r.reviewed_at,
            )
            for r in version.reviews
        ],
    )

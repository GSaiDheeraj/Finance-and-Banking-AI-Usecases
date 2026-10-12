"""Bridge between the filing readers and the relational store.

Reading a PDF never touches the database, and HTTP handlers never run the
readers directly — this module owns both handoffs so each side stays narrow.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime

import fitz  # PyMuPDF
from sqlalchemy.orm import Session

from fin_doc_intel.db.models import Document, LineItemRecord, Project, ProjectDocument
from fin_doc_intel.db.session import get_db_session
from fin_doc_intel.extraction import (
    ExtractionRunResult,
    detect_issuer_metadata,
    extract_document_content,
)
from fin_doc_intel.pdf_index import PageIndex, load_pdf_as_pages
from fin_doc_intel.schemas import LineItem
from fin_doc_intel.storage import DocumentStorage

logger = logging.getLogger(__name__)


def _count_sheets(raw_pdf: bytes) -> int:
    with fitz.open(stream=raw_pdf, filetype="pdf") as filing:
        return filing.page_count


def _utc_moment() -> datetime:
    return datetime.now(UTC)


def resolve_document(
    db: Session, filename: str, content: bytes, storage: DocumentStorage
) -> Document:
    """Return the stored row for these bytes, creating it on first sight.

    Identical uploads share one row and one folder: the PDF is rewritten every
    time, which is harmless on a repeat because the bytes match.
    """
    if not filename:
        raise ValueError("filename must be a non-empty string.")
    if not content:
        raise ValueError("content must be non-empty bytes.")
    digest = hashlib.sha256(content).hexdigest()
    stored = db.query(Document).filter(Document.sha256_hash == digest).one_or_none()
    if stored is None:
        stored = Document(
            id=str(uuid.uuid4()),
            sha256_hash=digest,
            filename=filename,
            page_count=_count_sheets(content),
            storage_uri="",
        )
        db.add(stored)

    disk_path, archive_path = storage.save_pdf(stored.id, filename, content)
    stored.storage_uri = disk_path
    stored.s3_uri = archive_path
    return stored


def create_project_and_documents(
    db: Session,
    project_id: str,
    project_name: str,
    description: str | None,
    documents: list[Document],
) -> Project:
    """Insert the project row plus its membership rows in a single commit."""
    if not project_id:
        raise ValueError("project_id must be a non-empty string.")
    if not project_name or not project_name.strip():
        raise ValueError("project_name must be a non-empty string.")
    project = Project(
        id=project_id,
        project_name=project_name,
        description=description,
        status="processing",
        requested_at=_utc_moment(),
    )
    db.add(project)
    for filing in documents:
        db.add(ProjectDocument(project_id=project_id, document_id=filing.id))
    db.commit()
    db.refresh(project)
    return project


def _record_for(document_id: str, fact: LineItem) -> LineItemRecord:
    return LineItemRecord(
        document_id=document_id,
        statement=fact.statement.value,
        label=fact.label,
        section_path=fact.section_path,
        value=fact.value,
        unit=fact.unit,
        currency=fact.currency,
        period=fact.period,
        period_end_date=fact.period_end_date,
        period_length_months=fact.period_length_months,
        scale=fact.scale,
        consolidated=fact.consolidated,
        page=fact.page,
        source_snippet=fact.source_snippet,
        validation_errors=[flag.model_dump() for flag in fact.validation_errors],
    )


def _replace_document_facts(
    db: Session,
    filing: Document,
    outcome: ExtractionRunResult,
    cover: dict[str, str | None],
) -> dict:
    filing.company_name = cover.get("issuer")
    filing.period = cover.get("period")
    filing.document_type = cover.get("document_type")
    filing.validation_iterations = outcome.iterations_run
    db.query(LineItemRecord).filter(LineItemRecord.document_id == filing.id).delete()
    for fact in outcome.line_items:
        db.add(_record_for(filing.id, fact))
    return {
        "document_id": filing.id,
        "company_name": cover.get("issuer"),
        "line_items": len(outcome.line_items),
        "iterations_run": outcome.iterations_run,
        "diagnostics": outcome.diagnostics,
    }


def _read_and_replace(db: Session, storage: DocumentStorage, document_id: str) -> tuple[dict, int]:
    filing = db.get(Document, document_id)
    if filing is None:
        raise ValueError(f"document {document_id} not found")
    lookup = PageIndex(load_pdf_as_pages(filing.storage_uri))
    cover = detect_issuer_metadata(lookup)
    outcome = extract_document_content(filing.storage_uri, document_id, storage)
    recap = _replace_document_facts(db, filing, outcome, cover)
    return recap, outcome.llm_call_count + 1


def run_and_persist(project_id: str, document_ids: list[str]) -> None:
    """Read every filing and store its facts.

    Earlier facts for a document are discarded and rewritten: two independent
    model passes have no stable row identity to merge on, so replace wins over
    diffing. Failures mark the project instead of raising, because this runs
    detached from the request that queued it.
    """
    db = get_db_session()
    try:
        project = db.get(Project, project_id)
        if project is None:
            logger.error("project %s not found when starting analysis", project_id)
            return

        started = time.time()
        model_calls = 0
        recaps = []
        keeper = DocumentStorage()
        for document_id in document_ids:
            try:
                recap, calls = _read_and_replace(db, keeper, document_id)
            except ValueError:
                logger.exception("skipping missing document %s", document_id)
                continue
            model_calls += calls
            recaps.append(recap)

        project.raw_result = {"documents": recaps}
        project.latency_ms = round((time.time() - started) * 1000.0, 2)
        project.llm_call_count = model_calls
        project.status = "completed"
        project.completed_at = _utc_moment()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("extraction failed for project %s", project_id)
        db.rollback()
        _record_failure(db, project_id, exc)
    finally:
        db.close()


def _record_failure(db: Session, project_id: str, cause: BaseException) -> None:
    """Persist a failure marker without letting recovery errors escape."""
    try:
        project = db.get(Project, project_id)
        if project is None:
            return
        project.status = "failed"
        project.raw_result = {"error": str(cause)}
        project.completed_at = _utc_moment()
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("failed to record failure status for project %s", project_id)
        db.rollback()

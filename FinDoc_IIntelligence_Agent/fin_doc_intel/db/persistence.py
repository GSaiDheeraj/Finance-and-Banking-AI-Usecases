"""
Persistence layer bridging the extraction pipeline (extraction.py) to PostgreSQL.

Kept out of `extraction.py` so that module stays DB-free and independently
testable/CLI-usable; kept out of `api/main.py` so routes stay thin. Also owns the
per-document loop (previously `agent_graph.py`'s job) — with no merge/research/Q&A
step left, that loop is this function's only remaining orchestration, so a
separate module for it would be pure indirection.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import datetime
from typing import List, Optional

import fitz  # PyMuPDF
from sqlalchemy.orm import Session

from fin_doc_intel.config import get_config
from fin_doc_intel.db.models import Document, LineItemRecord, Project, ProjectDocument
from fin_doc_intel.db.session import get_db_session
from fin_doc_intel.extraction import detect_issuer_metadata, extract_document_content
from fin_doc_intel.pdf_index import PageIndex, load_pdf_as_pages

logger = logging.getLogger(__name__)


def resolve_document(db: Session, filename: str, content: bytes) -> Document:
    """Find-or-create the `Document` row for this file's content.

    Documents are deduped globally by SHA-256: re-uploading byte-identical
    content — even under a different project or filename — reuses the same
    document_id and storage folder instead of piling up duplicates. The PDF
    is (re)written to disk unconditionally: on a hash match the bytes are
    identical so the write is a no-op, and skipping it would need a branch
    to handle nothing more than the rare case of a manually-deleted upload
    folder.
    """
    sha256_hash = hashlib.sha256(content).hexdigest()
    document = db.query(Document).filter(Document.sha256_hash == sha256_hash).one_or_none()
    if document is None:
        document = Document(
            id=str(uuid.uuid4()),
            sha256_hash=sha256_hash,
            filename=filename,
            page_count=fitz.open(stream=content, filetype="pdf").page_count,
            storage_uri="",
        )
        db.add(document)

    doc_dir = os.path.join(get_config().document_storage_dir, document.id)
    os.makedirs(doc_dir, exist_ok=True)
    file_path = os.path.join(doc_dir, filename)
    with open(file_path, "wb") as buffer:
        buffer.write(content)
    document.storage_uri = file_path
    return document


def create_project_and_documents(
    db: Session,
    project_id: str,
    project_name: str,
    description: Optional[str],
    documents: List[Document],
) -> Project:
    """Create the Project row and link it to `documents`, committed once.

    Runs synchronously in the request handler (not the background task) so the
    project exists, and is visible to any session, before the client gets a
    project_id back to poll with.
    """
    project = Project(
        id=project_id,
        project_name=project_name,
        description=description,
        status="processing",
        requested_at=datetime.utcnow(),
    )
    db.add(project)
    for document in documents:
        db.add(ProjectDocument(project_id=project_id, document_id=document.id))
    db.commit()
    db.refresh(project)
    return project


def run_and_persist(project_id: str, document_ids: List[str]) -> None:
    """Background-task entry point: extract every document's line items and
    persist them, detecting each document's company name along the way.

    Each document's prior `LineItemRecord`s are replaced (deleted, then
    reinserted), not merged — this is what makes a re-upload of the same
    content "update the existing results" rather than duplicate them. A full
    replace is used instead of matching old-vs-new rows because there's no
    stable identity for a line item across two independent LLM extraction
    runs (label wording can shift); delete-then-reinsert is also a single
    bulk statement rather than a per-row diff.

    A failure here (extraction or DB) is an expected operational outcome, not a
    bug in this function — it's recorded as `status="failed"` rather than raised,
    since this runs detached in a background thread with no caller to raise to.
    """
    db = get_db_session()
    try:
        project = db.get(Project, project_id)
        if project is None:
            logger.error("project %s not found when starting analysis", project_id)
            return

        start = time.time()
        total_llm_calls = 0
        per_document_summary = []
        for document_id in document_ids:
            document = db.get(Document, document_id)
            pdf_path = document.storage_uri
            index = PageIndex(load_pdf_as_pages(pdf_path))
            meta = detect_issuer_metadata(index)
            items, llm_calls, diagnostics = extract_document_content(pdf_path)
            total_llm_calls += llm_calls + 1  # +1 for detect_issuer_metadata

            document.company_name = meta.get("issuer")
            document.period = meta.get("period")
            db.query(LineItemRecord).filter(LineItemRecord.document_id == document_id).delete()

            for line_item in items:
                db.add(LineItemRecord(
                    document_id=document_id,
                    statement=line_item.statement.value,
                    label=line_item.label,
                    section_path=line_item.section_path,
                    value=line_item.value,
                    unit=line_item.unit,
                    currency=line_item.currency,
                    period=line_item.period,
                    period_end_date=line_item.period_end_date,
                    period_length_months=line_item.period_length_months,
                    scale=line_item.scale,
                    consolidated=line_item.consolidated,
                    page=line_item.page,
                    source_snippet=line_item.source_snippet,
                ))
            per_document_summary.append({
                "document_id": document_id,
                "company_name": meta.get("issuer"),
                "line_items": len(items),
                "diagnostics": diagnostics,
            })

        project.raw_result = {"documents": per_document_summary}
        project.latency_ms = round((time.time() - start) * 1000.0, 2)
        project.llm_call_count = total_llm_calls
        project.status = "completed"
        project.completed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        logger.exception("extraction failed for project %s", project_id)
        db.rollback()
        _mark_failed(db, project_id, exc)
    finally:
        db.close()


def _mark_failed(db: Session, project_id: str, exc: BaseException) -> None:
    """Record a failed run. Runs in its own try/except: the prior Project instance
    is expired after the rollback above, so it must be re-fetched, and this
    recovery path must not itself crash the background task."""
    try:
        project = db.get(Project, project_id)
        if project is not None:
            project.status = "failed"
            project.raw_result = {"error": str(exc)}
            project.completed_at = datetime.utcnow()
            db.commit()
    except Exception:
        logger.exception("failed to record failure status for project %s", project_id)
        db.rollback()

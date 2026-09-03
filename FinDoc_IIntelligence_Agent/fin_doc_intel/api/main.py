"""
FastAPI application for FinDoc Intelligence Agent.

This module provides the REST API endpoints for financial document analysis,
including project creation, status checking, and result retrieval.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from fin_doc_intel.db.models import Document, LineItemRecord, Project
from fin_doc_intel.db.persistence import create_project_and_documents, resolve_document, run_and_persist
from fin_doc_intel.db.session import get_db, init_db
from fin_doc_intel.schemas import LineItem


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FinDoc Intelligence API",
    description="Financial document intelligence and investment research API",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files for the web UI
app.mount("/static", StaticFiles(directory="web"), name="static")

# Coarse status -> progress fraction for the polling endpoint. There is no per-phase
# instrumentation of the pipeline, so this is the most granularity that's honest.
_STATUS_PROGRESS = {"processing": 0.5, "completed": 1.0, "failed": 1.0}


class DocumentSummary(BaseModel):
    """One processed document, for the documents list and as an upload's result."""
    document_id: str
    filename: str
    company_name: Optional[str] = None
    period: Optional[str] = None
    line_item_count: int = 0


class DocumentDetail(DocumentSummary):
    """A document plus every line item extracted from it."""
    line_items: List[LineItem] = []


class ProjectResponse(BaseModel):
    """Response model for project information."""
    project_id: str
    project_name: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    documents: List[DocumentSummary] = []


@app.get("/")
async def root():
    """Root endpoint that serves the web UI."""
    from fastapi.responses import FileResponse
    return FileResponse("web/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint for Kubernetes probes."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/projects", response_model=ProjectResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    project_name: str = Form(...),
    description: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
):
    """
    Create a new research project.

    Uploads financial documents, records the project and its documents, and starts
    the analysis pipeline in the background.
    """
    project_id = str(uuid.uuid4())
    documents: List[Document] = []
    for file in files or []:
        content = await file.read()
        documents.append(resolve_document(db, file.filename, content))

    project = create_project_and_documents(db, project_id, project_name, description, documents)

    background_tasks.add_task(
        run_and_persist,
        project_id=project_id,
        document_ids=[document.id for document in documents],
    )

    return ProjectResponse(
        project_id=project.id,
        project_name=project.project_name,
        status=project.status,
        created_at=project.requested_at.isoformat(),
        documents=[
            DocumentSummary(document_id=document.id, filename=document.filename)
            for document in documents
        ],
    )


def _line_item_counts_by_document(db: Session) -> dict:
    """Map every document_id with at least one extracted fact to its row count."""
    return dict(
        db.query(LineItemRecord.document_id, func.count(LineItemRecord.id))
        .group_by(LineItemRecord.document_id)
        .all()
    )


def _document_summary(document: Document, line_item_count: int) -> DocumentSummary:
    return DocumentSummary(
        document_id=document.id,
        filename=document.filename,
        company_name=document.company_name,
        period=document.period,
        line_item_count=line_item_count,
    )


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: Session = Depends(get_db)):
    """Get project status and basic information, plus the documents it uploaded."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    counts = _line_item_counts_by_document(db)
    documents = [link.document for link in project.documents]
    return ProjectResponse(
        project_id=project.id,
        project_name=project.project_name,
        status=project.status,
        created_at=project.requested_at.isoformat(),
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
        documents=[_document_summary(document, counts.get(document.id, 0)) for document in documents],
    )


@app.get("/documents", response_model=List[DocumentSummary])
async def list_documents(db: Session = Depends(get_db)):
    """List every document ever uploaded, newest first — the source for the
    Documents tab's picker. A document with `line_item_count == 0` is either
    still processing or its extraction run failed; there is no per-document
    status field, so the count is the only signal available today."""
    counts = _line_item_counts_by_document(db)
    documents = db.query(Document).order_by(Document.uploaded_at.desc()).all()
    return [_document_summary(document, counts.get(document.id, 0)) for document in documents]


@app.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, db: Session = Depends(get_db)):
    """Get one document's metadata plus every line item extracted from it —
    primary statements, KPIs/ratios, and notes alike."""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    rows = (
        db.query(LineItemRecord)
        .filter(LineItemRecord.document_id == document_id)
        .order_by(LineItemRecord.statement, LineItemRecord.page)
        .all()
    )
    return DocumentDetail(
        **_document_summary(document, len(rows)).model_dump(),
        line_items=[
            LineItem(
                statement=row.statement,
                label=row.label,
                section_path=row.section_path or [],
                value=row.value,
                unit=row.unit,
                currency=row.currency,
                period=row.period,
                period_end_date=row.period_end_date,
                period_length_months=row.period_length_months,
                scale=row.scale,
                consolidated=row.consolidated,
                page=row.page,
                source_snippet=row.source_snippet,
            )
            for row in rows
        ],
    )


@app.get("/projects/{project_id}/status")
async def get_project_status(project_id: str, db: Session = Depends(get_db)):
    """Get the current processing status of a project."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return {
        "project_id": project.id,
        "status": project.status,
        "progress": _STATUS_PROGRESS.get(project.status, 0.0),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

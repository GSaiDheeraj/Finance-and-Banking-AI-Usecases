"""HTTP surface for uploading filings and reading back extracted facts."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from fin_doc_intel.db.models import Document, LineItemRecord, Project
from fin_doc_intel.db.persistence import (
    create_project_and_documents,
    resolve_document,
    run_and_persist,
)
from fin_doc_intel.db.session import get_db, init_db
from fin_doc_intel.schemas import LineItem
from fin_doc_intel.storage import DocumentStorage


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FinDoc Intelligence API",
    description="Financial document intelligence and investment research API",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="web"), name="static")

_PROGRESS_BY_STATUS = {"processing": 0.5, "completed": 1.0, "failed": 1.0}


class DocumentSummary(BaseModel):
    """One filing and its extraction counters, without the fact rows."""

    document_id: str
    filename: str
    company_name: str | None = None
    period: str | None = None
    document_type: str | None = None
    line_item_count: int = 0
    validation_iterations: int | None = None
    flagged_line_item_count: int = 0


class DocumentDetail(DocumentSummary):
    """A filing together with every fact read from it."""

    line_items: list[LineItem] = []


class ProjectResponse(BaseModel):
    """Project status plus the filings uploaded under it."""

    project_id: str
    project_name: str
    status: str
    created_at: str
    completed_at: str | None = None
    documents: list[DocumentSummary] = []


@app.get("/")
async def root():
    """Serve the browser workspace."""
    return FileResponse("web/index.html")


@app.get("/health")
async def health_check():
    """Liveness probe for orchestrators."""
    return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}


@app.post("/projects", response_model=ProjectResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    project_name: str = Form(...),
    description: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
):
    """Accept filings, register the project, and queue the readers."""
    if not project_name or not project_name.strip():
        raise HTTPException(status_code=422, detail="project_name must be non-empty")
    project_id = str(uuid.uuid4())
    keeper = DocumentStorage()
    filings: list[Document] = []
    for upload in files or []:
        raw_bytes = await upload.read()
        filings.append(resolve_document(db, upload.filename or "upload.pdf", raw_bytes, keeper))

    project = create_project_and_documents(db, project_id, project_name, description, filings)

    background_tasks.add_task(
        run_and_persist,
        project_id=project_id,
        document_ids=[filing.id for filing in filings],
    )

    return ProjectResponse(
        project_id=project.id,
        project_name=project.project_name,
        status=project.status,
        created_at=project.requested_at.isoformat(),
        documents=[
            DocumentSummary(document_id=filing.id, filename=filing.filename) for filing in filings
        ],
    )


def _tally_items(db: Session, flagged_only: bool = False) -> dict[str, int]:
    rows = db.query(LineItemRecord.document_id, func.count(LineItemRecord.id))
    if flagged_only:
        rows = rows.filter(func.json_array_length(LineItemRecord.validation_errors) > 0)
    return dict(rows.group_by(LineItemRecord.document_id).all())


def _summarise_document(filing: Document, total: int, flagged: int = 0) -> DocumentSummary:
    return DocumentSummary(
        document_id=filing.id,
        filename=filing.filename,
        company_name=filing.company_name,
        period=filing.period,
        document_type=filing.document_type,
        line_item_count=total,
        validation_iterations=filing.validation_iterations,
        flagged_line_item_count=flagged,
    )


def _totals_for(db: Session) -> tuple[dict[str, int], dict[str, int]]:
    return _tally_items(db), _tally_items(db, flagged_only=True)


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: Session = Depends(get_db)):
    """Fetch one project with per-filing counters."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    totals, flagged_totals = _totals_for(db)
    filings = [link.document for link in project.documents]
    return ProjectResponse(
        project_id=project.id,
        project_name=project.project_name,
        status=project.status,
        created_at=project.requested_at.isoformat(),
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
        documents=[
            _summarise_document(filing, totals.get(filing.id, 0), flagged_totals.get(filing.id, 0))
            for filing in filings
        ],
    )


@app.get("/documents", response_model=list[DocumentSummary])
async def list_documents(db: Session = Depends(get_db)):
    """Every filing ever stored, newest first."""
    totals, flagged_totals = _totals_for(db)
    filings = db.query(Document).order_by(Document.uploaded_at.desc()).all()
    return [
        _summarise_document(filing, totals.get(filing.id, 0), flagged_totals.get(filing.id, 0))
        for filing in filings
    ]


def _row_to_fact(row: LineItemRecord) -> LineItem:
    return LineItem(
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
        validation_errors=row.validation_errors or [],
    )


@app.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, db: Session = Depends(get_db)):
    """One filing's cover fields plus all of its extracted facts."""
    filing = db.get(Document, document_id)
    if filing is None:
        raise HTTPException(status_code=404, detail="document not found")

    rows = (
        db.query(LineItemRecord)
        .filter(LineItemRecord.document_id == document_id)
        .order_by(LineItemRecord.statement, LineItemRecord.page)
        .all()
    )
    flagged = sum(1 for row in rows if row.validation_errors)
    return DocumentDetail(
        **_summarise_document(filing, len(rows), flagged).model_dump(),
        line_items=[_row_to_fact(row) for row in rows],
    )


@app.get("/documents/{document_id}/file")
async def get_document_file(document_id: str, db: Session = Depends(get_db)):
    """Send the original PDF back for in-browser viewing."""
    filing = db.get(Document, document_id)
    if filing is None:
        raise HTTPException(status_code=404, detail="document not found")
    if not filing.storage_uri or not Path(filing.storage_uri).exists():
        raise HTTPException(status_code=404, detail="stored file not found")
    return FileResponse(
        filing.storage_uri,
        media_type="application/pdf",
        filename=filing.filename,
        content_disposition_type="inline",
    )


@app.get("/projects/{project_id}/status")
async def get_project_status(project_id: str, db: Session = Depends(get_db)):
    """Lightweight progress probe while the readers run."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return {
        "project_id": project.id,
        "status": project.status,
        "progress": _PROGRESS_BY_STATUS.get(project.status, 0.0),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

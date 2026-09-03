"""
FastAPI application for Fraud Detection Agent.

This module provides the REST API endpoints for customer onboarding risk assessment,
including case creation, status checking, and result retrieval.
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os
import uuid
from datetime import datetime

from onboarding_risk.agent_graph import run_onboarding_assessment
from onboarding_risk.subject_graph import run_subject_360, SubjectSeed
from onboarding_risk.config import get_config
from onboarding_risk.db.session import init_db, get_db_session
from onboarding_risk.db.models import Case, UBO, WatchlistHit, EDDMeasure, Review
from onboarding_risk.storage import get_storage_manager

app = FastAPI(
    title="Fraud Detection API",
    description="Customer onboarding risk assessment and fraud detection API",
    version="1.0.0"
)

config = get_config()

# Mount static files for the web UI
app.mount("/static", StaticFiles(directory="web"), name="static")


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    try:
        init_db()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Warning: Database initialization failed: {e}")
        print("Continuing with database operations (may fail if DB is unavailable)")


def serialize_pydantic(obj):
    """Helper function to serialize Pydantic models to JSON-compatible dicts."""
    if hasattr(obj, 'model_dump'):
        # Use mode='json' to automatically convert enums to their string values
        return obj.model_dump(mode='json')
    elif hasattr(obj, 'dict'):
        return obj.dict()
    elif isinstance(obj, dict):
        return {k: serialize_pydantic(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_pydantic(item) for item in obj]
    else:
        return obj


def save_assessment_to_db(case_id: str, client_name: str, case_type: str, result: Dict[str, Any], pdf_paths: Optional[List[str]] = None):
    """Save assessment results to the database, handling duplicate client names by updating existing records."""
    try:
        db = get_db_session()
        storage_manager = get_storage_manager()
        
        try:
            assessment = result.get("assessment")
            if not assessment:
                raise ValueError("No assessment in result")
            
            scorecard = assessment.scorecard
            ownership = assessment.ownership
            
            # Check if client already exists and get the latest version
            existing_case = db.query(Case).filter(
                Case.client_name == client_name,
                Case.is_latest == True
            ).first()
            
            analysis_version = 1
            if existing_case:
                # Mark existing as not latest and increment version
                existing_case.is_latest = False
                analysis_version = (existing_case.analysis_version or 0) + 1
            
            # Save files to storage with timestamp
            storage_path = None
            if pdf_paths:
                # Save uploaded PDFs to storage
                files_to_save = []
                for pdf_path in pdf_paths:
                    if os.path.exists(pdf_path):
                        filename = os.path.basename(pdf_path)
                        with open(pdf_path, 'rb') as f:
                            files_to_save.append((filename, f.read()))
                
                metadata = {
                    "client_name": client_name,
                    "case_type": case_type,
                    "case_id": case_id,
                    "analysis_version": analysis_version,
                    "risk_score": scorecard.normalized_score,
                    "risk_band": scorecard.band.value,
                    "decision": scorecard.decision.value
                }
                
                storage_path = storage_manager.save_analysis_files(
                    client_name, files_to_save, metadata, serialize_pydantic(result)
                )
            else:
                # For name_search, still save metadata and result
                metadata = {
                    "client_name": client_name,
                    "case_type": case_type,
                    "case_id": case_id,
                    "analysis_version": analysis_version,
                    "risk_score": scorecard.normalized_score,
                    "risk_band": scorecard.band.value,
                    "decision": scorecard.decision.value
                }
                
                storage_path = storage_manager.save_analysis_files(
                    client_name, [], metadata, serialize_pydantic(result)
                )
            
            # Create or update case with duplicate handling
            case = db.query(Case).filter(Case.id == case_id).first()
            if not case:
                case = Case(
                    id=case_id,
                    client_name=client_name,
                    case_type=case_type,
                    status="completed",
                    risk_score=scorecard.normalized_score,
                    risk_band=scorecard.band.value.upper(),
                    decision=scorecard.decision.value.upper(),
                    hard_stop_reason=scorecard.hard_stop_reason,
                    raw_result=serialize_pydantic(result),
                    reasoning=result.get("reasoning", []),
                    latency_ms=result.get("metrics", {}).get("latency_ms"),
                    llm_call_count=result.get("metrics", {}).get("llm_calls"),
                    completed_at=datetime.utcnow(),
                    analysis_version=analysis_version,
                    storage_path=storage_path,
                    is_latest=True
                )
                db.add(case)
            else:
                case.status = "completed"
                case.risk_score = scorecard.normalized_score
                case.risk_band = scorecard.band.value.upper()
                case.decision = scorecard.decision.value.upper()
                case.hard_stop_reason = scorecard.hard_stop_reason
                # Convert Pydantic model to dict for JSON serialization
                case.raw_result = serialize_pydantic(result)
                case.reasoning = result.get("reasoning", [])
                case.latency_ms = result.get("metrics", {}).get("latency_ms")
                case.llm_call_count = result.get("metrics", {}).get("llm_calls")
                case.completed_at = datetime.utcnow()
                case.analysis_version = analysis_version
                case.storage_path = storage_path
                case.is_latest = True
            
            # Clear existing related records
            db.query(UBO).filter(UBO.case_id == case_id).delete()
            db.query(WatchlistHit).filter(WatchlistHit.case_id == case_id).delete()
            db.query(EDDMeasure).filter(EDDMeasure.case_id == case_id).delete()
            
            # Save UBOs
            for ubo in ownership.ubos:
                db.add(UBO(
                    case_id=case_id,
                    party_id=ubo.party_id,
                    party_name=ubo.name,
                    effective_ownership_percentage=ubo.effective_ownership_pct,
                    basis=ubo.basis,
                    control_roles=ubo.control_roles,
                    ownership_paths=ubo.paths
                ))
            
            # Save watchlist hits
            for hit in assessment.watchlist_hits:
                db.add(WatchlistHit(
                    case_id=case_id,
                    party_id=hit.party_id,
                    party_name=hit.party_name,
                    list_type=hit.list_type,
                    matched_name=hit.matched_name,
                    match_score=hit.match_score,
                    tier=hit.tier,
                    details=hit.details,
                    source_list=hit.source_list
                ))
            
            # Save EDD measures
            for measure in assessment.edd_rationale.edd_measures:
                db.add(EDDMeasure(
                    case_id=case_id,
                    measure=measure
                ))
            
            # Create an automatic review record based on the decision
            decision = scorecard.decision.value
            action = "approved"
            reason = f"Automatically approved based on risk assessment: {decision}"
            
            if decision == "decline":
                action = "rejected"
                reason = f"Automatically rejected due to: {scorecard.hard_stop_reason or 'high risk assessment'}"
            elif decision == "escalate_mlro":
                action = "corrected"
                reason = "Escalated to MLRO for manual review due to elevated risk"
            
            db.add(Review(
                case_id=case_id,
                reviewer="system",
                action=action,
                reason=reason
            ))
            
            db.commit()
            print(f"Assessment {case_id} saved to database successfully")
            
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()
    except Exception as e:
        print(f"Failed to save assessment to database: {e}")
        # Continue without failing the assessment


def run_pdf_assessment_wrapper(pdf_paths: List[str], case_id: str, client_name: str):
    """Wrapper to run PDF assessment and store results in database."""
    try:
        # Run assessment directly without creating processing record
        result = run_onboarding_assessment(pdf_paths)
        
        # Save results to database with file storage (only when completed)
        save_assessment_to_db(case_id, client_name, "documents", result, pdf_paths)
        
    except Exception as e:
        print(f"PDF assessment failed: {e}")
        # Save failed status to database for tracking
        try:
            db = get_db_session()
            case = Case(
                id=case_id,
                client_name=client_name,
                case_type="documents",
                status="failed",
                completed_at=datetime.utcnow()
            )
            db.add(case)
            db.commit()
            db.close()
        except Exception as db_error:
            print(f"Failed to save failed status: {db_error}")


def run_subject_assessment_wrapper(seed: SubjectSeed, case_id: str, client_name: str):
    """Wrapper to run subject assessment and store results in database."""
    try:
        # Run assessment directly without creating processing record
        result = run_subject_360(seed)
        
        # Save results to database (no PDF files for name search)
        save_assessment_to_db(case_id, client_name, "name_search", result, None)
        
    except Exception as e:
        print(f"Subject assessment failed: {e}")
        # Save failed status to database for tracking
        try:
            db = get_db_session()
            case = Case(
                id=case_id,
                client_name=client_name,
                case_type="name_search",
                status="failed",
                completed_at=datetime.utcnow()
            )
            db.add(case)
            db.commit()
            db.close()
        except Exception as db_error:
            print(f"Failed to save failed status: {db_error}")


class CaseCreateRequest(BaseModel):
    """Request model for creating a new onboarding case."""
    client_name: str
    case_type: str  # "documents" or "name_search"
    description: Optional[str] = None


class CaseResponse(BaseModel):
    """Response model for case information."""
    case_id: str
    client_name: str
    case_type: str
    status: str
    created_at: str
    completed_at: Optional[str] = None


class AssessmentResult(BaseModel):
    """Response model for assessment results."""
    case_id: str
    risk_score: float
    risk_band: str  # lowercase: low, medium, high, prohibited
    decision: str  # lowercase: approve_standard_cdd, approve_with_edd, escalate_mlro, decline
    ubos: List[dict]
    watchlist_hits: List[dict]
    edd_measures: List[str]


class ClientInfo(BaseModel):
    """Response model for client information."""
    client_name: str
    total_analyses: int
    latest_analysis_id: Optional[str] = None
    latest_risk_score: Optional[float] = None
    latest_risk_band: Optional[str] = None
    latest_decision: Optional[str] = None
    latest_analysis_date: Optional[str] = None


class HistoricalAnalysis(BaseModel):
    """Response model for historical analysis."""
    case_id: str
    client_name: str
    case_type: str
    status: str
    analysis_version: int
    risk_score: Optional[float] = None
    risk_band: Optional[str] = None
    decision: Optional[str] = None
    storage_path: Optional[str] = None
    requested_at: str
    completed_at: Optional[str] = None


@app.get("/")
async def root():
    """Root endpoint that serves the web UI."""
    from fastapi.responses import FileResponse
    return FileResponse("web/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint for Kubernetes probes."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/cases", response_model=CaseResponse)
async def create_case(
    background_tasks: BackgroundTasks,
    client_name: str = Form(...),
    case_type: str = Form(...),
    description: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None)
):
    """
    Create a new onboarding case.
    
    Uploads KYC documents or starts a name-based search, running the assessment pipeline in the background.
    """
    case_id = str(uuid.uuid4())
    
    # Save uploaded files if provided
    file_paths = []
    if files and case_type == "documents":
        upload_dir = os.path.join(config.document_storage_dir, case_id)
        os.makedirs(upload_dir, exist_ok=True)
        os.makedirs(config.document_storage_dir, exist_ok=True)
        
        for file in files:
            file_path = os.path.join(upload_dir, file.filename)
            with open(file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
            file_paths.append(file_path)
    
    # Schedule background task
    if case_type == "documents" and file_paths:
        background_tasks.add_task(
            run_pdf_assessment_wrapper,
            pdf_paths=file_paths,
            case_id=case_id,
            client_name=client_name
        )
    elif case_type == "name_search":
        seed = SubjectSeed(full_name=client_name)
        background_tasks.add_task(
            run_subject_assessment_wrapper,
            seed=seed,
            case_id=case_id,
            client_name=client_name
        )
    
    # Return immediate response without processing status in DB
    return CaseResponse(
        case_id=case_id,
        client_name=client_name,
        case_type=case_type,
        status="queued",
        created_at=datetime.utcnow().isoformat()
    )


@app.get("/cases/{case_id}", response_model=CaseResponse)
async def get_case(case_id: str):
    """Get case status and basic information."""
    try:
        db = get_db_session()
        case = db.query(Case).filter(Case.id == case_id).first()
        db.close()
        
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        
        return CaseResponse(
            case_id=case.id,
            client_name=case.client_name,
            case_type=case.case_type,
            status=case.status,
            created_at=case.requested_at.isoformat() if case.requested_at else datetime.utcnow().isoformat(),
            completed_at=case.completed_at.isoformat() if case.completed_at else None
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching case: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch case")


@app.get("/cases/{case_id}/result", response_model=AssessmentResult)
async def get_case_result(case_id: str):
    """Get the full assessment result for a completed case."""
    try:
        db = get_db_session()
        case = db.query(Case).filter(Case.id == case_id).first()
        
        if not case:
            db.close()
            raise HTTPException(status_code=404, detail="Case not found")
        
        if case.status != "completed":
            db.close()
            raise HTTPException(status_code=400, detail="Assessment not completed")
        
        # Try to get data from database first
        ubos = []
        watchlist_hits = []
        edd_measures = []
        
        try:
            # Get UBOs from database
            ubos = [
                {
                    "party_name": ubo.party_name,
                    "effective_ownership_percentage": ubo.effective_ownership_percentage,
                    "basis": ubo.basis,
                    "control_roles": ubo.control_roles
                }
                for ubo in db.query(UBO).filter(UBO.case_id == case_id).all()
            ]
            
            # Get watchlist hits from database
            watchlist_hits = [
                {
                    "party_name": hit.party_name,
                    "list_type": hit.list_type,
                    "matched_name": hit.matched_name,
                    "match_score": hit.match_score,
                    "details": hit.details
                }
                for hit in db.query(WatchlistHit).filter(WatchlistHit.case_id == case_id).all()
            ]
            
            # Get EDD measures from database
            edd_measures = [
                measure.measure
                for measure in db.query(EDDMeasure).filter(EDDMeasure.case_id == case_id).all()
            ]
        except Exception as db_error:
            print(f"Error fetching related data from DB: {db_error}")
            # If database query fails, try to get from raw_result
            if case.raw_result and "assessment" in case.raw_result:
                assessment_data = case.raw_result["assessment"]
                try:
                    # Extract UBOs from raw result
                    if "ownership" in assessment_data and "ubos" in assessment_data["ownership"]:
                        for ubo in assessment_data["ownership"]["ubos"]:
                            ubos.append({
                                "party_name": ubo.get("name", ""),
                                "effective_ownership_percentage": ubo.get("effective_ownership_pct"),
                                "basis": ubo.get("basis", ""),
                                "control_roles": ubo.get("control_roles", [])
                            })
                    
                    # Extract watchlist hits from raw result
                    if "watchlist_hits" in assessment_data:
                        for hit in assessment_data["watchlist_hits"]:
                            watchlist_hits.append({
                                "party_name": hit.get("party_name", ""),
                                "list_type": hit.get("list_type", ""),
                                "matched_name": hit.get("matched_name", ""),
                                "match_score": hit.get("match_score"),
                                "details": hit.get("details", "")
                            })
                    
                    # Extract EDD measures from raw result
                    if "edd_rationale" in assessment_data and "edd_measures" in assessment_data["edd_rationale"]:
                        edd_measures = assessment_data["edd_rationale"]["edd_measures"]
                except Exception as raw_error:
                    print(f"Error extracting from raw_result: {raw_error}")
        
        db.close()
        
        return AssessmentResult(
            case_id=case_id,
            risk_score=case.risk_score or 0.0,
            risk_band=(case.risk_band or "UNKNOWN").lower(),
            decision=(case.decision or "UNKNOWN").lower(),
            ubos=ubos,
            watchlist_hits=watchlist_hits,
            edd_measures=edd_measures
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching case result: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch case result")


@app.get("/cases/{case_id}/status")
async def get_case_status(case_id: str):
    """Get the current processing status of a case."""
    try:
        db = get_db_session()
        case = db.query(Case).filter(Case.id == case_id).first()
        db.close()
        
        if not case:
            # Case not found in DB means it's still being processed
            return {
                "case_id": case_id,
                "status": "processing",
                "current_phase": "analyzing",
                "progress": 0.5
            }
        
        # Determine current phase and progress based on status
        status = case.status
        if status == "completed":
            current_phase = "completed"
            progress = 1.0
        elif status == "failed":
            current_phase = "failed"
            progress = 0.0
        else:
            current_phase = "unknown"
            progress = 0.0
        
        return {
            "case_id": case_id,
            "status": status,
            "current_phase": current_phase,
            "progress": progress
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching case status: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch case status")


@app.get("/clients", response_model=List[ClientInfo])
async def get_all_clients():
    """Get list of all unique clients with their latest analysis info."""
    try:
        db = get_db_session()
        
        # Get unique client names
        unique_clients = db.query(Case.client_name).distinct().all()
        
        client_info = []
        for (client_name,) in unique_clients:
            # Get the latest analysis for this client
            latest_case = db.query(Case).filter(
                Case.client_name == client_name,
                Case.is_latest == True
            ).first()
            
            # Count total analyses for this client
            total_analyses = db.query(Case).filter(Case.client_name == client_name).count()
            
            if latest_case:
                client_info.append(ClientInfo(
                    client_name=client_name,
                    total_analyses=total_analyses,
                    latest_analysis_id=latest_case.id,
                    latest_risk_score=latest_case.risk_score,
                    latest_risk_band=latest_case.risk_band.lower() if latest_case.risk_band else None,
                    latest_decision=latest_case.decision.lower() if latest_case.decision else None,
                    latest_analysis_date=latest_case.completed_at.isoformat() if latest_case.completed_at else None
                ))
        
        db.close()
        return client_info
    except Exception as e:
        print(f"Error fetching clients: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch clients")


@app.get("/clients/{client_name}/history", response_model=List[HistoricalAnalysis])
async def get_client_history(client_name: str):
    """Get historical analysis for a specific client."""
    try:
        db = get_db_session()
        
        # Get all analyses for the client, ordered by version (newest first)
        cases = db.query(Case).filter(
            Case.client_name == client_name
        ).order_by(Case.analysis_version.desc()).all()
        
        history = []
        for case in cases:
            history.append(HistoricalAnalysis(
                case_id=case.id,
                client_name=case.client_name,
                case_type=case.case_type,
                status=case.status,
                analysis_version=case.analysis_version,
                risk_score=case.risk_score,
                risk_band=case.risk_band.lower() if case.risk_band else None,
                decision=case.decision.lower() if case.decision else None,
                storage_path=case.storage_path,
                requested_at=case.requested_at.isoformat() if case.requested_at else "",
                completed_at=case.completed_at.isoformat() if case.completed_at else None
            ))
        
        db.close()
        return history
    except Exception as e:
        print(f"Error fetching client history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch client history")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

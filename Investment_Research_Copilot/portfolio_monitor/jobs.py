"""Background monitoring job executed by FastAPI BackgroundTasks or Celery."""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.orm import Session

from .agent_graph import run_construction, run_monitoring
from .config import get_config, llm_available
from .db.models import PortfolioRun
from .db.persist import persist_monitoring_result
from .db.session import get_session
from .logconf import get_logger
from .schemas import ConstructionRequest
from .status_tracker import set_phase

_log = get_logger()


def execute_monitoring_job(
    run_id: str,
    holdings_path: str = "",
    transactions_path: str = "",
    statement_path: str = "",
    construction_params: Optional[dict] = None,
    mandate: str = "balanced",
    base_currency: str = "USD",
    mode: str = "both",
    enable_news: bool = True,
    question: str = "",
) -> None:
    """Run the monitoring pipeline and persist results. Opens its own DB session."""
    session = next(get_session())
    try:
        run = session.get(PortfolioRun, run_id)
        if run is None:
            _log.error("monitoring job: run %s vanished before it could start", run_id)
            return

        run.status = "processing"
        run.model_name = get_config().llm_model_name if llm_available() else None
        session.commit()

        # A plain dict crosses the Celery/BackgroundTasks boundary (Celery's task serializer
        # is JSON-only); rebuild the typed request here, closest to where it's actually used.
        construction_request = (
            ConstructionRequest.model_validate(construction_params)
            if construction_params else None
        )

        set_phase(run_id, "ingesting_holdings", "Loading holdings and transactions")
        out = run_monitoring(
            holdings_path=holdings_path,
            transactions_path=transactions_path,
            statement_path=statement_path,
            construction_request=construction_request,
            mandate=mandate,
            base_currency=base_currency,
            mode=mode,
            enable_news=enable_news,
            question=question,
            name=run.portfolio_name,
        )

        set_phase(run_id, "persisting_results", "Saving alerts, trades, and briefing")
        result = out["result"]
        if question and out.get("answer"):
            run.answer = out["answer"]

        persist_monitoring_result(session, run, result, out["reasoning"], out["metrics"])
        set_phase(run_id, "done", "Monitoring complete")
        session.commit()
    except Exception:
        _log.error("monitoring job failed for run=%s", run_id, exc_info=True)
        try:
            run = session.get(PortfolioRun, run_id)
            if run is not None:
                run.status = "validation_review"
                set_phase(run_id, "failed", "Pipeline error — see server logs")
                session.commit()
        except Exception:
            session.rollback()
    finally:
        session.close()


def execute_construction_job(
    run_id: str,
    construction_params: dict,
    mandate: str = "balanced",
    base_currency: str = "USD",
    mode: str = "both",
    enable_news: bool = True,
) -> None:
    """Run the construction pipeline and persist the full ConstructionResult (rationale,
    pros/cons, feasibility, country research intact — unlike the monitoring wrapper's
    `_construct_from_scratch`, which discards all of that when it flattens positions into
    plain Holdings). Same run/commit/fail-soft shape as `execute_monitoring_job`."""
    session = next(get_session())
    try:
        run = session.get(PortfolioRun, run_id)
        if run is None:
            _log.error("construction job: run %s vanished before it could start", run_id)
            return

        run.status = "processing"
        run.model_name = get_config().llm_model_name if llm_available() else None
        session.commit()

        request = ConstructionRequest.model_validate(construction_params)
        set_phase(run_id, "constructing_portfolio", "Building the universe and portfolio")
        out = run_construction(request, mode=mode, enable_news=enable_news)

        set_phase(run_id, "persisting_results", "Saving the constructed portfolio")
        result = out["result"]
        run.raw_result = result.model_dump(mode="json")
        run.reasoning = out["reasoning"]
        run.latency_ms = out["metrics"].get("latency_ms")
        run.llm_call_count = 1 if (mode in ("llm", "both") and llm_available()) else 0
        run.status = "awaiting_review"
        run.completed_at = run.completed_at or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc)
        set_phase(run_id, "done", "Construction complete")
        session.commit()
    except Exception:
        _log.error("construction job failed for run=%s", run_id, exc_info=True)
        try:
            run = session.get(PortfolioRun, run_id)
            if run is not None:
                run.status = "validation_review"
                set_phase(run_id, "failed", "Pipeline error — see server logs")
                session.commit()
        except Exception:
            session.rollback()
    finally:
        session.close()


def celery_enabled() -> bool:
    import os

    return os.getenv("USE_CELERY", "").lower() in ("1", "true", "yes")


def dispatch_monitoring_job(**kwargs) -> Optional[str]:
    """Dispatch to Celery when enabled; otherwise caller uses BackgroundTasks."""
    if not celery_enabled():
        return None
    from .tasks import run_monitoring_task

    task = run_monitoring_task.delay(**kwargs)
    return task.id


def dispatch_construction_job(**kwargs) -> Optional[str]:
    """Dispatch to Celery when enabled; otherwise caller uses BackgroundTasks."""
    if not celery_enabled():
        return None
    from .tasks import run_construction_task

    task = run_construction_task.delay(**kwargs)
    return task.id

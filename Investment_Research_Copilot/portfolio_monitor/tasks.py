"""Celery tasks for durable background monitoring."""
from __future__ import annotations

from .celery_app import celery_app
from .jobs import execute_construction_job, execute_monitoring_job


@celery_app.task(name="portfolio_monitor.run_monitoring", bind=True, max_retries=2)
def run_monitoring_task(
    self,
    run_id: str,
    holdings_path: str = "",
    transactions_path: str = "",
    statement_path: str = "",
    construction_params: dict | None = None,
    mandate: str = "balanced",
    base_currency: str = "USD",
    mode: str = "both",
    enable_news: bool = True,
    question: str = "",
) -> None:
    try:
        execute_monitoring_job(
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
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="portfolio_monitor.run_construction", bind=True, max_retries=2)
def run_construction_task(
    self,
    run_id: str,
    construction_params: dict,
    mandate: str = "balanced",
    base_currency: str = "USD",
    mode: str = "both",
    enable_news: bool = True,
) -> None:
    try:
        execute_construction_job(
            run_id=run_id,
            construction_params=construction_params,
            mandate=mandate,
            base_currency=base_currency,
            mode=mode,
            enable_news=enable_news,
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)

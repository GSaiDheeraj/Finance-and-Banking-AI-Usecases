"""
Celery application for durable background monitoring jobs.

Postgres and Redis are external services. When CELERY_BROKER_URL is set, the API
dispatches long-running monitoring runs to a separate worker pod instead of using
FastAPI BackgroundTasks (which do not survive process restarts).
"""
from __future__ import annotations

import os

from celery import Celery

_broker = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL")
if not _broker:
    _host = os.getenv("REDIS_HOST", "127.0.0.1")
    _port = os.getenv("REDIS_PORT", "6379")
    _db = os.getenv("CELERY_REDIS_DB", os.getenv("REDIS_DB", "1"))
    _broker = f"redis://{_host}:{_port}/{_db}"

celery_app = Celery(
    "portfolio_monitor",
    broker=_broker,
    backend=_broker,
    include=["portfolio_monitor.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

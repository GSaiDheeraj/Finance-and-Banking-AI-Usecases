"""
Live job-progress tracking in Redis — separate from Postgres's durable status.

`AssessmentVersion.status` in Postgres (created/processing/awaiting_review/...) is the
durable, historical record — it changes at a handful of commit points and is what the
audit trail relies on. While a background job is actually running, a client polling for
"what's happening right now" doesn't need a full row fetch — this module gives the
pipeline a way to publish a lightweight phase update as it progresses, and gives the poll
endpoint a fast, DB-free read for the common case.

This is a genuinely optional enhancement, not a new hard dependency: every write/read
degrades to a no-op/`None` if Redis is unreachable (logged once, not per call), so a
missing or down Redis never blocks or fails an assessment — it only means the client's
poll falls back to Postgres's coarser status (see `api/routes.py`'s `/status` endpoint).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import redis

from .logconf import get_logger

_log = get_logger()
_STATUS_TTL_SECONDS = 3600  # a finished/abandoned job's last phase is irrelevant after this
_KEY_PREFIX = "credit_risk:assessment_status:"

_client: Optional[redis.Redis] = None
_unavailable = False  # sticky flag so a down Redis logs once, not on every poll


def _get_client() -> Optional[redis.Redis]:
    """Lazily construct the client once; return None (not raise) if Redis is unreachable."""
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        _client = redis.Redis(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    try:
        _client.ping()
    except redis.RedisError as exc:
        _log.warning("status_tracker: Redis unavailable (%s) — live phase updates disabled, "
                     "falling back to the durable Postgres status.", exc)
        _unavailable = True
        return None
    return _client


def set_phase(version_id: str, phase: str, detail: str = "") -> None:
    """Publish the pipeline's current phase for `version_id`. Best-effort — never raises."""
    client = _get_client()
    if client is None:
        return
    payload = json.dumps({"phase": phase, "detail": detail, "updated_at": time.time()})
    try:
        client.set(_KEY_PREFIX + version_id, payload, ex=_STATUS_TTL_SECONDS)
    except redis.RedisError as exc:
        _log.warning("status_tracker: failed to set phase for %s: %s", version_id, exc)


def get_phase(version_id: str) -> Optional[dict[str, Any]]:
    """The last published phase for `version_id`, or None if unset/expired/unavailable."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(_KEY_PREFIX + version_id)
    except redis.RedisError as exc:
        _log.warning("status_tracker: failed to read phase for %s: %s", version_id, exc)
        return None
    return json.loads(raw) if raw else None

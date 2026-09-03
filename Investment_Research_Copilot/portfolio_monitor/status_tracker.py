"""
Live job-progress tracking in Redis — separate from Postgres's durable status.

While a monitoring job runs, clients poll a lightweight phase label from Redis
instead of hitting Postgres on every tick. Degrades gracefully when Redis is down.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import redis

from .logconf import get_logger

_log = get_logger()
_STATUS_TTL_SECONDS = 3600
_KEY_PREFIX = "portfolio_monitor:run_status:"

_client: Optional[redis.Redis] = None
_unavailable = False


def _get_client() -> Optional[redis.Redis]:
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
        _log.warning(
            "status_tracker: Redis unavailable (%s) — live phase updates disabled.",
            exc,
        )
        _unavailable = True
        return None
    return _client


def set_phase(portfolio_id: str, phase: str, detail: str = "") -> None:
    client = _get_client()
    if client is None:
        return
    payload = json.dumps({"phase": phase, "detail": detail, "updated_at": time.time()})
    try:
        client.set(_KEY_PREFIX + portfolio_id, payload, ex=_STATUS_TTL_SECONDS)
    except redis.RedisError as exc:
        _log.warning("status_tracker: failed to set phase for %s: %s", portfolio_id, exc)


def get_phase(portfolio_id: str) -> Optional[dict[str, Any]]:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(_KEY_PREFIX + portfolio_id)
    except redis.RedisError as exc:
        _log.warning("status_tracker: failed to read phase for %s: %s", portfolio_id, exc)
        return None
    return json.loads(raw) if raw else None

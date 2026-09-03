"""
Logging configuration for the credit-risk pipeline.

A single named logger (`credit_risk`) writes **structured JSON** lines to stderr, and —
when `CREDIT_RISK_LOG_FILE` is set — also appends them to that file. Every line also
carries the active OpenTelemetry `trace_id`/`span_id` when one exists (see
`credit_risk/tracing.py`), so a slow request's trace and the log lines emitted while
handling it can be correlated directly instead of matched up by eye on a timestamp.

This is where the data reconciliation is recorded: what the documents covered, what was
fetched from yfinance, where the two disagree, and which ratio inputs are still missing
after the fallback.

Env vars
--------
* `CREDIT_RISK_LOG_LEVEL` — DEBUG | INFO | WARNING | ERROR (default INFO).
* `CREDIT_RISK_LOG_FILE`  — optional path; logs are appended here as well as stderr.
"""
from __future__ import annotations

import logging
import os
import sys

from opentelemetry import trace
from pythonjsonlogger.json import JsonFormatter

_CONFIGURED = False
_LOGGER_NAME = "credit_risk"

# %(asctime)s etc. select which LogRecord attributes JsonFormatter includes; the dict
# renames them to the field names clean-code's own logging guidance asks for (a
# consistent `timestamp`/`level`/`logger` shape instead of stdlib's raw attribute names).
_FIELD_FORMAT = "%(asctime)s %(levelname)s %(name)s %(module)s %(lineno)d %(message)s"
_RENAME_FIELDS = {"asctime": "timestamp", "levelname": "level", "name": "logger", "lineno": "line"}


class _TraceAwareJsonFormatter(JsonFormatter):
    """Stamps the active OpenTelemetry span's trace/span id onto every record, if one
    exists — `get_current_span()` always returns a (no-op, invalid) span when tracing
    isn't configured or no request is in flight, so this is a no-op outside a traced
    request rather than something that needs its own on/off switch."""

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = _LOGGER_NAME
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            log_record["trace_id"] = format(span_context.trace_id, "032x")
            log_record["span_id"] = format(span_context.span_id, "016x")


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Return the package logger, configuring handlers once."""
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    if not _CONFIGURED:
        level_name = (os.getenv("CREDIT_RISK_LOG_LEVEL", "INFO") or "INFO").upper()
        logger.setLevel(getattr(logging, level_name, logging.INFO))

        fmt = _TraceAwareJsonFormatter(fmt=_FIELD_FORMAT, rename_fields=_RENAME_FIELDS)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        log_file = os.getenv("CREDIT_RISK_LOG_FILE")
        if log_file:
            try:
                fh = logging.FileHandler(log_file, encoding="utf-8")
                fh.setFormatter(fmt)
                logger.addHandler(fh)
            except OSError as exc:
                logger.warning("could not open log file %r: %s", log_file, exc)

        logger.propagate = False
        _CONFIGURED = True
    return logger

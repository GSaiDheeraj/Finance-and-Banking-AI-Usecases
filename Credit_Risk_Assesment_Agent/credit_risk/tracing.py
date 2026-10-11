"""OpenTelemetry setup — per-request API timing (opt-in).

Tracing is OFF by default: `setup_tracing(app)` returns without installing any
exporter or instrumentation, so no JSON span blobs are printed to the console.
The previous behaviour (BatchSpanProcessor + ConsoleSpanExporter dumping every
span as JSON) was the source of the large `{ "name": "GET ...", ... }` blocks
in the server output.

To re-enable request tracing (e.g. against a real collector/backend), set
`CREDIT_RISK_TRACING=1` — this installs the FastAPI instrumentation only, still
without a console exporter. Point `_build_span_processor()` at an OTLP exporter
for actual collection.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

_SERVICE_NAME = "credit-risk-api"


def _tracing_enabled() -> bool:
    return (os.getenv("CREDIT_RISK_TRACING", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def setup_tracing(app: FastAPI) -> None:
    """Instrument `app` only when CREDIT_RISK_TRACING=1. Otherwise a no-op."""
    if not _tracing_enabled():
        return
    FastAPIInstrumentor.instrument_app(app)

"""
OpenTelemetry setup — tracks per-request API timing.

`setup_tracing(app)` does two things:

1. Builds a `TracerProvider` with a `BatchSpanProcessor` exporting to the console. There
   is no real APM backend (Jaeger/Tempo/a vendor) wired into this project yet, so a
   `ConsoleSpanExporter` is the only way to actually *see* a span locally without first
   standing up infrastructure nothing here currently needs — swapping in a real backend
   later is a one-line change to `_build_span_processor()` (an OTLP exporter pointed at a
   collector), not a redesign, which is the entire point of OpenTelemetry's
   exporter/processor split. `BatchSpanProcessor` (vs. `SimpleSpanProcessor`) exports
   spans from a background worker thread rather than synchronously inline with the
   request — the right choice even for local dev, since a `Simple` processor would add
   the exporter's own I/O latency to every single request it's supposed to be measuring.
2. Calls `FastAPIInstrumentor.instrument_app(app)`, which wraps the app in ASGI
   middleware that opens one span per incoming request and closes it when the response
   is sent — capturing method, route, status code, and wall-clock duration automatically.
   This is a request-scoped instrumentation library, not a decorator applied per route:
   API timing is identical in shape across all 8 endpoints (time-in, time-out), so
   instrumenting the one place every request already passes through (the ASGI layer)
   avoids repeating the same span-creation boilerplate in every route function — the
   FastAPI/Starlette equivalent of a Decorator wrapping "the whole app" instead of "one
   function at a time."

`credit_risk/logconf.py`'s formatter reads the *active* span's trace/span id via
`opentelemetry.trace.get_current_span()` on every log line, so nothing here needs to pass
IDs around explicitly — the SDK ties spans and logs together through the same
context-local "current span" both `logconf.py` and the FastAPI instrumentation itself
read from.
"""
from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_SERVICE_NAME = "credit-risk-api"


def _build_span_processor() -> BatchSpanProcessor:
    # Swap ConsoleSpanExporter for an OTLP exporter (opentelemetry-exporter-otlp) here
    # once a real collector/backend exists — everything else in this module is unchanged.
    return BatchSpanProcessor(ConsoleSpanExporter())


def setup_tracing(app: FastAPI) -> None:
    """Configure the global tracer provider and instrument `app`. Call once, at startup."""
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: _SERVICE_NAME}))
    provider.add_span_processor(_build_span_processor())
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)

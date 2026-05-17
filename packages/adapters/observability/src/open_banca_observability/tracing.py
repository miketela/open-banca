"""OTel tracer setup for open-banca services.

Usage::

    from open_banca_observability.tracing import setup_tracer

    tracer = setup_tracer("api")
    with tracer.start_as_current_span("my-operation") as span:
        span.set_attribute("bank_id", "banco_general")

Environment variables:

    OPEN_BANCA_OTEL_ENABLED       "true" to enable (default: false / no-op)
    OTEL_EXPORTER_OTLP_ENDPOINT   gRPC endpoint (default: http://localhost:4317)
    OTEL_SERVICE_NAME             override service name attribute

ADR-0020: ``RedactingSpanExporter`` wraps the OTLP exporter so that canary
values and PII patterns are scrubbed from every span before export.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Tracer

from open_banca_observability.redact_processor import RedactingSpanExporter

_NOOP_TRACER: Tracer | None = None


def _otel_enabled() -> bool:
    return os.environ.get("OPEN_BANCA_OTEL_ENABLED", "").lower() in {"1", "true", "yes"}


def _build_otlp_exporter() -> SpanExporter:
    """Build the OTLP gRPC exporter.  Import is deferred so the package remains
    importable even when grpcio/otlp-exporter is not installed in minimal envs.
    """
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,  # type: ignore[import-untyped]
    )

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    return OTLPSpanExporter(endpoint=endpoint, insecure=not endpoint.startswith("https"))


def setup_tracer(service_name: str) -> Tracer:
    """Configure a TracerProvider for *service_name* and return a Tracer.

    When ``OPEN_BANCA_OTEL_ENABLED`` is falsy (the default), a no-op tracer is
    returned so callers need no conditional guards.

    The OTLP exporter is wrapped in ``RedactingSpanExporter`` to guarantee
    that canary values and PII are stripped before spans leave the process.
    (REQ-016 / ADR-0020).

    Args:
        service_name: Logical service identifier (e.g. ``"api"``, ``"orchestrator"``).

    Returns:
        An OTel :class:`~opentelemetry.trace.Tracer` instance.
    """
    if not _otel_enabled():
        return trace.get_tracer(service_name)

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Wrap the OTLP exporter in the redact layer (ADR-0020 gate).
    raw_exporter = _build_otlp_exporter()
    safe_exporter = RedactingSpanExporter(inner=raw_exporter)
    provider.add_span_processor(BatchSpanProcessor(safe_exporter))

    trace.set_tracer_provider(provider)
    return provider.get_tracer(service_name)

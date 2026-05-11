"""open-banca: Observability adapter — OTel, Langfuse, and redact filter."""

from open_banca_observability.langfuse_client import flush_langfuse, get_langfuse_client
from open_banca_observability.metrics import CoreInstruments, get_instruments, setup_meter
from open_banca_observability.propagation import (
    extract_context,
    get_current_span_id,
    get_current_trace_id,
    inject_headers,
    start_activity_span,
)
from open_banca_observability.redact import RedactConfig, RedactFilter, RedactStream
from open_banca_observability.redact_processor import RedactingSpanExporter, RedactingSpanProcessor
from open_banca_observability.tracing import setup_tracer

__all__ = [
    # redact
    "RedactConfig",
    "RedactFilter",
    "RedactStream",
    # redact exporter
    "RedactingSpanExporter",
    "RedactingSpanProcessor",
    # tracing
    "setup_tracer",
    # metrics
    "setup_meter",
    "get_instruments",
    "CoreInstruments",
    # propagation
    "extract_context",
    "inject_headers",
    "get_current_trace_id",
    "get_current_span_id",
    "start_activity_span",
    # langfuse
    "get_langfuse_client",
    "flush_langfuse",
]

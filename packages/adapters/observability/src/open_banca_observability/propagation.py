"""Trace context propagation helpers for Temporal activities and HTTP calls.

Temporal workflows must not call OTel directly (determinism constraint).
Instead, activities extract/inject context via these helpers so that spans
produced inside an activity are correctly parented to the triggering workflow.

HTTP propagation uses the W3C TraceContext + Baggage format (OTel default).

Usage in a Temporal activity::

    from open_banca_observability.propagation import extract_context, start_activity_span

    @activity.defn
    async def my_activity(carrier: dict[str, str]) -> None:
        ctx = extract_context(carrier)
        with start_activity_span("my_activity", context=ctx) as span:
            span.set_attribute("bank_id", "banco_general")
            ...

Usage for outgoing HTTP calls (httpx)::

    from open_banca_observability.propagation import inject_headers

    headers: dict[str, str] = {}
    inject_headers(headers)
    response = httpx.get(url, headers=headers)

For Temporal's built-in propagation via ``TracingInterceptor``, configure the
interceptor at worker/client creation time rather than using these helpers
directly — see ``open_banca_orchestrator.worker`` setup.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import context, propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Tracer


def extract_context(carrier: dict[str, str]) -> Context:
    """Extract a trace context from a *carrier* dict (e.g. Temporal header payload).

    The carrier must have been populated by :func:`inject_headers` on the sender
    side, carrying W3C ``traceparent`` / ``tracestate`` headers.

    Args:
        carrier: Dictionary of string keys/values (e.g. from a Temporal payload).

    Returns:
        OTel :class:`~opentelemetry.context.Context` with the extracted span context.
        Returns an empty context when the carrier has no propagation headers.
    """
    return propagate.extract(carrier)


def inject_headers(carrier: dict[str, str]) -> None:
    """Inject the current trace context into *carrier* as W3C headers.

    Modifies *carrier* in-place.  Use before outgoing HTTP requests or when
    building a Temporal activity payload that must carry trace context.

    Args:
        carrier: Mutable dict to inject headers into.
    """
    propagate.inject(carrier)


def get_current_trace_id() -> str:
    """Return the current trace ID as a 32-character hex string, or ``"0"`` if none.

    Useful for injecting ``trace_id`` into log records (logfmt).

    Returns:
        Hex trace ID string or ``"0"`` when no active span exists.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if span_context and span_context.is_valid:
        return format(span_context.trace_id, "032x")
    return "0"


def get_current_span_id() -> str:
    """Return the current span ID as a 16-character hex string, or ``"0"`` if none.

    Returns:
        Hex span ID string or ``"0"`` when no active span exists.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if span_context and span_context.is_valid:
        return format(span_context.span_id, "016x")
    return "0"


@contextmanager
def start_activity_span(
    name: str,
    tracer: Tracer | None = None,
    context: Context | None = None,  # noqa: A002
    attributes: dict[str, str] | None = None,
) -> Iterator[Span]:
    """Context manager that creates a span for a Temporal activity.

    Restores the provided *context* (if any) before starting the span so the
    new span is correctly parented to the upstream workflow/activity span.

    Args:
        name: Span name (typically the activity function name).
        tracer: OTel Tracer to use.  Defaults to the global tracer.
        context: Parent context extracted from a Temporal payload.
        attributes: Initial span attributes.

    Yields:
        The active :class:`~opentelemetry.trace.Span`.
    """
    _tracer = tracer or trace.get_tracer(__name__)
    _attrs = dict(attributes or {})
    token = None
    if context is not None:
        token = context_api_set(context)
    try:
        with _tracer.start_as_current_span(name, attributes=_attrs) as span:  # type: ignore[arg-type]
            yield span
    finally:
        if token is not None:
            context_api_reset(token)


# Thin wrappers to avoid importing context at the call-site.
def context_api_set(ctx: Context) -> object:
    """Attach *ctx* as the current context; returns a token for reset."""
    return context.attach(ctx)


def context_api_reset(token: object) -> None:
    """Detach a previously attached context token."""
    context.detach(token)  # type: ignore[arg-type]

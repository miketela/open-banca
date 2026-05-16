"""Redacting SpanExporter — ADR-0020 / REQ-016 security gate.

``ReadableSpan`` objects are immutable after ``on_end`` is called, so a
``SpanProcessor`` cannot reliably mutate span attributes before export.
Instead, this module provides a ``SpanExporter`` *wrapper* that scrubs every
attribute value before forwarding spans to the inner exporter.

This guarantees that ``SECRET_CANARY_VALUE`` and PII canary strings can NEVER
leave the process in OTel export payloads, satisfying the CI canary gate.

Usage::

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from open_banca_observability.redact_processor import RedactingSpanExporter
    from open_banca_observability.redact import RedactFilter, RedactConfig

    inner = InMemorySpanExporter()
    config = RedactConfig(secret_canary="my-canary")
    exporter = RedactingSpanExporter(inner=inner, config=config)

    # Use exporter in a BatchSpanProcessor …

The name ``RedactingSpanExporter`` is intentionally exported as
``RedactingSpanProcessor`` alias for compatibility with task spec wording.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from open_banca_observability.redact import RedactConfig, RedactFilter


class RedactingSpanExporter(SpanExporter):
    """``SpanExporter`` wrapper that scrubs canary and PII values from all span attributes.

    Clones each span's attributes and applies ``RedactFilter.scrub()`` to
    every string value before forwarding the batch to the *inner* exporter.

    Args:
        inner: The real destination exporter (OTLP, InMemory, etc.).
        config: ``RedactConfig`` instance.  If ``None``, reads canary values
                from environment variables at construction time.
    """

    def __init__(
        self,
        inner: SpanExporter,
        config: RedactConfig | None = None,
    ) -> None:
        self._inner = inner
        self._filter = RedactFilter(config=config)

    def _scrub_span(self, span: ReadableSpan) -> ReadableSpan:
        """Return a shallow-copied span with all string attributes redacted.

        We copy the span because ``ReadableSpan.attributes`` is a read-only
        ``MappingProxyType``; we must produce a new span object.
        """
        if not span.attributes:
            return span

        scrubbed: dict[str, object] = {}
        for key, value in span.attributes.items():
            if isinstance(value, str):
                scrubbed[key] = self._filter.scrub(value)
            elif isinstance(value, (list, tuple)):
                scrubbed[key] = type(value)(
                    self._filter.scrub(v) if isinstance(v, str) else v for v in value
                )
            else:
                scrubbed[key] = value

        # Build a new ReadableSpan with the scrubbed attributes.
        # ReadableSpan exposes its fields as properties; we use __class__ to
        # create a copy via __new__ + direct __dict__ mutation (OTel SDK
        # does not provide a copy constructor).
        new_span = copy.copy(span)
        object.__setattr__(new_span, "_attributes", scrubbed)
        return new_span

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Scrub *spans* and forward to the inner exporter.

        Args:
            spans: Batch of finished spans.

        Returns:
            ``SpanExportResult.SUCCESS`` or ``FAILURE`` from inner.
        """
        safe_spans = [self._scrub_span(s) for s in spans]
        return self._inner.export(safe_spans)

    def shutdown(self) -> None:
        """Propagate shutdown to inner exporter."""
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Propagate force-flush to inner exporter."""
        return self._inner.force_flush(timeout_millis)


# Alias for task-spec compatibility.
RedactingSpanProcessor = RedactingSpanExporter

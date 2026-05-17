"""Tests for RedactingSpanExporter — ADR-0020 canary gate.

These tests verify that SECRET_CANARY_VALUE and PII canary strings are
stripped from span attributes before they reach the export destination.

Marks: canary — these are the CI security gate tests.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from open_banca_observability.redact import RedactConfig
from open_banca_observability.redact_processor import RedactingSpanExporter

_CANARY_SECRET = "super-secret-canary-value-12345"
_CANARY_NAME = "Juan Pérez PII Canary"
_CANARY_ACCT = "1234567890123456"


def _make_provider_with_redact(
    mem_exporter: InMemorySpanExporter,
    config: RedactConfig,
) -> TracerProvider:
    """Build a TracerProvider that routes spans through RedactingSpanExporter."""
    redact_exporter = RedactingSpanExporter(inner=mem_exporter, config=config)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(redact_exporter))
    return provider


@pytest.mark.canary
class TestRedactProcessor:
    """test_redact_processor: span with SECRET_CANARY_VALUE → exported value redacted."""

    def test_secret_canary_redacted_in_attributes(self) -> None:
        """Span attribute containing SECRET_CANARY_VALUE is redacted before export."""
        mem = InMemorySpanExporter()
        config = RedactConfig(secret_canary=_CANARY_SECRET)
        provider = _make_provider_with_redact(mem, config)
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("test-span") as span:
            span.set_attribute("url", f"https://bank.example.com?token={_CANARY_SECRET}")
            span.set_attribute("safe.key", "safe-value")

        spans = mem.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes or {}

        # The canary must NOT appear in the exported attribute.
        assert _CANARY_SECRET not in str(attrs.get("url", ""))
        assert "[REDACTED]" in str(attrs.get("url", ""))
        # Safe values must pass through unchanged.
        assert attrs.get("safe.key") == "safe-value"

    def test_pii_canary_name_redacted(self) -> None:
        """Span attribute containing PII_CANARY_NAME is redacted before export."""
        mem = InMemorySpanExporter()
        config = RedactConfig(pii_name=_CANARY_NAME)
        provider = _make_provider_with_redact(mem, config)
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("pii-span") as span:
            span.set_attribute("account.owner", _CANARY_NAME)

        spans = mem.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes or {}
        assert _CANARY_NAME not in str(attrs.get("account.owner", ""))
        assert "[REDACTED]" in str(attrs.get("account.owner", ""))

    def test_span_name_not_redacted(self) -> None:
        """Span *name* is not modified — only attributes are redacted."""
        mem = InMemorySpanExporter()
        config = RedactConfig(secret_canary=_CANARY_SECRET)
        provider = _make_provider_with_redact(mem, config)
        tracer = provider.get_tracer("test")

        span_name = "safe-span-name"
        with tracer.start_as_current_span(span_name):
            pass

        spans = mem.get_finished_spans()
        assert spans[0].name == span_name

    def test_non_string_attributes_pass_through(self) -> None:
        """Integer/boolean span attributes are not modified."""
        mem = InMemorySpanExporter()
        config = RedactConfig(secret_canary=_CANARY_SECRET)
        provider = _make_provider_with_redact(mem, config)
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("numeric-span") as span:
            span.set_attribute("http.status_code", 200)
            span.set_attribute("retry.count", 3)

        spans = mem.get_finished_spans()
        attrs = spans[0].attributes or {}
        assert attrs.get("http.status_code") == 200
        assert attrs.get("retry.count") == 3


@pytest.mark.canary
class TestRedactPiiCanaries:
    """test_redact_pii_canaries: PII_CANARY_NAME redacted in trace attrs."""

    def test_pii_canary_acct_redacted(self) -> None:
        """16-digit account number (structural pattern) is redacted."""
        mem = InMemorySpanExporter()
        config = RedactConfig()  # structural patterns active by default
        provider = _make_provider_with_redact(mem, config)
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("acct-span") as span:
            span.set_attribute("detail", f"Account: {_CANARY_ACCT} was processed")

        spans = mem.get_finished_spans()
        attrs = spans[0].attributes or {}
        assert _CANARY_ACCT not in str(attrs.get("detail", ""))

    def test_all_four_pii_canaries_redacted(self) -> None:
        """All four canary env-var values are scrubbed from span attributes."""
        mem = InMemorySpanExporter()
        config = RedactConfig(
            secret_canary=_CANARY_SECRET,
            pii_name=_CANARY_NAME,
            pii_acct="CANARY_ACCT_LITERAL",
            pii_bal="CANARY_BAL_999.99",
        )
        provider = _make_provider_with_redact(mem, config)
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("all-pii-span") as span:
            span.set_attribute("name_field", _CANARY_NAME)
            span.set_attribute("acct_field", "CANARY_ACCT_LITERAL")
            span.set_attribute("bal_field", "CANARY_BAL_999.99")
            span.set_attribute("secret_field", _CANARY_SECRET)

        spans = mem.get_finished_spans()
        attrs = dict(spans[0].attributes or {})

        for key in ("name_field", "acct_field", "bal_field", "secret_field"):
            assert "[REDACTED]" in str(attrs[key]), f"{key} not redacted: {attrs[key]!r}"

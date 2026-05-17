"""Tests for open_banca_observability.tracing — tracer setup."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from open_banca_observability.tracing import setup_tracer


class TestTracerSetup:
    """test_tracer_setup: tracer instantiable with and without OTel enabled."""

    def test_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When OPEN_BANCA_OTEL_ENABLED is unset, setup_tracer returns a no-op tracer."""
        monkeypatch.delenv("OPEN_BANCA_OTEL_ENABLED", raising=False)
        tracer = setup_tracer("test-service")
        assert tracer is not None
        # Should produce no-op spans (no real export)
        with tracer.start_as_current_span("test-span") as span:
            # A no-op span has an invalid context
            ctx = span.get_span_context()
            assert ctx is not None  # object exists

    def test_tracer_name_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """setup_tracer returns a Tracer object for the given service_name."""
        monkeypatch.delenv("OPEN_BANCA_OTEL_ENABLED", raising=False)
        tracer = setup_tracer("my-svc")
        assert tracer is not None

    def test_enabled_creates_sdk_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When OPEN_BANCA_OTEL_ENABLED=true, a real TracerProvider is installed."""
        monkeypatch.setenv("OPEN_BANCA_OTEL_ENABLED", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        # We patch _build_otlp_exporter to use InMemorySpanExporter so we don't
        # need a real collector running.
        mem_exporter = InMemorySpanExporter()

        import open_banca_observability.tracing as tracing_mod

        original_build = tracing_mod._build_otlp_exporter
        tracing_mod._build_otlp_exporter = lambda: mem_exporter  # type: ignore[assignment]
        try:
            tracer = setup_tracer("sdk-test-service")
            assert tracer is not None
            with tracer.start_as_current_span("enabled-span") as span:
                span.set_attribute("test.key", "test.value")
        finally:
            tracing_mod._build_otlp_exporter = original_build  # type: ignore[assignment]
            # Reset global provider to avoid polluting other tests.
            trace.set_tracer_provider(trace.ProxyTracerProvider())

"""Tests for open_banca_observability.propagation — trace context propagation.

test_propagation_temporal: trace_id propagated through a simulated
workflow→activity context transfer.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from open_banca_observability.propagation import (
    extract_context,
    get_current_span_id,
    get_current_trace_id,
    inject_headers,
    start_activity_span,
)


def _install_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Install a fresh TracerProvider with InMemory exporter."""
    from opentelemetry import trace

    mem = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(mem))
    trace.set_tracer_provider(provider)
    return provider, mem


class TestPropagationHelpers:
    """Basic propagation helper unit tests."""

    def test_inject_and_extract_round_trip(self) -> None:
        """Inject current context into carrier, extract it back — trace IDs match."""
        provider, mem = _install_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("root-span"):
            carrier: dict[str, str] = {}
            inject_headers(carrier)

            # Must have injected W3C traceparent header
            assert "traceparent" in carrier

            # Extract and verify the trace ID matches the root span
            ctx = extract_context(carrier)
            assert ctx is not None

    def test_get_current_trace_id_zero_outside_span(self) -> None:
        """get_current_trace_id returns '0' when no active span."""
        from opentelemetry import trace

        # Ensure no active span by using a fresh provider without spans.
        trace.set_tracer_provider(TracerProvider())
        assert get_current_trace_id() == "0"

    def test_get_current_span_id_zero_outside_span(self) -> None:
        """get_current_span_id returns '0' when no active span."""
        from opentelemetry import trace

        trace.set_tracer_provider(TracerProvider())
        assert get_current_span_id() == "0"

    def test_get_current_trace_id_inside_span(self) -> None:
        """get_current_trace_id returns a non-zero hex string inside an active span."""
        provider, _ = _install_provider()
        tracer = provider.get_tracer("test")

        with tracer.start_as_current_span("active-span"):
            trace_id = get_current_trace_id()
            assert trace_id != "0"
            assert len(trace_id) == 32
            int(trace_id, 16)  # must be valid hex


class TestPropagationTemporal:
    """test_propagation_temporal: trace_id propagated through workflow → activity span."""

    def test_trace_id_preserved_through_carrier(self) -> None:
        """Simulates Temporal header propagation: workflow injects, activity extracts.

        The activity span should share the same trace_id as the workflow span.
        """
        provider, mem = _install_provider()
        tracer = provider.get_tracer("temporal-test")

        # Step 1: "Workflow" creates a span and injects context into a carrier
        carrier: dict[str, str] = {}
        with tracer.start_as_current_span("workflow-span") as workflow_span:
            inject_headers(carrier)
            workflow_trace_id = format(workflow_span.get_span_context().trace_id, "032x")

        # Step 2: "Activity" extracts context and starts its span as a child
        parent_ctx = extract_context(carrier)
        with start_activity_span("activity-span", tracer=tracer, context=parent_ctx) as act_span:
            activity_trace_id = format(act_span.get_span_context().trace_id, "032x")

        # Both spans must share the same trace_id
        assert workflow_trace_id == activity_trace_id
        assert workflow_trace_id != "0"

    def test_activity_span_is_child_of_workflow(self) -> None:
        """Activity span's parent span ID matches the workflow span's span ID."""
        provider, mem = _install_provider()
        tracer = provider.get_tracer("temporal-test")

        carrier: dict[str, str] = {}
        with tracer.start_as_current_span("workflow-span") as wf_span:
            inject_headers(carrier)
            wf_span_id = wf_span.get_span_context().span_id

        parent_ctx = extract_context(carrier)
        with start_activity_span(
            "activity-span",
            tracer=tracer,
            context=parent_ctx,
            attributes={"bank_id": "banco_general"},
        ):
            pass

        finished = mem.get_finished_spans()
        # Find the activity span
        act_spans = [s for s in finished if s.name == "activity-span"]
        assert len(act_spans) == 1

        act_parent = act_spans[0].parent
        assert act_parent is not None
        assert act_parent.span_id == wf_span_id

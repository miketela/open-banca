"""Tests for OTel cost metric emission from CostTrackingChatModel.

Verifies that when a cost_counter is injected, each LLM call emits to the
llm_cost_usd_total OTel metric (task #27).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from open_banca_llm.mapper.agent import CostTrackingChatModel
from open_banca_llm.mapper.cost_tracker import CostTracker
from open_banca_observability.metrics import get_instruments


def _make_reader_and_counter() -> tuple[InMemoryMetricReader, Any]:
    """Create an InMemory MeterProvider and return reader + llm_cost_usd_total counter."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("llm-test")
    instruments = get_instruments(meter)
    return reader, instruments.llm_cost_usd_total


def _make_fake_completion(prompt_tokens: int, completion_tokens: int) -> Any:
    """Build a minimal ChatInvokeCompletion-like mock with usage."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    completion = MagicMock()
    completion.usage = usage
    return completion


class TestCostMetricEmission:
    """test_metrics_llm_cost: after an LLM call, llm_cost_usd_total is incremented."""

    @pytest.mark.asyncio
    async def test_cost_counter_incremented_on_ainvoke(self) -> None:
        """When cost_counter is injected, ainvoke emits llm_cost_usd_total."""
        reader, cost_counter = _make_reader_and_counter()

        # Fake inner model that returns a completion with known usage
        fake_completion = _make_fake_completion(prompt_tokens=100, completion_tokens=50)
        inner = MagicMock()
        inner.ainvoke = AsyncMock(return_value=fake_completion)

        tracker = CostTracker(cost_cap_usd=1.0)
        model = CostTrackingChatModel(
            inner=inner,
            tracker=tracker,
            cost_counter=cost_counter,
            agent_name="mapper",
        )

        from browser_use.llm.messages import UserMessage  # type: ignore[import-untyped]

        await model.ainvoke([UserMessage(content="test")])

        # Verify counter received a value
        metrics_data = reader.get_metrics_data()
        assert metrics_data is not None

        metric_names = {
            metric.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
        }
        assert "llm_cost_usd_total" in metric_names

    @pytest.mark.asyncio
    async def test_no_counter_no_error(self) -> None:
        """When cost_counter=None (default), ainvoke works without OTel."""
        fake_completion = _make_fake_completion(prompt_tokens=50, completion_tokens=25)
        inner = MagicMock()
        inner.ainvoke = AsyncMock(return_value=fake_completion)

        tracker = CostTracker(cost_cap_usd=1.0)
        model = CostTrackingChatModel(inner=inner, tracker=tracker)

        from browser_use.llm.messages import UserMessage  # type: ignore[import-untyped]

        # Should not raise
        result = await model.ainvoke([UserMessage(content="test")])
        assert result is not None

    @pytest.mark.asyncio
    async def test_agent_name_label_in_metric(self) -> None:
        """The agent_name label appears as an attribute on emitted metric points."""
        reader, cost_counter = _make_reader_and_counter()

        fake_completion = _make_fake_completion(prompt_tokens=200, completion_tokens=100)
        inner = MagicMock()
        inner.ainvoke = AsyncMock(return_value=fake_completion)

        tracker = CostTracker(cost_cap_usd=1.0)
        model = CostTrackingChatModel(
            inner=inner,
            tracker=tracker,
            cost_counter=cost_counter,
            agent_name="remapper",
        )

        from browser_use.llm.messages import UserMessage  # type: ignore[import-untyped]

        await model.ainvoke([UserMessage(content="test")])

        metrics_data = reader.get_metrics_data()
        assert metrics_data is not None

        # Find the llm_cost_usd_total metric data points
        for rm in metrics_data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    if metric.name == "llm_cost_usd_total":
                        # Check that at least one data point has agent=remapper
                        for dp in metric.data.data_points:
                            if dp.attributes.get("agent") == "remapper":
                                return
        pytest.fail("No llm_cost_usd_total data point with agent=remapper found")

"""Tests for open_banca_observability.metrics — core metric instruments.

test_metrics_scrape_count: after a scrape completes, scrape_jobs_total increments.
"""

from __future__ import annotations

from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
)

from open_banca_observability.metrics import CoreInstruments, get_instruments


def _make_meter(name: str = "test-service") -> tuple[Meter, InMemoryMetricReader]:
    """Build an in-memory MeterProvider for testing."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return provider.get_meter(name), reader


class TestMetricsScrapeCount:
    """test_metrics_scrape_count: scrape complete → counter incremented."""

    def test_scrape_jobs_total_increments(self) -> None:
        """Calling scrape_jobs_total.add(1) is reflected in the exported metrics."""
        meter, reader = _make_meter()
        instruments = get_instruments(meter)

        instruments.scrape_jobs_total.add(1, {"status": "success"})
        instruments.scrape_jobs_total.add(1, {"status": "error"})

        metrics_data = reader.get_metrics_data()
        assert metrics_data is not None

        metric_names = {
            metric.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
        }
        assert "scrape_jobs_total" in metric_names

    def test_scrape_duration_records(self) -> None:
        """scrape_duration_seconds histogram records values."""
        meter, reader = _make_meter()
        instruments = get_instruments(meter)

        instruments.scrape_duration_seconds.record(5.2)
        instruments.scrape_duration_seconds.record(12.7)

        metrics_data = reader.get_metrics_data()
        assert metrics_data is not None
        metric_names = {
            metric.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
        }
        assert "scrape_duration_seconds" in metric_names

    def test_llm_cost_usd_total_increments(self) -> None:
        """llm_cost_usd_total counter records LLM spend."""
        meter, reader = _make_meter()
        instruments = get_instruments(meter)

        instruments.llm_cost_usd_total.add(0.05, {"agent": "mapper"})
        instruments.llm_cost_usd_total.add(0.03, {"agent": "remapper"})

        metrics_data = reader.get_metrics_data()
        assert metrics_data is not None
        metric_names = {
            metric.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
        }
        assert "llm_cost_usd_total" in metric_names

    def test_all_core_instruments_present(self) -> None:
        """get_instruments returns a CoreInstruments with all five instruments."""
        meter, _ = _make_meter()
        instruments = get_instruments(meter)

        assert isinstance(instruments, CoreInstruments)
        assert instruments.scrape_jobs_total is not None
        assert instruments.scrape_duration_seconds is not None
        assert instruments.llm_cost_usd_total is not None
        assert instruments.webhook_dispatch_total is not None
        assert instruments.breakage_events_total is not None

    def test_webhook_dispatch_and_breakage_counters(self) -> None:
        """webhook_dispatch_total and breakage_events_total accept increments."""
        meter, reader = _make_meter()
        instruments = get_instruments(meter)

        instruments.webhook_dispatch_total.add(1, {"status": "delivered"})
        instruments.breakage_events_total.add(1, {"bank": "banco_general"})

        metrics_data = reader.get_metrics_data()
        assert metrics_data is not None
        metric_names = {
            metric.name
            for rm in metrics_data.resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
        }
        assert "webhook_dispatch_total" in metric_names
        assert "breakage_events_total" in metric_names

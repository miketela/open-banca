"""OTel meter setup and core metric instruments for open-banca services.

Core metrics:

    scrape_jobs_total{status}           Counter — scrape job completions.
    scrape_duration_seconds             Histogram — end-to-end scrape latency.
    llm_cost_usd_total{agent}           Counter — cumulative LLM spend in USD.
    webhook_dispatch_total{status}      Counter — webhook delivery outcomes.
    breakage_events_total{bank}         Counter — bank-site breakage detections.

Usage::

    from open_banca_observability.metrics import setup_meter, get_instruments

    meter = setup_meter("orchestrator")
    instruments = get_instruments(meter)
    instruments.scrape_jobs_total.add(1, {"status": "success"})
    instruments.scrape_duration_seconds.record(12.3)

Environment variables:

    OPEN_BANCA_OTEL_ENABLED   "true" to enable (default: false / no-op)
    OTEL_EXPORTER_OTLP_ENDPOINT  gRPC endpoint (default: http://localhost:4317)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter


def _otel_enabled() -> bool:
    return os.environ.get("OPEN_BANCA_OTEL_ENABLED", "").lower() in {"1", "true", "yes"}


def setup_meter(service_name: str) -> Meter:
    """Configure a MeterProvider for *service_name* and return a Meter.

    When ``OPEN_BANCA_OTEL_ENABLED`` is falsy, a no-op meter is returned.
    When enabled, pushes to an OTLP gRPC endpoint.

    Args:
        service_name: Logical service name (e.g. ``"api"``, ``"orchestrator"``).

    Returns:
        An OTel :class:`~opentelemetry.metrics.Meter`.
    """
    if not _otel_enabled():
        return metrics.get_meter(service_name)

    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,  # type: ignore[import-untyped]
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=not endpoint.startswith("https"),
    )
    reader = PeriodicExportingMetricReader(exporter)
    resource = Resource.create({"service.name": service_name})
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    return provider.get_meter(service_name)


@dataclass(frozen=True)
class CoreInstruments:
    """Pre-created core metric instruments for open-banca services.

    All instruments are lazily obtained from the given *meter*; callers
    should obtain this via :func:`get_instruments` and cache the result.
    """

    scrape_jobs_total: Counter
    scrape_duration_seconds: Histogram
    llm_cost_usd_total: Counter
    webhook_dispatch_total: Counter
    breakage_events_total: Counter


def get_instruments(meter: Meter) -> CoreInstruments:
    """Create all core metric instruments from *meter*.

    Idempotent: calling with the same meter name returns consistent handles.

    Args:
        meter: OTel Meter (from :func:`setup_meter`).

    Returns:
        :class:`CoreInstruments` with all five counters/histograms.
    """
    return CoreInstruments(
        scrape_jobs_total=meter.create_counter(
            name="scrape_jobs_total",
            description="Total scrape job completions partitioned by status",
            unit="1",
        ),
        scrape_duration_seconds=meter.create_histogram(
            name="scrape_duration_seconds",
            description="End-to-end scrape job latency in seconds",
            unit="s",
        ),
        llm_cost_usd_total=meter.create_counter(
            name="llm_cost_usd_total",
            description="Cumulative LLM spend in USD partitioned by agent",
            unit="USD",
        ),
        webhook_dispatch_total=meter.create_counter(
            name="webhook_dispatch_total",
            description="Webhook delivery outcomes partitioned by status",
            unit="1",
        ),
        breakage_events_total=meter.create_counter(
            name="breakage_events_total",
            description="Bank-site breakage detection events partitioned by bank",
            unit="1",
        ),
    )

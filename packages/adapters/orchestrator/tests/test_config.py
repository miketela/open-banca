"""Tests: OrchestratorSettings loads correctly from environment variables."""

import pytest

from open_banca_orchestrator.config import OrchestratorSettings


def test_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings return documented defaults when no env vars are set."""
    # Clear any OPEN_BANCA_ vars that might be set in the test environment
    for key in list(__import__("os").environ.keys()):
        if key.startswith("OPEN_BANCA_TEMPORAL") or key.startswith("OPEN_BANCA_WORKER"):
            monkeypatch.delenv(key, raising=False)

    settings = OrchestratorSettings()
    assert settings.temporal_address == "localhost:7233"
    assert settings.temporal_namespace == "default"
    assert settings.temporal_task_queue == "open-banca-task-queue"
    assert settings.worker_max_concurrent_activities == 10
    assert settings.worker_max_concurrent_workflows == 100


def test_temporal_address_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPEN_BANCA_TEMPORAL_ADDRESS overrides temporal_address."""
    monkeypatch.setenv("OPEN_BANCA_TEMPORAL_ADDRESS", "temporal.internal:7233")
    settings = OrchestratorSettings()
    assert settings.temporal_address == "temporal.internal:7233"


def test_temporal_namespace_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPEN_BANCA_TEMPORAL_NAMESPACE overrides temporal_namespace."""
    monkeypatch.setenv("OPEN_BANCA_TEMPORAL_NAMESPACE", "production")
    settings = OrchestratorSettings()
    assert settings.temporal_namespace == "production"


def test_temporal_task_queue_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPEN_BANCA_TEMPORAL_TASK_QUEUE overrides temporal_task_queue."""
    monkeypatch.setenv("OPEN_BANCA_TEMPORAL_TASK_QUEUE", "open-banca-task-queue-v2")
    settings = OrchestratorSettings()
    assert settings.temporal_task_queue == "open-banca-task-queue-v2"


def test_worker_concurrency_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPEN_BANCA_WORKER_MAX_CONCURRENT_ACTIVITIES and WORKFLOWS override defaults."""
    monkeypatch.setenv("OPEN_BANCA_WORKER_MAX_CONCURRENT_ACTIVITIES", "5")
    monkeypatch.setenv("OPEN_BANCA_WORKER_MAX_CONCURRENT_WORKFLOWS", "50")
    settings = OrchestratorSettings()
    assert settings.worker_max_concurrent_activities == 5
    assert settings.worker_max_concurrent_workflows == 50


def test_case_insensitive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """pydantic-settings is case-insensitive for env var names."""
    monkeypatch.setenv("OPEN_BANCA_TEMPORAL_NAMESPACE", "staging")
    settings = OrchestratorSettings()
    assert settings.temporal_namespace == "staging"

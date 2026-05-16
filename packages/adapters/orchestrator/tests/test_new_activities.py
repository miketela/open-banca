"""Tests for new Phase 1 activities: spawn_sandbox, cleanup_sandbox, persist_result, list_accounts."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_banca_orchestrator.activities.list_accounts import (
    AccountInfo,
    ListAccountsInput,
    ListAccountsResult,
    _extract_accounts,
    list_accounts,
)


def _inject_sandbox_mock() -> tuple[MagicMock, MagicMock]:
    """Inject a mock open_banca_sandbox module into sys.modules."""
    sandbox_mod = types.ModuleType("open_banca_sandbox")
    runner_mod = types.ModuleType("open_banca_sandbox.runner")
    mock_cls = MagicMock()
    runner_mod.DockerSandboxRunner = mock_cls  # type: ignore[attr-defined]
    sandbox_mod.runner = runner_mod  # type: ignore[attr-defined]
    sys.modules["open_banca_sandbox"] = sandbox_mod
    sys.modules["open_banca_sandbox.runner"] = runner_mod
    return mock_cls, sandbox_mod  # type: ignore[return-value]


class TestSpawnSandbox:
    """SpawnSandboxActivity tests with mocked Docker runner."""

    @pytest.mark.asyncio
    async def test_spawn_returns_container_id(self) -> None:
        from open_banca_domain.ports.sandbox_port import SandboxToken
        from open_banca_orchestrator.activities.spawn_sandbox import (
            SpawnSandboxInput,
            SpawnSandboxResult,
            spawn_sandbox,
        )

        mock_token = SandboxToken(
            container_id="abc123",
            container_ip="172.18.0.2",
            network_name="banca-job1",
            sidecar_socket_path="/run/banca/sidecar.sock",
        )
        mock_cls, _ = _inject_sandbox_mock()
        mock_runner = MagicMock()
        mock_runner.spawn.return_value = mock_token
        mock_cls.return_value = mock_runner

        result = await spawn_sandbox(
            SpawnSandboxInput(job_id="job-1", bank_id="banco_general")
        )

        assert isinstance(result, SpawnSandboxResult)
        assert result.container_id == "abc123"
        assert result.sidecar_socket_path == "/run/banca/sidecar.sock"
        mock_runner.spawn.assert_called_once_with(job_id="job-1", bank_id="banco_general")


class TestCleanupSandbox:
    """CleanupSandboxActivity tests with mocked Docker runner."""

    @pytest.mark.asyncio
    async def test_cleanup_success(self) -> None:
        from open_banca_orchestrator.activities.cleanup_sandbox import (
            CleanupSandboxInput,
            CleanupSandboxResult,
            cleanup_sandbox,
        )

        mock_cls, _ = _inject_sandbox_mock()
        mock_runner = MagicMock()
        mock_runner.kill.return_value = None
        mock_cls.return_value = mock_runner

        result = await cleanup_sandbox(
            CleanupSandboxInput(container_id="abc123")
        )

        assert isinstance(result, CleanupSandboxResult)
        assert result.cleaned is True

    @pytest.mark.asyncio
    async def test_cleanup_idempotent_not_found(self) -> None:
        """Cleanup should succeed even if container is already gone."""
        from open_banca_orchestrator.activities.cleanup_sandbox import (
            CleanupSandboxInput,
            cleanup_sandbox,
        )

        mock_cls, _ = _inject_sandbox_mock()
        mock_runner = MagicMock()
        mock_runner.kill.side_effect = Exception("container not found: 404")
        mock_cls.return_value = mock_runner

        result = await cleanup_sandbox(
            CleanupSandboxInput(container_id="gone123")
        )

        assert result.cleaned is True


class TestListAccounts:
    """ListAccountsActivity tests."""

    def test_extract_accounts_from_accounts_field(self) -> None:
        data = {
            "accounts": [
                {"account_id": "ACC-001", "account_type": "savings", "label": "Ahorros"},
                {"account_id": "ACC-002", "account_type": "checking"},
            ]
        }
        accounts = _extract_accounts(data, "test_bank")
        assert len(accounts) == 2
        assert accounts[0].account_id == "ACC-001"
        assert accounts[0].account_type == "savings"

    def test_extract_accounts_from_entry_points(self) -> None:
        data = {"entry_points": ["ACC-A", "ACC-B"]}
        accounts = _extract_accounts(data, "test_bank")
        assert len(accounts) == 2
        assert accounts[0].account_id == "ACC-A"

    def test_extract_accounts_empty(self) -> None:
        accounts = _extract_accounts({}, "test_bank")
        assert len(accounts) == 0

    @pytest.mark.asyncio
    async def test_list_accounts_falls_back_to_default(self) -> None:
        with patch(
            "open_banca_orchestrator.activities.list_accounts._BANKS_DIR",
            Path("/nonexistent"),
        ):
            result = await list_accounts(
                ListAccountsInput(bank_id="banco_general")
            )
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == "default-account"

    @pytest.mark.asyncio
    async def test_list_accounts_reads_map_json(self, tmp_path: Path) -> None:
        bank_dir = tmp_path / "test_bank"
        bank_dir.mkdir()
        map_data = {
            "bank_id": "test_bank",
            "accounts": [
                {"account_id": "ACC-100", "account_type": "savings"},
            ],
        }
        (bank_dir / "map.json").write_text(json.dumps(map_data))

        with patch(
            "open_banca_orchestrator.activities.list_accounts._BANKS_DIR",
            tmp_path,
        ):
            result = await list_accounts(
                ListAccountsInput(bank_id="test_bank")
            )
        assert len(result.accounts) == 1
        assert result.accounts[0].account_id == "ACC-100"


class TestWorkerRegistration:
    """Verify all new activities are registered in the worker."""

    def _get_activity_names(self) -> list[str]:
        from open_banca_orchestrator.worker import _ASYNC_ACTIVITIES

        names = []
        for a in _ASYNC_ACTIVITIES:
            defn = getattr(a, "__temporal_activity_definition", None)
            if defn is not None:
                names.append(defn.name)
            elif hasattr(a, "__name__"):
                names.append(a.__name__)
        return names

    def test_all_new_activities_in_async_list(self) -> None:
        from open_banca_orchestrator.worker import _ASYNC_ACTIVITIES

        activity_funcs = [
            a.__name__ if hasattr(a, "__name__") else str(a)
            for a in _ASYNC_ACTIVITIES
        ]

        assert "spawn_sandbox" in activity_funcs
        assert "cleanup_sandbox" in activity_funcs
        assert "persist_result" in activity_funcs
        assert "list_accounts" in activity_funcs
        assert "human_input_await" in activity_funcs

    def test_activity_count(self) -> None:
        from open_banca_orchestrator.worker import _ASYNC_ACTIVITIES, _SYNC_ACTIVITIES

        assert len(_ASYNC_ACTIVITIES) == 14
        assert len(_SYNC_ACTIVITIES) == 1

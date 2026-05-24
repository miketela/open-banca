"""Tests for run-mapper --capture-har (dry-run and plan text)."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from open_banca_cli.main import app

runner = CliRunner()


@pytest.mark.unit()
def test_run_mapper_dry_run_capture_har_exits_zero() -> None:
    """With OPEN_BANCA_LIVE_MAPPER unset, --capture-har still dry-runs and exits 0."""
    env = {k: v for k, v in os.environ.items() if k != "OPEN_BANCA_LIVE_MAPPER"}
    result = runner.invoke(
        app,
        [
            "run-mapper",
            "--bank",
            "banco_general",
            "--credential",
            "personal",
            "--capture-har",
        ],
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output.upper() or "dry-run" in result.output.lower()
    assert "--capture-har" in result.output.lower() or "capture-har" in result.output.lower()


@pytest.mark.unit()
def test_capture_har_path_must_be_under_fixtures_har_raw() -> None:
    """--capture-har-path outside fixtures/har/raw/ is rejected (credentials leak risk)."""
    env = {k: v for k, v in os.environ.items() if k != "OPEN_BANCA_LIVE_MAPPER"}
    result = runner.invoke(
        app,
        [
            "run-mapper",
            "--bank",
            "banco_general",
            "--credential",
            "personal",
            "--capture-har-path",
            "/tmp/mapper_outside_raw.har",
        ],
        env=env,
    )
    assert result.exit_code != 0, result.output
    assert "fixtures/har/raw" in result.output.lower() or "har/raw" in result.output.lower()


@pytest.mark.unit()
def test_capture_har_path_relative_under_raw_dir_ok() -> None:
    """Relative path under fixtures/har/raw/ is accepted in dry-run."""
    env = {k: v for k, v in os.environ.items() if k != "OPEN_BANCA_LIVE_MAPPER"}
    result = runner.invoke(
        app,
        [
            "run-mapper",
            "--bank",
            "banco_general",
            "--capture-har",
            "--capture-har-path",
            "packages/banks/banco_general/fixtures/har/raw/custom_run.har",
        ],
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert "custom_run.har" in result.output

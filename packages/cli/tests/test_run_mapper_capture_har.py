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

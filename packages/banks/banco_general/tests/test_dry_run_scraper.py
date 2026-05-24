"""Smoke tests for the dry_run_scraper script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve()
for _candidate in [_REPO_ROOT, *_REPO_ROOT.parents]:
    _pyproject = _candidate / "pyproject.toml"
    if _pyproject.exists() and "[tool.uv.workspace]" in _pyproject.read_text():
        _REPO_ROOT = _candidate
        break

_SCRIPT_PATH = _REPO_ROOT / "scripts" / "dry_run_scraper.py"
_MAP_PATH = _REPO_ROOT / "packages" / "banks" / "banco_general" / "map.json"


def test_dry_run_script_exists() -> None:
    """The dry_run_scraper.py script must exist."""
    assert _SCRIPT_PATH.exists(), f"dry_run_scraper.py not found at {_SCRIPT_PATH}"


def test_dry_run_script_is_importable() -> None:
    """The script must be importable without error (syntax check)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("dry_run_scraper", _SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Load (but don't execute __main__ guard) — tests for import-time errors
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except SystemExit:
        pass  # SystemExit during import is expected from argparse on some Python versions


def test_dry_run_main_function_exists() -> None:
    """The script must expose a main() function."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("dry_run_scraper", _SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except SystemExit:
        pass
    assert hasattr(module, "main"), "dry_run_scraper.py must define a main() function"


def test_dry_run_script_runs_successfully() -> None:
    """Running dry_run_scraper.py banco_general must exit 0."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "banco_general"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"dry_run_scraper.py exited with {result.returncode}.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_dry_run_script_reports_ok() -> None:
    """The script output must contain 'PASSED' for the banco_general stub."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "banco_general"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert "PASSED" in result.stdout, (
        f"Expected 'PASSED' in output.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_dry_run_script_fails_on_missing_bank() -> None:
    """Running with a non-existent bank should exit non-zero (map.json not found)."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "nonexistent_bank_xyz"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode != 0, "Expected non-zero exit for non-existent bank, got 0"


def test_dry_run_inline_validation() -> None:
    """Call the dry-run validation logic directly (no subprocess) for faster feedback."""
    import json

    from open_banca_domain.entities.bank_map import BankMap  # type: ignore[import]

    assert _MAP_PATH.exists(), f"map.json not found at {_MAP_PATH}"
    data = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    bank_map = BankMap.model_validate(data)

    # Basic structural checks mirroring dry_run_scraper logic
    assert bank_map.bank_id == "banco_general"
    assert len(bank_map.steps) > 0

    known_actions = frozenset(
        {
            "navigate",
            "click",
            "fill",
            "wait_for_selector",
            "wait_for_download",
            "select_date_range",
            "assert_text",
            "extract_table",
            "download_file",
            "prompt_user",
        }
    )
    for step in bank_map.steps:
        assert step.action in known_actions, f"Unknown action: {step.action!r}"

    # Fill steps must have value_ref
    for step in bank_map.steps:
        if step.action == "fill":
            step_data = step.model_dump(mode="json")
            assert step_data.get("value_ref"), f"Fill step {step.step_id!r} missing value_ref"


def test_dry_run_scraper_runner_stub() -> None:
    """ScraperRunner.execute_map in stub mode must succeed with the banco_general map."""
    import json

    from open_banca_browser.runner import ScraperRunner  # type: ignore[import]
    from open_banca_domain.entities.bank_map import BankMap  # type: ignore[import]
    from open_banca_domain.entities.credential import Credential  # type: ignore[import]

    data = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    bank_map = BankMap.model_validate(data)

    dummy_credential = Credential(
        id="dry-run-cred",
        bank=bank_map.bank_id,
        credential_ref="vault://dry-run/dummy",
        label="dry-run credential",
    )

    runner = ScraperRunner(
        secret_resolver=lambda _ref: "DRY_RUN_VALUE",
    )

    # OPEN_BANCA_PLAYWRIGHT_REAL must NOT be set — stub mode only
    import os

    if os.environ.get("OPEN_BANCA_PLAYWRIGHT_REAL", "").strip() == "1":
        pytest.skip("OPEN_BANCA_PLAYWRIGHT_REAL=1 — skipping stub test to avoid real browser")

    result = runner.execute_map(bank_map, dummy_credential)
    assert result is not None
    assert result.metadata.get("stub") is True, "Expected stub=True in metadata"

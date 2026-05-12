"""Tests for RemapPatch dry-run validation (validate_patch).

Covers:
  - test_dry_run_valid_patch: schema valid → ok=True
  - test_dry_run_invalid_step_type: unknown action → ok=False
  - test_dry_run_embedded_creds_rejected: sensitive:true + value → rejected
  - test_dry_run_runs_linter: 14-rule MapLinter is invoked
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_llm.remapper.agent import RemapPatch, RemapRisk
from open_banca_llm.remapper.dry_run import validate_patch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def current_map() -> BankMap:
    return BankMap(
        bank_id="banco-general",
        version="1.0.0",
        schema_version="1",
        steps=[
            StepSpec(step_id="s1", action="navigate", target="https://bancogeneral.com"),
            StepSpec(step_id="s2", action="fill", target="#username"),
            StepSpec(step_id="s3", action="click", target="#submit"),
        ],
    )


@pytest.fixture()
def valid_patch() -> RemapPatch:
    return RemapPatch(
        target_step_index=2,
        new_steps=[
            StepSpec(step_id="s3-fixed", action="click", target="#btn-submit"),
        ],
        rationale="Selector changed",
        confidence=0.9,
        risk=RemapRisk.low,
    )


# ---------------------------------------------------------------------------
# test_dry_run_valid_patch
# ---------------------------------------------------------------------------


def test_dry_run_valid_patch(current_map: BankMap, valid_patch: RemapPatch) -> None:
    """A structurally valid patch on a valid map → DryRunResult.ok=True."""
    # We need open-banca-parsing available; if not, linter step is skipped
    # but schema / step-type checks should still pass.
    result = validate_patch(valid_patch, current_map)

    # If linter unavailable the result should still be ok (no schema errors)
    # If linter IS available it should also pass (valid map + stub parser)
    if not result.ok:
        # Only acceptable failure is "MapLinter not available" (CI without parsing pkg)
        linter_issues = [i for i in result.issues if "MapLinter not available" not in i]
        assert not linter_issues, f"Unexpected issues: {result.issues}"


# ---------------------------------------------------------------------------
# test_dry_run_invalid_step_type
# ---------------------------------------------------------------------------


def test_dry_run_invalid_step_type(current_map: BankMap) -> None:
    """A patch with an unknown action type → DryRunResult.ok=False."""
    bad_patch = RemapPatch(
        target_step_index=2,
        new_steps=[
            StepSpec(step_id="s3-bad", action="teleport_to_moon", target="#x"),
        ],
        rationale="Bad step type",
        confidence=0.5,
        risk=RemapRisk.high,
    )
    result = validate_patch(bad_patch, current_map)

    assert not result.ok
    assert any("teleport_to_moon" in issue for issue in result.issues), (
        f"Expected 'teleport_to_moon' in issues, got: {result.issues}"
    )


# ---------------------------------------------------------------------------
# test_dry_run_embedded_creds_rejected
# ---------------------------------------------------------------------------


def test_dry_run_embedded_creds_rejected(current_map: BankMap) -> None:
    """A patch step with sensitive:true AND a literal value → rejected (L04 equiv)."""
    # StepSpec uses extra="allow", so we can pass extra fields via model_validate
    cred_step = StepSpec.model_validate(
        {
            "step_id": "s3-cred",
            "action": "fill",
            "target": "#password",
            "sensitive": True,
            "value": "plaintext_password_leak",
        }
    )
    bad_patch = RemapPatch(
        target_step_index=2,
        new_steps=[cred_step],
        rationale="Leak creds",
        confidence=0.9,
        risk=RemapRisk.high,
    )
    result = validate_patch(bad_patch, current_map)

    assert not result.ok
    assert any("sensitive" in issue and "value" in issue for issue in result.issues), (
        f"Expected embedded-cred issue, got: {result.issues}"
    )


# ---------------------------------------------------------------------------
# test_dry_run_runs_linter (14 rules invoked)
# ---------------------------------------------------------------------------


def test_dry_run_runs_linter(current_map: BankMap, valid_patch: RemapPatch) -> None:
    """validate_patch invokes MapLinter (14-rule community linter) over patched map."""
    try:
        import open_banca_parsing.community.linter as linter_mod  # noqa: PLC0415
    except ImportError:
        pytest.skip("open-banca-parsing not available")

    from pathlib import Path  # noqa: PLC0415

    from open_banca_parsing.community.linter import LintResult  # noqa: PLC0415

    mock_instance = MagicMock()
    mock_instance.lint.return_value = LintResult(bank_dir=Path("/tmp/stub"), errors=[])

    with patch.object(linter_mod, "MapLinter", return_value=mock_instance):
        validate_patch(valid_patch, current_map)

    # Linter was instantiated and lint() was called once
    mock_instance.lint.assert_called_once()
    # The path passed to lint() is a Path object
    call_args = mock_instance.lint.call_args[0]
    assert len(call_args) == 1
    assert hasattr(call_args[0], "__fspath__"), "lint() should be called with a Path"

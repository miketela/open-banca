"""dry_run.py — In-memory patch validation for RemapPatch.

Applies a RemapPatch to a copy of the current BankMap, writes it to a
temporary directory alongside a minimal stub parser.json, then invokes the
14-rule MapLinter from T28.

Design choice: we write a minimal stub parser.json because MapLinter requires
both map.json and parser.json.  The stub parser.json has one sheet with no
column_map entries, so L08/L09/L11/L14 parser rules are vacuously satisfied.
Map-only rules (L01/L03/L04/L05/L06/L07/L12) are fully exercised.

This module does NOT run Playwright or make network calls.
"""

from __future__ import annotations

import copy
import json
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from open_banca_domain.entities.bank_map import BankMap
from open_banca_llm.remapper.agent import RemapPatch

logger = logging.getLogger(__name__)

# Minimal stub parser.json that satisfies ParserSpec schema.
# SheetSpec requires:
#   header_row >= 1, data_start_row >= 1, column_map min_length=1
_STUB_PARSER: dict[str, Any] = {
    "version": "1.0.0",
    "bank": "stub",
    "sheets": [
        {
            "name_pattern": ".*",
            "header_row": 1,
            "data_start_row": 2,
            "column_map": [
                {
                    "target_field": "date",
                    "source": "A",
                    "required": True,
                }
            ],
        }
    ],
}

# Known-valid step action types (same set as ScraperRunner dispatch)
_VALID_STEP_ACTIONS: frozenset[str] = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "wait_for_selector",
        "wait_for_navigation",
        "pause_for_otp",
        "download_file",
        "screenshot",
        "select_option",
        "hover",
        "press_key",
        "scroll",
    }
)


@dataclass
class DryRunResult:
    """Result of validate_patch()."""

    ok: bool
    issues: list[str] = field(default_factory=list)

    def add_issue(self, msg: str) -> None:
        self.issues.append(msg)
        self.ok = False


class _PatchedMapModel(BaseModel):
    """Thin wrapper around raw dict for serialisation only."""

    data: dict[str, Any]


def _apply_patch(patch: RemapPatch, current_map: BankMap) -> BankMap:
    """Apply patch to a deep copy of current_map and return the patched map.

    The patch replaces the window starting at target_step_index with new_steps.
    Steps before the window and steps at index + 1 onwards (after the replaced
    region) are preserved.

    For simplicity the patch replaces exactly ONE step (the failing one) with
    patch.new_steps.  This matches the narrow-window repair semantics.
    """
    current_data = current_map.model_dump()
    patched_steps = copy.deepcopy(current_data["steps"])

    idx = patch.target_step_index
    # Replace the single step at idx with new_steps (may be empty to delete)
    if idx < len(patched_steps):
        patched_steps[idx : idx + 1] = [s.model_dump() for s in patch.new_steps]
    else:
        # Append at end if index is beyond current length
        patched_steps.extend(s.model_dump() for s in patch.new_steps)

    current_data["steps"] = patched_steps
    return BankMap.model_validate(current_data)


def validate_patch(patch: RemapPatch, current_map: BankMap) -> DryRunResult:
    """Validate a RemapPatch against the current BankMap.

    Steps:
    1. Schema check: patch.new_steps all have valid step_id and action fields.
    2. Step-type check: every action in new_steps is a known type.
    3. Embedded-cred check: no step has sensitive:true AND a literal value field.
    4. Apply patch to an in-memory copy of current_map.
    5. Write patched map.json + stub parser.json to a tmpdir.
    6. Run MapLinter (14 rules) over the tmpdir.

    Args:
        patch: RemapPatch produced by RemapperAgent.
        current_map: The current (broken) BankMap.

    Returns:
        DryRunResult with ok=True if all checks pass, issues list otherwise.
    """
    result = DryRunResult(ok=True)

    # ------------------------------------------------------------------
    # 1. Schema validity of new_steps
    # ------------------------------------------------------------------
    for i, step in enumerate(patch.new_steps):
        if not step.step_id:
            result.add_issue(f"new_steps[{i}]: missing step_id")
        if not step.action:
            result.add_issue(f"new_steps[{i}]: missing action")

    # ------------------------------------------------------------------
    # 2. Step type validity
    # ------------------------------------------------------------------
    for i, step in enumerate(patch.new_steps):
        if step.action and step.action not in _VALID_STEP_ACTIONS:
            result.add_issue(
                f"new_steps[{i}] step_id={step.step_id!r}: "
                f"unknown action {step.action!r} — "
                f"allowed: {sorted(_VALID_STEP_ACTIONS)}"
            )

    # ------------------------------------------------------------------
    # 3. Embedded-cred check (inline, before linter — early exit signal)
    # ------------------------------------------------------------------
    for i, step in enumerate(patch.new_steps):
        extra = step.model_extra or {}
        sensitive = extra.get("sensitive", False)
        has_literal_value = "value" in extra
        if sensitive and has_literal_value:
            result.add_issue(
                f"new_steps[{i}] step_id={step.step_id!r}: "
                "sensitive:true with embedded literal `value` field — "
                "credentials must not be hardcoded (L04)"
            )

    # ------------------------------------------------------------------
    # 4. Apply patch to in-memory copy
    # ------------------------------------------------------------------
    try:
        patched_map = _apply_patch(patch, current_map)
    except Exception as exc:
        result.add_issue(f"Patch application failed: {exc}")
        return result

    # ------------------------------------------------------------------
    # 5-6. Write to tmpdir and run MapLinter (14 rules, T28)
    # ------------------------------------------------------------------
    try:
        _run_linter(patched_map, current_map.bank_id, result)
    except Exception as exc:
        result.add_issue(f"Linter invocation error: {exc}")

    return result


def _run_linter(patched_map: BankMap, bank_id: str, result: DryRunResult) -> None:
    """Write patched map.json + stub parser.json to tmpdir; run MapLinter."""
    try:
        from open_banca_parsing.community.linter import (
            MapLinter,  # type: ignore[import-untyped]
        )
    except ImportError as exc:
        result.add_issue(f"MapLinter not available (open-banca-parsing missing): {exc}")
        return

    with tempfile.TemporaryDirectory(prefix="ob_dryrun_") as tmpdir:
        bank_dir = Path(tmpdir)

        # Write patched map.json
        map_data = patched_map.model_dump()
        (bank_dir / "map.json").write_text(
            json.dumps(map_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Write stub parser.json (satisfies ParserSpec schema, vacuous rules)
        stub_parser = dict(_STUB_PARSER)
        stub_parser["bank"] = bank_id
        (bank_dir / "parser.json").write_text(
            json.dumps(stub_parser, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        linter = MapLinter()
        lint_result = linter.lint(bank_dir)

    if not lint_result.ok:
        for error in lint_result.errors:
            result.add_issue(str(error))

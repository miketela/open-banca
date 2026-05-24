"""MapPatcher — atomically apply a RemapPatch to a bank's map.json on disk.

Applies new_steps by replacing the window starting at target_step_index,
validates the resulting map via Pydantic (BankMap schema), then writes
atomically (tmpfile + os.replace).  Creates a .bak backup before apply.

Architectural note (task-21):
  The docs state "apply corre dentro de RemapBankWorkflow".  In v1 (HITL-only)
  the API endpoint calls this service directly to apply the patch before
  signalling the workflow.  Auto-apply inside the workflow is deferred to v1.x.
  See ADR-0013 amendment and docs/03-flows/remap-approval.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Regex to validate bank and proposal IDs before using in file paths / git args.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


@dataclass
class ApplyResult:
    """Outcome of map_patcher.apply_patch_to_disk()."""

    bank_dir: Path
    map_path: Path
    backup_path: Path
    old_version: str
    new_version: str
    steps_replaced: int
    new_steps_count: int


def _bump_version(version: str) -> str:
    """Increment the patch segment of a semver-like version string.

    Strips pre-release suffixes (e.g. '-stub') before parsing.
    Examples:
        '0.0.1-stub' → '0.0.2'
        '1.2.3'      → '1.2.4'
    """
    # Strip pre-release / build metadata
    clean = re.sub(r"[+-].*$", "", version)
    parts = clean.split(".")
    if len(parts) < 3:
        parts = [*parts, "0", "0", "0"][:3]
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts[-1] = "1"
    return ".".join(parts)


def _validate_map(data: dict[str, Any]) -> None:
    """Validate map data against BankMap Pydantic schema.

    Raises ValueError with a human-readable message if validation fails.
    This is the lightweight T28-compatible validation: schema + steps structure.
    Full community linter (L01-L14) is a T28/v1.x follow-up.
    """
    from open_banca_domain.entities.bank_map import BankMap

    try:
        BankMap.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ValueError(f"Map schema validation failed: {exc}") from exc

    # Require at least one step
    if not data.get("steps"):
        raise ValueError("Map must contain at least one step after patch apply")


def apply_patch_to_disk(
    bank_dir: Path,
    target_step_index: int,
    new_steps: list[dict[str, Any]],
    new_version: str | None = None,
) -> ApplyResult:
    """Apply a RemapPatch window to map.json in bank_dir.

    Steps:
    1. Load current map.json.
    2. Create .bak backup (atomic copy).
    3. Splice new_steps at target_step_index (replace one step at that index).
    4. Validate rebuilt map via Pydantic BankMap schema.
    5. Bump version (or use provided new_version).
    6. Write back atomically via tmpfile + os.replace.

    Args:
        bank_dir: Directory containing map.json (e.g. packages/banks/banco_general/).
        target_step_index: Zero-based index of the step to start replacement at.
        new_steps: Replacement steps (list of raw dicts).
        new_version: Override version string; auto-bumped if None.

    Returns:
        ApplyResult with metadata.

    Raises:
        FileNotFoundError: map.json not found.
        ValueError: Validation fails (no write has occurred; .bak preserved).
        IndexError: target_step_index out of bounds.
    """
    map_path = bank_dir / "map.json"
    if not map_path.exists():
        raise FileNotFoundError(f"map.json not found: {map_path}")

    raw = map_path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)

    old_version: str = data.get("version", "0.0.1")
    steps: list[Any] = list(data.get("steps", []))

    if target_step_index > len(steps):
        raise IndexError(
            f"target_step_index {target_step_index} out of range (steps count={len(steps)})"
        )

    # Back up current map before any mutation
    backup_path = map_path.with_suffix(".json.bak")
    shutil.copy2(map_path, backup_path)
    logger.debug("Backed up %s → %s", map_path, backup_path)

    # Splice: replace the single step at target_step_index with new_steps
    steps_replaced = 1 if target_step_index < len(steps) else 0
    new_step_list = (
        steps[:target_step_index] + list(new_steps) + steps[target_step_index + steps_replaced :]
    )
    data["steps"] = new_step_list

    bumped_version = new_version or _bump_version(old_version)
    data["version"] = bumped_version

    # Validate BEFORE writing — if this raises, map_path is still untouched
    try:
        _validate_map(data)
    except ValueError:
        # Remove backup since the original was never changed
        backup_path.unlink(missing_ok=True)
        raise

    # Atomic write: write to tmp then rename
    tmp_path = map_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp_path, map_path)

    logger.info(
        "apply_patch_to_disk: %s version %s → %s (steps_replaced=%d, new_steps=%d)",
        bank_dir.name,
        old_version,
        bumped_version,
        steps_replaced,
        len(new_steps),
    )

    return ApplyResult(
        bank_dir=bank_dir,
        map_path=map_path,
        backup_path=backup_path,
        old_version=old_version,
        new_version=bumped_version,
        steps_replaced=steps_replaced,
        new_steps_count=len(new_steps),
    )

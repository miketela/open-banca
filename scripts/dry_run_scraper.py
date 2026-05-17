#!/usr/bin/env python3
"""dry_run_scraper.py — Validate a BankMap stub without launching a real browser.

Usage:
    uv run python scripts/dry_run_scraper.py banco_general
    uv run python scripts/dry_run_scraper.py banco_general --map-path packages/banks/banco_general/map.json

Exit codes:
    0 — all checks passed
    1 — one or more validation errors found

Checks performed:
    1. JSON parse succeeds
    2. BankMap schema validation passes (BankMap.model_validate)
    3. All step.action values are in the known STEP_DISPATCH_TABLE
    4. Fill steps: value_ref present (not empty), sensitive field is bool
    5. No step embeds a literal credential (no 'password' / 'secret' in value fields)
    6. ScraperRunner.execute_map in stub mode returns without raising
    7. Step ordering sanity: at least one 'navigate' before any 'fill'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Known valid actions (mirrors step_dispatcher.STEP_DISPATCH_TABLE)
# ---------------------------------------------------------------------------
_KNOWN_ACTIONS = frozenset(
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
    }
)

# Fields that must never hold a literal secret value.
_SENSITIVE_FIELD_NAMES = frozenset(
    {"password", "passwd", "secret", "pin", "clave", "token", "api_key"}
)


def _repo_root() -> Path:
    """Find the repository root by walking up from this script's directory."""
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            if "[tool.uv.workspace]" in content:
                return candidate
    return here.parent  # fallback


def main() -> int:
    """Run all dry-run checks and return exit code (0=ok, 1=errors)."""
    import argparse

    parser = argparse.ArgumentParser(description="Dry-run validate a BankMap JSON file.")
    parser.add_argument("bank", help="Bank identifier, e.g. banco_general")
    parser.add_argument(
        "--map-path",
        type=Path,
        default=None,
        help="Override path to map.json (default: packages/banks/<bank>/map.json)",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    map_path: Path = args.map_path or (repo_root / "packages" / "banks" / args.bank / "map.json")

    errors: list[str] = []
    warnings: list[str] = []

    print(f"[dry_run_scraper] Validating: {map_path}")

    # ── Check 1: file exists ─────────────────────────────────────────────────
    if not map_path.exists():
        print(f"ERROR: map.json not found at {map_path}")
        return 1

    # ── Check 2: JSON parse ──────────────────────────────────────────────────
    try:
        raw = map_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: JSON parse failed: {exc}")
        return 1

    print("  [ok] JSON parse")

    # ── Check 3: BankMap schema validation ───────────────────────────────────
    try:
        from open_banca_domain.entities.bank_map import BankMap  # type: ignore[import]

        bank_map = BankMap.model_validate(data)
    except Exception as exc:
        errors.append(f"BankMap schema validation failed: {exc}")
        bank_map = None

    if bank_map is not None:
        print(
            f"  [ok] BankMap schema valid (bank_id={bank_map.bank_id!r}, version={bank_map.version!r})"
        )

    # ── Check 4: action names ────────────────────────────────────────────────
    if bank_map is not None:
        for step in bank_map.steps:
            if step.action not in _KNOWN_ACTIONS:
                errors.append(
                    f"Step {step.step_id!r} has unknown action {step.action!r}. "
                    f"Known: {sorted(_KNOWN_ACTIONS)}"
                )
        action_counts = {action: 0 for action in _KNOWN_ACTIONS}
        for step in bank_map.steps:
            if step.action in action_counts:
                action_counts[step.action] += 1
        unknown_count = sum(1 for s in bank_map.steps if s.action not in _KNOWN_ACTIONS)
        if unknown_count == 0:
            print(f"  [ok] All {len(bank_map.steps)} step actions are valid")

    # ── Check 5: fill steps must have value_ref ──────────────────────────────
    if bank_map is not None:
        for step in bank_map.steps:
            if step.action == "fill":
                step_data = step.model_dump(mode="json")
                value_ref = step_data.get("value_ref", "")
                if not value_ref:
                    errors.append(
                        f"Fill step {step.step_id!r} is missing 'value_ref'. "
                        "Credentials must be resolved via vault references, not embedded."
                    )
                sensitive = step_data.get("sensitive")
                if sensitive is not None and not isinstance(sensitive, bool):
                    errors.append(
                        f"Fill step {step.step_id!r}: 'sensitive' must be a boolean, got {sensitive!r}"
                    )
        fill_steps = [s for s in bank_map.steps if s.action == "fill"]
        fill_errors = sum(
            1 for s in fill_steps if not s.model_dump(mode="json").get("value_ref", "")
        )
        if fill_steps and fill_errors == 0:
            print(f"  [ok] All {len(fill_steps)} fill step(s) have value_ref")

    # ── Check 6: no embedded literal credentials ─────────────────────────────
    raw_lower = raw.lower()
    for field in _SENSITIVE_FIELD_NAMES:
        # Look for patterns like "value": "some_literal_password"
        # We allow "value_ref" (the correct pattern) but flag bare "value" with sensitive names.
        import re

        pattern = rf'"value"\s*:\s*"[^"]*{re.escape(field)}[^"]*"'
        if re.search(pattern, raw_lower):
            warnings.append(
                f"Potential literal credential in map.json (field containing {field!r}). "
                "Use value_ref instead of embedding secrets."
            )

    if not warnings:
        print("  [ok] No embedded literal credentials detected")
    else:
        for w in warnings:
            print(f"  [warn] {w}")

    # ── Check 7: step ordering — navigate before fill ────────────────────────
    if bank_map is not None:
        seen_navigate = False
        ordering_ok = True
        for step in bank_map.steps:
            if step.action == "navigate":
                seen_navigate = True
            if step.action == "fill" and not seen_navigate:
                errors.append(
                    f"Fill step {step.step_id!r} appears before any navigate step. "
                    "Login form cannot be filled before navigating to the login page."
                )
                ordering_ok = False
                break
        if ordering_ok:
            print("  [ok] Step ordering: navigate appears before fill steps")

    # ── Check 8: ScraperRunner stub execution ────────────────────────────────
    if bank_map is not None:
        try:
            from open_banca_browser.runner import ScraperRunner  # type: ignore[import]
            from open_banca_domain.entities.credential import Credential  # type: ignore[import]

            dummy_credential = Credential(
                id="dry-run-cred",
                bank=bank_map.bank_id,
                credential_ref="vault://dry-run/dummy",
                label="dry-run credential",
            )

            runner = ScraperRunner(
                secret_resolver=lambda _ref: "DRY_RUN_VALUE",
            )

            # OPEN_BANCA_PLAYWRIGHT_REAL must NOT be set for stub mode.
            # If set, we warn but skip the stub test to avoid launching a real browser.
            import os

            if os.environ.get("OPEN_BANCA_PLAYWRIGHT_REAL", "").strip() == "1":
                warnings.append(
                    "OPEN_BANCA_PLAYWRIGHT_REAL=1 is set — skipping ScraperRunner stub test "
                    "to avoid launching a real browser in dry-run mode."
                )
            else:
                result = runner.execute_map(bank_map, dummy_credential)
                assert result is not None, "execute_map returned None"
                print(
                    f"  [ok] ScraperRunner stub execution succeeded "
                    f"(job_id={result.metadata.get('job_id', 'N/A')!r})"
                )

        except ImportError as exc:
            warnings.append(f"open-banca-browser not installed, skipping stub execution: {exc}")
        except Exception as exc:
            errors.append(f"ScraperRunner stub execution failed: {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if errors:
        print(f"FAILED — {len(errors)} error(s):")
        for err in errors:
            print(f"  ERROR: {err}")
        if warnings:
            print(f"\n{len(warnings)} warning(s):")
            for w in warnings:
                print(f"  WARN: {w}")
        return 1
    else:
        print("PASSED — all checks OK")
        if warnings:
            print(f"  ({len(warnings)} non-fatal warning(s) above)")
        return 0


if __name__ == "__main__":
    sys.exit(main())

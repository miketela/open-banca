"""Test that the PILOT_RUNBOOK.md is internally consistent.

Checks:
  - The runbook file exists.
  - All CLI subcommands referenced (open-banca <cmd>) are registered in the app.
  - All file paths referenced that should exist under the repo do exist.
  - The runbook contains all required sections.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RUNBOOK_PATH = Path(__file__).parent.parent / "PILOT_RUNBOOK.md"
_REPO_ROOT = Path(__file__).resolve()
# Walk up to find the workspace root (contains pyproject.toml with [tool.uv.workspace])
for _candidate in [_REPO_ROOT, *_REPO_ROOT.parents]:
    _pyproject = _candidate / "pyproject.toml"
    if _pyproject.exists() and "[tool.uv.workspace]" in _pyproject.read_text():
        _REPO_ROOT = _candidate
        break

# Subcommands that must be registered in the CLI app.
_EXPECTED_CLI_COMMANDS = {
    "register-credentials",
    "list-credentials",
    "run-mapper",
}

# Required section headings in the runbook.
_REQUIRED_SECTIONS = [
    "Pre-checks",
    "register-credentials",
    "list-credentials",
    "run-mapper",
    "dry_run_scraper",
    "Troubleshooting",
]


def test_runbook_exists() -> None:
    """PILOT_RUNBOOK.md must exist in the banco_general package."""
    assert _RUNBOOK_PATH.exists(), f"PILOT_RUNBOOK.md not found at {_RUNBOOK_PATH}"


def test_runbook_is_not_empty() -> None:
    """PILOT_RUNBOOK.md must have substantial content."""
    content = _RUNBOOK_PATH.read_text(encoding="utf-8")
    assert len(content) > 500, "PILOT_RUNBOOK.md is too short — likely incomplete"


def test_runbook_contains_required_sections() -> None:
    """The runbook must contain all expected section keywords."""
    content = _RUNBOOK_PATH.read_text(encoding="utf-8")
    missing = [s for s in _REQUIRED_SECTIONS if s not in content]
    assert missing == [], f"PILOT_RUNBOOK.md is missing sections/keywords: {missing}"


def test_cli_commands_referenced_in_runbook_exist() -> None:
    """All 'open-banca <subcommand>' references in the runbook must be registered in the CLI app."""
    content = _RUNBOOK_PATH.read_text(encoding="utf-8")

    # Find all 'open-banca <subcommand>' in the runbook.
    # Match only known-style subcommand names (hyphenated words) that appear
    # immediately after 'open-banca' followed by at least one space.
    # Exclude bare words that look like prose (all-uppercase, plain English).
    referenced_cmds: set[str] = set()
    pattern = re.compile(r"open-banca\s+([\w][\w-]*[\w])")
    known_prefix = ("register", "list", "rotate", "run")
    for match in pattern.finditer(content):
        cmd = match.group(1)
        # Only consider hyphenated or known-prefix commands (skip prose words like CLI, --help etc.)
        if cmd.startswith("-"):
            continue
        if "-" in cmd or any(cmd.startswith(p) for p in known_prefix):
            referenced_cmds.add(cmd)

    # Import the CLI app to check registered commands
    from open_banca_cli.main import app  # type: ignore[import]

    registered = {cmd.name for cmd in app.registered_commands if cmd.name}

    for cmd in referenced_cmds:
        assert cmd in registered, (
            f"Runbook references 'open-banca {cmd}' but it is not registered in the CLI app. "
            f"Registered: {sorted(registered)}"
        )


def test_file_paths_referenced_in_runbook_exist() -> None:
    """File paths that are referenced and should exist under the repo must actually exist."""
    content = _RUNBOOK_PATH.read_text(encoding="utf-8")

    # Extract path-like strings from code blocks (lines starting with paths or containing packages/)
    path_pattern = re.compile(r"packages/[\w/._-]+\.(json|py|toml|md)")
    referenced_paths: set[str] = set()
    for match in path_pattern.finditer(content):
        referenced_paths.add(match.group(0))

    missing: list[str] = []
    for rel_path in referenced_paths:
        full_path = _REPO_ROOT / rel_path
        if not full_path.exists():
            missing.append(rel_path)

    # We expect map.json and parser.json to exist (they are stubs we created)
    assert missing == [], "The runbook references paths that do not exist:\n" + "\n".join(
        f"  - {p}" for p in missing
    )


def test_runbook_mentions_stub_warning() -> None:
    """The runbook must warn that map.json and parser.json are stubs."""
    content = _RUNBOOK_PATH.read_text(encoding="utf-8")
    keywords = ["stub", "STUB", "reemplazar", "replace", "TBD"]
    has_warning = any(kw in content for kw in keywords)
    assert has_warning, (
        "The runbook must warn that map.json/parser.json are stubs that must be replaced "
        "after the first Mapper run. Add a STUB warning."
    )


@pytest.mark.parametrize(
    "cmd",
    sorted(_EXPECTED_CLI_COMMANDS),
)
def test_expected_commands_are_in_cli_app(cmd: str) -> None:
    """Each expected CLI command must be registered in the app."""
    from open_banca_cli.main import app  # type: ignore[import]

    registered = {c.name for c in app.registered_commands if c.name}
    assert cmd in registered, (
        f"Expected command {cmd!r} not found in CLI app. Registered: {sorted(registered)}"
    )

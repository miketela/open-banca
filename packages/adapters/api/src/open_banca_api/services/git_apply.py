"""git_apply — helper to git-commit and git-tag a map.json change.

Security: all arguments are passed as list elements to subprocess.run,
never via shell=True.  bank and proposal_id are validated against a
safe-ID regex before use to prevent path-traversal or argument injection.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Only alphanumerics, underscores, hyphens allowed in IDs used in git args.
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
# Tag prefix for bank-map version tags.
_TAG_PREFIX = "bank-maps"


class GitApplyError(Exception):
    """Raised when a git operation fails."""


@dataclass
class GitApplyResult:
    """Result of a git commit + tag operation."""

    commit_sha: str
    tag: str


def _sanitize_id(value: str, label: str) -> str:
    """Validate that value matches the safe-ID pattern.

    Raises ValueError for any value containing characters outside [a-zA-Z0-9_-].
    This prevents shell injection even with shell=False by guarding interpolation
    into git commit messages and tag names.
    """
    if not _SAFE_ID_RE.match(value):
        raise ValueError(
            f"{label} contains disallowed characters: {value!r}. Only [a-zA-Z0-9_-] are permitted."
        )
    return value


def _run(args: list[str], cwd: Path) -> str:
    """Run a subprocess command; return stdout stripped.  Raises GitApplyError on failure."""
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise GitApplyError(
            f"git command failed ({result.returncode}): {args}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout.strip()


def git_commit_and_tag(
    repo_root: Path,
    bank: str,
    proposal_id: str,
    new_version: str,
    map_rel_path: str,
) -> GitApplyResult:
    """Stage map.json, commit, and create a semver tag for the bank.

    Args:
        repo_root:    Root of the git repository.
        bank:         Bank identifier (e.g. 'banco_general').
        proposal_id:  UUID of the approved proposal (used in commit message).
        new_version:  New semver string for the tag (e.g. '0.0.2').
        map_rel_path: Path to map.json relative to repo_root.

    Returns:
        GitApplyResult with commit SHA and tag name.

    Raises:
        ValueError:      If bank or proposal_id contain unsafe characters.
        GitApplyError:   If any git command fails.
    """
    bank = _sanitize_id(bank, "bank")
    proposal_id = _sanitize_id(proposal_id, "proposal_id")
    new_version = _sanitize_id(new_version.replace(".", "-"), "new_version")
    # Re-allow dots in version for tagging (we sanitized with dots replaced temporarily)
    new_version = new_version.replace("-", ".")
    # Re-check the dot-containing version with relaxed regex
    if not re.match(r"^[a-zA-Z0-9._\-]+$", new_version):
        raise ValueError(f"new_version contains disallowed characters: {new_version!r}")

    commit_msg = f"chore(maps): apply proposal {proposal_id} to {bank}"
    tag_name = f"{_TAG_PREFIX}/{bank}/v{new_version}"

    # git add
    _run(["git", "add", map_rel_path], cwd=repo_root)
    logger.debug("Staged %s", map_rel_path)

    # git commit
    _run(
        ["git", "commit", "-m", commit_msg, "--no-verify"],
        cwd=repo_root,
    )
    logger.info("Committed map change: %s", commit_msg)

    # get commit SHA
    sha = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)

    # git tag (lightweight — CI handles signed tags for official maps)
    _run(["git", "tag", tag_name], cwd=repo_root)
    logger.info("Created tag: %s at %s", tag_name, sha)

    return GitApplyResult(commit_sha=sha, tag=tag_name)

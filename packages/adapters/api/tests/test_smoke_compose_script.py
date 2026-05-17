"""Static checks for HU02 deploy helper scripts (bootstrap + smoke compose)."""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in [p, *p.parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "docker-compose.yml").is_file():
            return parent
    msg = "repo root not found (pyproject.toml + docker-compose.yml)"
    raise RuntimeError(msg)


def test_bootstrap_env_script_exists_and_executable() -> None:
    """Shellcheck-style: script present and marked executable for operators."""
    root = _repo_root()
    script = root / "scripts" / "bootstrap_env.sh"
    assert script.is_file(), f"missing {script}"
    assert os.access(script, os.X_OK), f"not executable: {script}"


def test_smoke_compose_uses_staggered_docker_compose_up() -> None:
    """Ensures smoke follows HU02 phased bring-up (postgres → temporal → proxy → app tier)."""
    root = _repo_root()
    text = (root / "scripts" / "smoke_compose.sh").read_text(encoding="utf-8")
    assert "docker compose" in text and "up -d postgres-temporal" in text
    assert "up -d temporal-server" in text
    assert "up -d docker-socket-proxy" in text
    assert "up -d api temporal-worker" in text


def test_smoke_compose_documents_keep_up() -> None:
    root = _repo_root()
    text = (root / "scripts" / "smoke_compose.sh").read_text(encoding="utf-8")
    assert "KEEP_UP=1" in text

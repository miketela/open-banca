"""Tests for scripts/redact_har.py — delegates to open_banca_browser.har.sanitize."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _minimal_har_with_canary(body: str) -> dict:
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "test", "version": "0"},
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": "https://bank.example.com/login",
                        "headers": [],
                        "cookies": [],
                        "queryString": [],
                        "postData": {"mimeType": "application/json", "text": body},
                    },
                    "response": {
                        "status": 200,
                        "headers": [],
                        "cookies": [],
                        "content": {"mimeType": "application/json", "text": "{}"},
                    },
                }
            ],
        }
    }


@pytest.mark.canary()
def test_redact_har_script_removes_secret_canary(tmp_path: Path) -> None:
    """HAR containing SECRET_CANARY_VALUE is sanitized to REDACTED via scripts/redact_har.py."""
    repo_root = Path(__file__).resolve().parents[4]
    script = repo_root / "scripts" / "redact_har.py"
    assert script.is_file(), f"expected {script}"

    raw = tmp_path / "raw.har"
    out = tmp_path / "sanitized.har"
    har = _minimal_har_with_canary('{"user":"x","note":"SECRET_CANARY_VALUE"}')
    raw.write_text(json.dumps(har), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script), str(raw), str(out)],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    assert result.returncode == 0, result.stderr + result.stdout

    sanitized = json.loads(out.read_text(encoding="utf-8"))
    blob = json.dumps(sanitized)
    assert "SECRET_CANARY_VALUE" not in blob
    assert "REDACTED" in blob

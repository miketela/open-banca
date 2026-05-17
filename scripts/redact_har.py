#!/usr/bin/env python3
"""Redact a HAR file for safe commit (delegates to open_banca_browser.har.sanitize).

Usage::

    uv run python scripts/redact_har.py raw.har sanitized.har

Equivalent to ``python -m open_banca_browser.har.sanitize`` with the same arguments.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    cmd = [sys.executable, "-m", "open_banca_browser.har.sanitize", *sys.argv[1:]]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()

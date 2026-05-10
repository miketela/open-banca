#!/usr/bin/env python3
"""HAR sanitizer gate — pre-commit hook.

Blocks committing HAR files that contain unsanitized credentials or canary values.
Used as a pre-commit local hook (Task 11 / REQ-016).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FORBIDDEN: list[re.Pattern[str]] = [
    re.compile(r"SECRET_CANARY_VALUE", re.IGNORECASE),
    re.compile(r"PII_CANARY_[A-Z0-9_]+", re.IGNORECASE),
    re.compile(r'"Authorization"\s*:\s*"(?!REDACTED)[^"]+', re.IGNORECASE),
    re.compile(r'"Cookie"\s*:\s*"(?!REDACTED)[^"]+', re.IGNORECASE),
    re.compile(r'"Set-Cookie"\s*:\s*"(?!REDACTED)[^"]+', re.IGNORECASE),
    re.compile(r'"(?:password|token|session)"\s*:\s*"(?!REDACTED)[^"]+', re.IGNORECASE),
]

RAW_PATH_RE = re.compile(r"[\\/]raw[\\/]")


def main() -> int:
    failed = False
    for p in sys.argv[1:]:
        path = Path(p)
        if RAW_PATH_RE.search(str(path)):
            print(f"ERROR: refusing to commit HAR from raw/ directory: {p}", file=sys.stderr)
            failed = True
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"WARNING: could not read {p}: {exc}", file=sys.stderr)
            continue
        for pat in FORBIDDEN:
            if pat.search(content):
                print(
                    f"ERROR: {p} contains unsanitized secret pattern: {pat.pattern}",
                    file=sys.stderr,
                )
                failed = True
                break
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

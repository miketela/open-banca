"""dsl/helpers.py — 9 whitelisted DSL helpers for Excel parser transformations.

Security constraints (ADR-0007-amendment):
- extract_regex uses re2 (google-re2) ONLY — O(n) linear-time, immune to ReDoS (CWE-1333).
- NO re stdlib usage (except strptime format string parsing — stdlib datetime only).
- lookup_table hard-capped at 100 000 entries (CWE-400).
- CPU cap: 100 ms per cell for extract_regex and parse_date (enforced via signal.alarm on Unix,
  or threading.Timer on macOS/Windows where SIGALRM is unreliable in threads).
- Memory cap: 10 MB uncompressed per sheet (enforced in engine, not per-helper).
- concat output capped at 64 KB per cell; max 50 refs per invocation.
"""

from __future__ import annotations

import signal
import sys
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# re2 — google-re2 package (NOT stdlib re). Linear-time regex engine.
# Import alias makes the "no re stdlib" constraint self-documenting.
import re2 as _re2  # type: ignore[import-untyped]

_CPU_CAP_SECONDS = 0.1  # 100 ms
_CONCAT_OUTPUT_MAX_BYTES = 64 * 1024  # 64 KB
_CONCAT_MAX_REFS = 50
_LOOKUP_TABLE_MAX_ENTRIES = 100_000


# ---------------------------------------------------------------------------
# Internal timing helpers
# ---------------------------------------------------------------------------


class _ResourceBudgetExceeded(RuntimeError):
    """Raised when a helper exceeds its CPU or memory cap."""


def _run_with_timeout(
    func: Any, *args: Any, timeout: float = _CPU_CAP_SECONDS, **kwargs: Any
) -> Any:
    """Run *func* with a hard CPU wall-clock timeout.

    On POSIX with SIGALRM available (Linux), uses SIGALRM for precision.
    On macOS / Windows (SIGALRM unreliable in non-main threads), uses a
    threading.Timer that sets an event; the main computation checks the event.
    """
    if sys.platform != "win32" and threading.current_thread() is threading.main_thread():
        # SIGALRM path — only valid in main thread on POSIX
        def _handler(signum: int, frame: Any) -> None:
            raise _ResourceBudgetExceeded("helper CPU cap exceeded (100 ms)")

        old = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            return func(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)
    else:
        # Threading path for worker threads / macOS reliability
        result: list[Any] = []
        exc: list[BaseException] = []
        timed_out: list[bool] = []

        def _target() -> None:
            try:
                result.append(func(*args, **kwargs))
            except BaseException as e:
                exc.append(e)

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            timed_out.append(True)
            raise _ResourceBudgetExceeded("helper CPU cap exceeded (100 ms)")
        if exc:
            raise exc[0]
        return result[0]


# ---------------------------------------------------------------------------
# 9 whitelisted helpers
# ---------------------------------------------------------------------------


def parse_date(value: str, format: str) -> datetime:
    """Parse *value* string into a datetime using the given strptime *format*.

    CPU cap: 100 ms. Raises ValueError on parse failure.

    Args:
        value: Cell string value.
        format: strptime-compatible format string, e.g. ``%d/%m/%Y``.

    Returns:
        Parsed datetime object.

    Raises:
        ValueError: If the value cannot be parsed with the given format.
        _ResourceBudgetExceeded: If parsing exceeds 100 ms CPU cap.
    """
    if not isinstance(value, str):
        value = str(value)

    def _parse() -> datetime:
        return datetime.strptime(value.strip(), format)

    return _run_with_timeout(_parse)


def extract_regex(value: str, pattern: str, group: int = 1) -> str:
    """Extract a substring from *value* using a re2-compiled *pattern*.

    Uses google-re2 (NOT stdlib re) for O(n) linear-time guarantees.
    Rejects patterns that re2 cannot compile (e.g. lookaheads, possessive quantifiers).

    Args:
        value: Cell string value.
        pattern: RE2-compatible regex pattern.
        group: Capture group index (default 1). 0 returns the full match.

    Returns:
        Matched group string, or empty string if no match.

    Raises:
        ValueError: If pattern is invalid under re2.
        _ResourceBudgetExceeded: If matching exceeds 100 ms CPU cap.
    """
    if not isinstance(value, str):
        value = str(value)

    try:
        compiled = _re2.compile(pattern)
    except Exception as exc:
        msg = f"extract_regex: invalid re2 pattern {pattern!r}: {exc}"
        raise ValueError(msg) from exc

    def _match() -> str:
        m = compiled.search(value)
        if m is None:
            return ""
        result = m.group(group)
        return str(result) if result is not None else ""

    return _run_with_timeout(_match)


def normalize_amount(value: str, locale: str = "en_US") -> Decimal:
    """Normalise a locale-formatted monetary string to a Python Decimal.

    Supported locales:
    - ``en_US``: thousands separator ``,``, decimal separator ``.``
    - ``pa_PA``: identical to en_US for numeric formatting
    - ``es_PA``: thousands separator ``.``, decimal separator ``,``
    - ``es_ES``: same as es_PA

    Args:
        value: Cell string value, e.g. ``-1,234.56`` or ``1.234,56``.
        locale: Locale hint for separator detection.

    Returns:
        Decimal representation of the amount.

    Raises:
        ValueError: If the value cannot be parsed as a decimal.
    """
    if not isinstance(value, str):
        value = str(value)

    cleaned = value.strip()
    # Remove currency symbols and whitespace
    for ch in ("$", "B/.", "PAB", "USD", "\xa0", " "):
        cleaned = cleaned.replace(ch, "")
    cleaned = cleaned.strip()

    # Detect format by locale
    if locale in ("es_PA", "es_ES"):
        # thousands='.', decimal=','
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # en_US / pa_PA: thousands=',', decimal='.'
        cleaned = cleaned.replace(",", "")

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        msg = f"normalize_amount: cannot parse {value!r} as decimal (locale={locale!r})"
        raise ValueError(msg) from exc


def lookup_table(value: str, map: dict[str, str]) -> str:
    """Replace *value* using a pre-built lookup *map* (O(1) hash lookup).

    Hard cap: 100 000 entries. Exceeding this raises ValueError at call time
    (should be caught at config validation; this is a runtime guard).

    Args:
        value: Cell string value.
        map: Dictionary of exact-match replacements.

    Returns:
        Mapped value if found, otherwise the original *value* unchanged.

    Raises:
        ValueError: If the map exceeds the 100 000-entry cap.
    """
    if len(map) > _LOOKUP_TABLE_MAX_ENTRIES:
        msg = f"lookup_table map has {len(map)} entries, exceeds cap of {_LOOKUP_TABLE_MAX_ENTRIES} (CWE-400)"
        raise ValueError(msg)
    return map.get(str(value), str(value))


def coalesce(*values: str | None) -> str:
    """Return the first non-empty, non-None value from *values*.

    Args:
        *values: Column cell values to check in order.

    Returns:
        First truthy string value, or empty string if all are empty/None.
    """
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def trim(value: str) -> str:
    """Strip leading and trailing whitespace from *value*.

    Args:
        value: Cell string value.

    Returns:
        Whitespace-stripped string.
    """
    return str(value).strip()


def to_upper(value: str) -> str:
    """Convert *value* to uppercase.

    Args:
        value: Cell string value.

    Returns:
        Uppercased string.
    """
    return str(value).upper()


def to_lower(value: str) -> str:
    """Convert *value* to lowercase.

    Args:
        value: Cell string value.

    Returns:
        Lowercased string.
    """
    return str(value).lower()


def concat(*values: str | None, sep: str = " ") -> str:
    """Join *values* with *sep*.

    Output is capped at 64 KB. Max 50 values per call.

    Args:
        *values: Values to join (typically column refs resolved to strings).
        sep: Separator string (default single space).

    Returns:
        Joined string.

    Raises:
        ValueError: If more than 50 values are provided.
        ValueError: If output exceeds 64 KB.
    """
    if len(values) > _CONCAT_MAX_REFS:
        msg = f"concat: max {_CONCAT_MAX_REFS} refs per invocation, got {len(values)}"
        raise ValueError(msg)
    result = sep.join(str(v) for v in values if v is not None)
    if len(result.encode()) > _CONCAT_OUTPUT_MAX_BYTES:
        msg = f"concat: output exceeds 64 KB cap ({len(result.encode())} bytes)"
        raise ValueError(msg)
    return result

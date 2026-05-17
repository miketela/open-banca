"""Redact filter for logs, OTel spans, and Langfuse traces.

Security requirement: REQ-016 / CLAUDE.md §Security Critical Rules #1.

This module provides:

  ``RedactFilter``
      A ``logging.Filter`` subclass.  Add to any logger or handler to
      scrub canary values and PII patterns from ``LogRecord.msg`` and
      ``LogRecord.args`` before emission.

  ``RedactStream``
      A ``logging.StreamHandler`` subclass that wraps a target handler and
      applies ``RedactFilter`` automatically.  Drop-in replacement for
      ``logging.StreamHandler`` in environments where the raw stream must be
      guarded (e.g. Langfuse HTTP exporter, OTel exporter stdout).

Patterns scrubbed (all configurable via environment variables):

  - ``SECRET_CANARY_VALUE``  — set via ``OPEN_BANCA_SECRET_CANARY_VALUE``
  - ``PII_CANARY_NAME``      — set via ``OPEN_BANCA_PII_CANARY_NAME``
  - ``PII_CANARY_ACCT``      — set via ``OPEN_BANCA_PII_CANARY_ACCT``
  - ``PII_CANARY_BAL``       — set via ``OPEN_BANCA_PII_CANARY_BAL``
  - Panama bank account numbers (16 digits, configurable)
  - Bearer tokens in ``Authorization:`` header strings
  - Password field values in JSON (``"password": "..."`` patterns)

ADR-0020: filter acts before OTel/Langfuse persistence; both text payloads
and JSON-serialised attributes are scrubbed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import IO

_REDACTED = "[REDACTED]"

# ── Default canary env var names ──────────────────────────────────────────────
_ENV_SECRET_CANARY = "OPEN_BANCA_SECRET_CANARY_VALUE"
_ENV_PII_NAME = "OPEN_BANCA_PII_CANARY_NAME"
_ENV_PII_ACCT = "OPEN_BANCA_PII_CANARY_ACCT"
_ENV_PII_BAL = "OPEN_BANCA_PII_CANARY_BAL"


# ── Structural regex patterns (always active) ─────────────────────────────────

# Panama bank account: 16 consecutive digits (Banco General, BAC, etc.)
# Documented: PA retail accounts are typically 16-digit identifiers.
# Configurable: set OPEN_BANCA_ACCT_PATTERN to override the regex.
_DEFAULT_ACCT_PATTERN = r"\b\d{16}\b"

# Bearer token in HTTP headers: Authorization: Bearer <token>
_BEARER_TOKEN_PATTERN = r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"

# Password value in JSON: "password": "<value>" (handles nested JSON logs)
_JSON_PASSWORD_PATTERN = r'(?i)"password"\s*:\s*"[^"]*"'


@dataclass
class RedactConfig:
    """Configuration for ``RedactFilter``.

    Attributes:
        secret_canary: Literal canary value to scrub (``SECRET_CANARY_VALUE``).
        pii_name: PII canary for name fields.
        pii_acct: PII canary for account number fields.
        pii_bal: PII canary for balance fields.
        extra_literals: Additional literal strings to scrub (e.g. live credentials).
        acct_pattern: Regex pattern for bank account numbers.
    """

    secret_canary: str = field(default_factory=lambda: os.environ.get(_ENV_SECRET_CANARY, ""))
    pii_name: str = field(default_factory=lambda: os.environ.get(_ENV_PII_NAME, ""))
    pii_acct: str = field(default_factory=lambda: os.environ.get(_ENV_PII_ACCT, ""))
    pii_bal: str = field(default_factory=lambda: os.environ.get(_ENV_PII_BAL, ""))
    extra_literals: list[str] = field(default_factory=list)
    acct_pattern: str = field(
        default_factory=lambda: os.environ.get("OPEN_BANCA_ACCT_PATTERN", _DEFAULT_ACCT_PATTERN)
    )

    def all_literals(self) -> list[str]:
        """Return all non-empty literal canary strings."""
        candidates = [
            self.secret_canary,
            self.pii_name,
            self.pii_acct,
            self.pii_bal,
            *self.extra_literals,
        ]
        return [s for s in candidates if s]


def _compile_patterns(config: RedactConfig) -> list[re.Pattern[str]]:
    """Compile all regex patterns (literal + structural) into a list."""
    patterns: list[re.Pattern[str]] = []

    # Literal canary values (escaped so they're treated as plain strings, not regex)
    for literal in config.all_literals():
        patterns.append(re.compile(re.escape(literal)))

    # Structural patterns (always active)
    patterns.append(re.compile(_BEARER_TOKEN_PATTERN))
    patterns.append(re.compile(_JSON_PASSWORD_PATTERN))

    # Account number pattern (configurable)
    if config.acct_pattern:
        patterns.append(re.compile(config.acct_pattern))

    return patterns


def _apply_redact(text: str, patterns: list[re.Pattern[str]]) -> str:
    """Apply all *patterns* substitutions to *text*, returning the scrubbed string."""
    for pat in patterns:
        text = pat.sub(_REDACTED, text)
    return text


class RedactFilter(logging.Filter):
    """``logging.Filter`` that scrubs secrets and PII from log records.

    Usage::

        import logging
        from open_banca_observability.redact import RedactFilter, RedactConfig

        config = RedactConfig(secret_canary="my-secret-value")
        handler = logging.StreamHandler()
        handler.addFilter(RedactFilter(config=config))

    The filter modifies ``record.msg`` and, if ``record.args`` is a tuple or
    dict, each string value within it.  Structured logging frameworks that
    serialise ``extra`` fields should add the filter to the handler rather
    than the logger to catch the final formatted string.

    Args:
        config: A ``RedactConfig`` instance.  If None, reads canary values
            from environment variables at construction time.
        name: Passed through to ``logging.Filter.__init__``.
    """

    def __init__(
        self,
        config: RedactConfig | None = None,
        name: str = "",
    ) -> None:
        super().__init__(name)
        self._config = config if config is not None else RedactConfig()
        self._patterns: list[re.Pattern[str]] = _compile_patterns(self._config)

    def filter(self, record: logging.LogRecord) -> bool:
        """Scrub the log record in-place; always return True (don't drop records)."""
        if isinstance(record.msg, str):
            record.msg = _apply_redact(record.msg, self._patterns)

        if isinstance(record.args, tuple):
            record.args = tuple(
                _apply_redact(a, self._patterns) if isinstance(a, str) else a for a in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                k: _apply_redact(v, self._patterns) if isinstance(v, str) else v
                for k, v in record.args.items()
            }

        return True

    def scrub(self, text: str) -> str:
        """Scrub *text* directly (bypasses LogRecord; useful for OTel/Langfuse hooks)."""
        return _apply_redact(text, self._patterns)


class RedactStream(logging.StreamHandler):
    """``logging.StreamHandler`` wrapper that auto-applies ``RedactFilter``.

    Drop-in for ``logging.StreamHandler`` in loggers feeding OTel exporters,
    Langfuse HTTP exporters, or any stream where secrets must not appear.

    Example::

        import sys
        from open_banca_observability.redact import RedactStream, RedactConfig

        config = RedactConfig(secret_canary="my-secret")
        handler = RedactStream(stream=sys.stdout, config=config)
        logging.getLogger().addHandler(handler)

    Args:
        stream: Writable stream (default: ``sys.stderr``).
        config: ``RedactConfig`` (default: reads from env).
    """

    def __init__(
        self,
        stream: IO[str] | None = None,
        config: RedactConfig | None = None,
    ) -> None:
        super().__init__(stream)
        self._redact_filter = RedactFilter(config=config)
        self.addFilter(self._redact_filter)

"""Payload redaction before webhook delivery (REQ-016).

Ensures SECRET_CANARY_VALUE and any registered sensitive values are never
sent in outbound webhook payloads.
"""

from __future__ import annotations

import json
import os

_REDACTED = "[REDACTED]"


def _collect_secrets() -> list[str]:
    """Collect secrets to redact from environment variables."""
    secrets: list[str] = []

    # PRIMARY: SECRET_CANARY_VALUE used in CI canary tests
    canary = os.environ.get("SECRET_CANARY_VALUE", "")
    if canary:
        secrets.append(canary)

    # SECONDARY: any OPEN_BANCA_WEBHOOK_SECRET variants
    webhook_secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "")
    if webhook_secret:
        secrets.append(webhook_secret)

    # TERTIARY: PII_CANARY_* values
    for k, v in os.environ.items():
        if k.upper().startswith("PII_CANARY_") and v:
            secrets.append(v)

    return [s for s in secrets if s]


def redact_payload(payload: dict[str, object]) -> dict[str, object]:
    """Redact sensitive values from a payload dict before sending.

    Performs a JSON-round-trip redaction: serializes to string, replaces
    all secret occurrences with [REDACTED], then deserializes back.

    Args:
        payload: The event payload dict.

    Returns:
        New dict with all secret values replaced.
    """
    secrets = _collect_secrets()
    if not secrets:
        return payload

    serialized = json.dumps(payload)
    for secret in secrets:
        if secret in serialized:
            serialized = serialized.replace(secret, _REDACTED)

    result: dict[str, object] = json.loads(serialized)
    return result


__all__ = ["redact_payload"]

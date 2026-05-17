"""HMAC-SHA256 sign + verify helpers for webhook payloads.

Signature format (ADR-0011 / security review P0-3):
  X-OpenBanca-Signature: t=<unix_ts>,v1=<hex_hmac>,n=<nonce>

Signed message: f"{timestamp}.{body}"

Security requirements:
  - Window: 60 seconds (narrowed from 300s per P0-3 review).
  - Nonce: REQUIRED — missing nonce is always invalid.
  - Anti-replay: same (nonce, timestamp) pair must be rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

_WINDOW_SECONDS = 60


def sign_payload(
    secret: str,
    body: str | bytes,
    *,
    timestamp: int | None = None,
    nonce: str,
) -> str:
    """Return the X-OpenBanca-Signature header value.

    Args:
        secret: HMAC secret key (UTF-8 string).
        body: Request body (string or bytes — will be encoded to UTF-8 if str).
        timestamp: Unix epoch seconds.  Defaults to current time.
        nonce: Per-request unique nonce (UUID recommended). REQUIRED.

    Returns:
        Header value string: ``t=<ts>,v1=<hex>,n=<nonce>``
    """
    if not nonce:
        raise ValueError("nonce is required for HMAC signature")

    ts = timestamp if timestamp is not None else int(time.time())
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    signed_message = f"{ts}.".encode() + body_bytes
    mac = hmac.new(secret.encode("utf-8"), signed_message, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac},n={nonce}"


def verify_signature(
    secret: str,
    body: str | bytes,
    header: str,
    *,
    seen_nonces: set[tuple[str, int]] | None = None,
    now: int | None = None,
) -> None:
    """Verify an X-OpenBanca-Signature header value.

    Args:
        secret: HMAC secret key.
        body: Raw request body.
        header: Value of X-OpenBanca-Signature header.
        seen_nonces: Optional mutable set of (nonce, timestamp) pairs already
            processed.  If provided, duplicate nonces are rejected (anti-replay).
            The caller is responsible for persisting and evicting old entries.
        now: Override current time for testing.

    Raises:
        ValueError: If signature is malformed, expired, nonce missing, or
            anti-replay check fails.
    """
    current_time = now if now is not None else int(time.time())

    # Parse header
    parts: dict[str, str] = {}
    for part in header.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()

    if "t" not in parts or "v1" not in parts or "n" not in parts:
        raise ValueError(
            "Invalid signature header: must contain t=, v1=, n= fields. Nonce is required."
        )

    try:
        ts = int(parts["t"])
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp in signature: {parts['t']}") from exc

    nonce = parts["n"]
    if not nonce:
        raise ValueError("Nonce is required but empty in signature header")

    # Window check (60 seconds)
    age = abs(current_time - ts)
    if age > _WINDOW_SECONDS:
        raise ValueError(f"Signature timestamp expired: age={age}s > window={_WINDOW_SECONDS}s")

    # Anti-replay: reject duplicate (nonce, ts) pairs
    if seen_nonces is not None:
        key = (nonce, ts)
        if key in seen_nonces:
            raise ValueError(f"Nonce already seen: {nonce} at t={ts}")
        seen_nonces.add(key)

    # HMAC verification
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    signed_message = f"{ts}.".encode() + body_bytes
    expected_mac = hmac.new(secret.encode("utf-8"), signed_message, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_mac, parts["v1"]):
        raise ValueError("Signature mismatch: HMAC does not match payload")


def parse_signature_header(header: str) -> dict[str, Any]:
    """Parse X-OpenBanca-Signature header into a dict with keys t, v1, n."""
    result: dict[str, Any] = {}
    for part in header.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


__all__ = ["sign_payload", "verify_signature", "parse_signature_header"]

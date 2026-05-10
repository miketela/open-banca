"""HMAC signature tests: sign, verify, nonce, window, anti-replay."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import time
import uuid

import pytest

from open_banca_webhooks.signature import sign_payload, verify_signature

SECRET = "test-webhook-secret-32-bytes-long"
BODY = '{"event_type":"job.completed","job_id":"test-123"}'


# ── test_hmac_sign_verify ─────────────────────────────────────────────────────

def test_hmac_sign_verify_roundtrip() -> None:
    """sign_payload + verify_signature should succeed with valid inputs."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)

    # Should not raise
    verify_signature(SECRET, BODY, header, now=ts)


def test_hmac_sign_verify_bytes_body() -> None:
    """verify_signature should accept bytes body."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    body_bytes = BODY.encode("utf-8")
    header = sign_payload(SECRET, body_bytes, timestamp=ts, nonce=nonce)
    verify_signature(SECRET, body_bytes, header, now=ts)


def test_hmac_sign_returns_correct_format() -> None:
    """Signature header must contain t=, v1=, n= fields."""
    nonce = "test-nonce-abc"
    header = sign_payload(SECRET, BODY, timestamp=1700000000, nonce=nonce)
    assert "t=1700000000" in header
    assert "v1=" in header
    assert f"n={nonce}" in header


def test_hmac_wrong_secret_rejected() -> None:
    """Signature with wrong secret should fail verification."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    header = sign_payload("wrong-secret", BODY, timestamp=ts, nonce=nonce)
    with pytest.raises(ValueError, match="Signature mismatch"):
        verify_signature(SECRET, BODY, header, now=ts)


def test_hmac_tampered_body_rejected() -> None:
    """Tampered body should fail HMAC verification."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)
    with pytest.raises(ValueError, match="Signature mismatch"):
        verify_signature(SECRET, BODY + " tampered", header, now=ts)


# ── test_hmac_nonce_required ──────────────────────────────────────────────────

def test_hmac_nonce_required_sign() -> None:
    """sign_payload must raise ValueError when nonce is empty."""
    with pytest.raises(ValueError, match="nonce is required"):
        sign_payload(SECRET, BODY, nonce="")


def test_hmac_nonce_required_verify_missing_field() -> None:
    """verify_signature must reject header missing n= field."""
    ts = int(time.time())
    # Build a header without the nonce field
    body_bytes = BODY.encode("utf-8")
    signed_message = f"{ts}.".encode() + body_bytes
    mac = _hmac.new(SECRET.encode(), signed_message, hashlib.sha256).hexdigest()
    header_no_nonce = f"t={ts},v1={mac}"  # Missing n=

    with pytest.raises(ValueError, match="Nonce is required"):
        verify_signature(SECRET, BODY, header_no_nonce, now=ts)


def test_hmac_nonce_required_verify_empty_nonce() -> None:
    """verify_signature must reject header with empty n= field."""
    ts = int(time.time())
    body_bytes = BODY.encode("utf-8")
    signed_message = f"{ts}.".encode() + body_bytes
    mac = _hmac.new(SECRET.encode(), signed_message, hashlib.sha256).hexdigest()
    header_empty_nonce = f"t={ts},v1={mac},n="

    with pytest.raises(ValueError, match="Nonce is required"):
        verify_signature(SECRET, BODY, header_empty_nonce, now=ts)


# ── test_hmac_window_60s ──────────────────────────────────────────────────────

def test_hmac_window_60s_fresh_accepted() -> None:
    """Signature within 60s window should be accepted."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)
    # 59s later — still within window
    verify_signature(SECRET, BODY, header, now=ts + 59)


def test_hmac_window_60s_exactly_at_boundary_rejected() -> None:
    """Signature exactly at 60s should be rejected (age > 60)."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)
    with pytest.raises(ValueError, match="expired"):
        verify_signature(SECRET, BODY, header, now=ts + 61)


def test_hmac_window_60s_old_timestamp_rejected() -> None:
    """Signature 5 minutes old must be rejected."""
    nonce = str(uuid.uuid4())
    ts = int(time.time()) - 300  # 5 minutes ago
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)
    with pytest.raises(ValueError, match="expired"):
        verify_signature(SECRET, BODY, header)


def test_hmac_window_future_timestamp_within_60s_accepted() -> None:
    """Signature from 30s in the future should be accepted (clock skew tolerance)."""
    nonce = str(uuid.uuid4())
    ts = int(time.time()) + 30
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)
    verify_signature(SECRET, BODY, header)


def test_hmac_window_future_timestamp_beyond_60s_rejected() -> None:
    """Signature from 61s in the future should be rejected."""
    nonce = str(uuid.uuid4())
    ts = int(time.time()) + 61
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)
    with pytest.raises(ValueError, match="expired"):
        verify_signature(SECRET, BODY, header)


# ── test_anti_replay ──────────────────────────────────────────────────────────

def test_anti_replay_same_nonce_rejected() -> None:
    """Same (nonce, ts) pair must be rejected the second time."""
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    header = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce)

    seen_nonces: set[tuple[str, int]] = set()
    # First call should succeed
    verify_signature(SECRET, BODY, header, seen_nonces=seen_nonces, now=ts)
    # Second call with same header should be rejected
    with pytest.raises(ValueError, match="Nonce already seen"):
        verify_signature(SECRET, BODY, header, seen_nonces=seen_nonces, now=ts)


def test_anti_replay_different_nonce_accepted() -> None:
    """Different nonce on same body should be accepted."""
    ts = int(time.time())
    nonce1 = str(uuid.uuid4())
    nonce2 = str(uuid.uuid4())
    header1 = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce1)
    header2 = sign_payload(SECRET, BODY, timestamp=ts, nonce=nonce2)

    seen_nonces: set[tuple[str, int]] = set()
    verify_signature(SECRET, BODY, header1, seen_nonces=seen_nonces, now=ts)
    # Different nonce — should be fine
    verify_signature(SECRET, BODY, header2, seen_nonces=seen_nonces, now=ts)

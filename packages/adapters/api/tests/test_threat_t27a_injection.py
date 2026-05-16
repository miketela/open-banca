"""Tests for T27a — injection defense in POST /jobs/{id}/human-input (ADR-0021).

TDD coverage per threat-model T27a:
  - answer with XSS-like content: HTML tags are permitted by charset rules,
    but Unicode direction marks and Cc control chars are rejected.
  - answer > 256 bytes UTF-8 is rejected (length cap).
  - answer with SQL injection-like patterns: generally allowed chars, BUT
    NULL bytes (\\x00) are Cc category and rejected.
  - answer with embed direction mark U+202E (Cf category) is rejected.
  - answer with valid accented text is accepted.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from open_banca_api.schemas.jobs import HumanInputRequest

# ── Length cap ────────────────────────────────────────────────────────────────


def test_answer_exactly_256_bytes_accepted() -> None:
    """256 bytes UTF-8 is the limit — exactly 256 is accepted."""
    answer = "a" * 256
    req = HumanInputRequest(field_key="security_q_test", answer=answer)
    assert req.answer == answer


def test_answer_257_bytes_rejected() -> None:
    """257 bytes UTF-8 exceeds limit."""
    answer = "a" * 257
    with pytest.raises(ValidationError, match="256"):
        HumanInputRequest(field_key="security_q_test", answer=answer)


def test_answer_256_utf8_bytes_multibyte_chars_accepted() -> None:
    """256 UTF-8 bytes with multibyte chars (64 × 4-byte chars) is at limit."""
    # Each '𝕳' is 4 bytes in UTF-8
    answer = "\U0001d573" * 64  # 64 × 4 = 256 bytes
    req = HumanInputRequest(field_key="security_q_test", answer=answer)
    assert len(req.answer.encode("utf-8")) == 256


def test_answer_257_utf8_bytes_multibyte_rejected() -> None:
    """65 × 4-byte chars = 260 bytes > 256."""
    answer = "\U0001d573" * 65
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer=answer)


# ── Charset: Control characters (Cc) ─────────────────────────────────────────


def test_null_byte_in_answer_rejected() -> None:
    """NULL byte (U+0000, category Cc) must be rejected (T27a)."""
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer="valid\x00suffix")


def test_sql_injection_like_null_rejected() -> None:
    """SQL injection using NULL terminator is rejected."""
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer="' OR 1=1\x00")


def test_carriage_return_rejected() -> None:
    """Carriage return (\\r, Cc category) is rejected."""
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer="line1\rline2")


def test_newline_rejected() -> None:
    """Newline (\\n, Cc category) is rejected."""
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer="line1\nline2")


# ── Charset: Format characters (Cf) ──────────────────────────────────────────


def test_embed_direction_mark_rejected() -> None:
    """Right-to-left override U+202E (Cf category) is rejected (T27a)."""
    answer_with_rtlo = "Fluffy‮"
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer=answer_with_rtlo)


def test_zero_width_no_break_space_rejected() -> None:
    """U+FEFF (zero-width no-break space, Cf category) is rejected."""
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="security_q_test", answer="valid﻿text")


# ── Valid answers ─────────────────────────────────────────────────────────────


def test_plain_ascii_answer_accepted() -> None:
    req = HumanInputRequest(field_key="security_q_test", answer="Fluffy")
    assert req.answer == "Fluffy"


def test_answer_with_accents_accepted() -> None:
    """Spanish accented text is valid."""
    req = HumanInputRequest(field_key="security_q_test", answer="Canción favorita")
    assert "ó" in req.answer


def test_answer_with_numbers_and_symbols_accepted() -> None:
    req = HumanInputRequest(field_key="security_q_test", answer="123-ABC!")
    assert req.answer == "123-ABC!"


def test_html_tags_in_answer_accepted() -> None:
    """HTML tags contain < > which are Ps/Pe (punctuation) — allowed by charset.
    Protection against XSS is the bank's responsibility (T27a documents this)."""
    req = HumanInputRequest(field_key="security_q_test", answer="<script>alert(1)</script>")
    assert "<script>" in req.answer


def test_sql_like_answer_without_null_accepted() -> None:
    """' OR 1=1' without NULL byte is allowed chars (Ps/Ll/Nd)."""
    req = HumanInputRequest(field_key="security_q_test", answer="' OR 1=1")
    assert req.answer == "' OR 1=1"


# ── Field key validation ──────────────────────────────────────────────────────


def test_field_key_regex_valid() -> None:
    req = HumanInputRequest(field_key="security_q_pet", answer="Fluffy")
    assert req.field_key == "security_q_pet"


def test_field_key_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="ab", answer="Fluffy")


def test_field_key_uppercase_rejected() -> None:
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="SecurityQ", answer="Fluffy")


def test_field_key_starts_with_number_rejected() -> None:
    with pytest.raises(ValidationError):
        HumanInputRequest(field_key="1security_q", answer="Fluffy")

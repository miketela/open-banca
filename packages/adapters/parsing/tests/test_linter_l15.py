"""Tests for linter rule L15 — prompt_user step validation (ADR-0021).

TDD coverage:
1. prompt_user without question_selector → L15 error.
2. prompt_user with invalid field_key regex → L15 error.
3. prompt_user with duplicate question_selectors within map → L15 error.
4. prompt_user without selector (answer input) → L15 error.
5. Valid prompt_user step passes L15.
6. Map with no prompt_user steps passes L15 (no false positives).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_banca_parsing.community.linter import MapLinter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_MAP: dict = {
    "bank_id": "test_bank",
    "version": "1.0.0",
    "schema_version": "1",
    "signature": None,
    "steps": [
        {
            "step_id": "login_navigate",
            "action": "navigate",
            "url": "https://www.testbank.com/login",
        },
    ],
}

_BASE_PARSER: dict = {
    "version": "1.0.0",
    "sheets": [],
}


def _write_bank_dir(tmp_path: Path, *, map_steps: list[dict], parser: dict | None = None) -> Path:
    bank_dir = tmp_path / "test_bank"
    bank_dir.mkdir()

    full_map = dict(_BASE_MAP)
    full_map["steps"] = list(_BASE_MAP["steps"]) + map_steps
    (bank_dir / "map.json").write_text(json.dumps(full_map), encoding="utf-8")

    parser_data = parser or _BASE_PARSER
    (bank_dir / "parser.json").write_text(json.dumps(parser_data), encoding="utf-8")
    return bank_dir


def _lint(tmp_path: Path, *, extra_steps: list[dict]) -> list[str]:
    """Run linter and return list of rule codes that triggered."""
    bank_dir = _write_bank_dir(tmp_path, map_steps=extra_steps)
    result = MapLinter().lint(bank_dir)
    return [e.rule for e in result.errors]


# ---------------------------------------------------------------------------
# L15 tests
# ---------------------------------------------------------------------------


def test_l15_missing_question_selector(tmp_path: Path) -> None:
    """prompt_user without question_selector triggers L15."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q",
                "action": "prompt_user",
                "selector": "#answer",
                # missing question_selector
                "field_key": "security_q_pet",
            }
        ],
    )
    assert "L15" in rules, f"Expected L15, got: {rules}"


def test_l15_invalid_field_key_too_short(tmp_path: Path) -> None:
    """field_key shorter than 3 chars triggers L15."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q",
                "action": "prompt_user",
                "selector": "#answer",
                "question_selector": ".question-text",
                "field_key": "ab",  # too short (minimum 3 chars after first)
            }
        ],
    )
    assert "L15" in rules, f"Expected L15 for short field_key, got: {rules}"


def test_l15_invalid_field_key_uppercase(tmp_path: Path) -> None:
    """field_key with uppercase letters triggers L15."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q",
                "action": "prompt_user",
                "selector": "#answer",
                "question_selector": ".question-text",
                "field_key": "SecurityQMother",  # uppercase not allowed
            }
        ],
    )
    assert "L15" in rules, f"Expected L15 for uppercase field_key, got: {rules}"


def test_l15_duplicate_question_selectors(tmp_path: Path) -> None:
    """Two prompt_user steps sharing the same question_selector triggers L15."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q1",
                "action": "prompt_user",
                "selector": "#answer1",
                "question_selector": ".same-selector",
                "field_key": "security_q_first",
            },
            {
                "step_id": "ask_security_q2",
                "action": "prompt_user",
                "selector": "#answer2",
                "question_selector": ".same-selector",  # duplicate!
                "field_key": "security_q_second",
            },
        ],
    )
    assert "L15" in rules, f"Expected L15 for duplicate question_selector, got: {rules}"


def test_l15_missing_answer_selector(tmp_path: Path) -> None:
    """prompt_user without selector (answer input) triggers L15."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q",
                "action": "prompt_user",
                # missing selector
                "question_selector": ".question-text",
                "field_key": "security_q_pet",
            }
        ],
    )
    assert "L15" in rules, f"Expected L15 for missing selector, got: {rules}"


def test_l15_valid_prompt_user_passes(tmp_path: Path) -> None:
    """A correctly-formed prompt_user step passes L15 with no errors."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q",
                "action": "prompt_user",
                "selector": "#security-answer",
                "question_selector": ".security-question-text",
                "field_key": "security_q_mother_color",
                "cache_answers": True,
                "timeout_s": 240,
            }
        ],
    )
    assert "L15" not in rules, f"Valid prompt_user should not trigger L15, got: {rules}"


def test_l15_no_false_positives_for_other_steps(tmp_path: Path) -> None:
    """Maps with no prompt_user steps do not trigger L15."""
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "fill_user",
                "action": "fill",
                "selector": "input#username",
                "value_ref": "personal:username",
            }
        ],
    )
    assert "L15" not in rules, f"Non-prompt_user steps should not trigger L15, got: {rules}"


def test_l15_field_key_too_long(tmp_path: Path) -> None:
    """field_key longer than 33 chars triggers L15."""
    long_key = "security_q_" + "x" * 25  # total > 33 chars
    rules = _lint(
        tmp_path,
        extra_steps=[
            {
                "step_id": "ask_security_q",
                "action": "prompt_user",
                "selector": "#answer",
                "question_selector": ".question",
                "field_key": long_key,
            }
        ],
    )
    assert "L15" in rules, f"Expected L15 for too-long field_key, got: {rules}"

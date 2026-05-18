"""Tests for DOM question extraction in prompt_user."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from open_banca_browser.errors import SelectorNotFound
from open_banca_browser.step_executors.prompt_user import (
    _pick_best_question,
    extract_question_text,
)


def test_pick_best_rejects_respuesta_label() -> None:
    best = _pick_best_question(
        [
            "Respuesta",
            "¿Cuál es el apodo de tu abuelo paterno?",
            "Validar",
        ]
    )
    assert best == "¿Cuál es el apodo de tu abuelo paterno?"


def test_extract_prefers_question_with_mark() -> None:
    root = MagicMock()
    answer = MagicMock()
    answer.count.return_value = 1
    answer.first.wait_for = MagicMock()

    question_p = MagicMock()
    question_p.count.return_value = 1
    question_p.nth.return_value.inner_text.return_value = "¿Cuál es su primer empleo?"

    def locator(sel: str) -> MagicMock:
        if sel == "#answer":
            return answer
        if "?" in sel or "pregunta" in sel:
            return question_p
        empty = MagicMock()
        empty.count.return_value = 0
        return empty

    root.locator.side_effect = locator
    answer.first.evaluate = MagicMock(return_value="")

    text = extract_question_text(
        root,
        question_selector="p:has-text('?')",
        answer_selector="#answer",
        question_wait_ms=1000,
    )
    assert "primer empleo" in text


def test_extract_raises_when_answer_field_missing() -> None:
    root = MagicMock()
    answer = MagicMock()
    answer.count.return_value = 0
    answer.first.wait_for.side_effect = Exception("timeout")
    root.locator.return_value = answer

    with pytest.raises(Exception, match="timeout"):
        extract_question_text(
            root,
            question_selector="label",
            answer_selector="#answer",
            question_wait_ms=100,
        )

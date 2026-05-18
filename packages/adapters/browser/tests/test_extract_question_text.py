"""Tests for DOM question extraction in prompt_user."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from open_banca_browser.errors import SelectorNotFound
from open_banca_browser.step_executors.prompt_user import extract_question_text


def test_extract_prefers_first_matching_selector() -> None:
    root = MagicMock()
    answer = MagicMock()
    answer.count.return_value = 1
    answer.first.wait_for = MagicMock()

    label = MagicMock()
    label.count.return_value = 1
    label.nth.return_value.inner_text.return_value = "¿Cuál es su primer empleo?"

    def locator(sel: str) -> MagicMock:
        if sel == "#answer":
            return answer
        if "label" in sel:
            return label
        empty = MagicMock()
        empty.count.return_value = 0
        return empty

    root.locator.side_effect = locator
    answer.first.evaluate = MagicMock()

    text = extract_question_text(
        root,
        question_selector="label[for='answer']",
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

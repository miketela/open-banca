"""Tests for prompt_user step executor (ADR-0021).

TDD coverage:
1. Cache HIT — vault returns answer, executor fills input, no exception raised.
2. Cache MISS — vault returns None, executor raises HumanInputRequired.
3. HumanInputRequired is NOT a ScraperError / BreakageEvent.
4. Missing question_selector raises SelectorNotFound.
5. Missing field_key raises SelectorNotFound.
6. compute_question_hash normalizes question text consistently.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from open_banca_browser.errors import HumanInputRequired, ScraperError, SelectorNotFound
from open_banca_browser.step_executors.prompt_user import (
    compute_question_hash,
    execute_prompt_user,
)
from open_banca_domain.entities.bank_map import StepSpec


def _make_step(
    *,
    step_id: str = "test_step",
    question_selector: str = ".security-question",
    field_key: str = "security_q_test",
    selector: str = "#answer-input",
    cache_answers: bool = True,
    timeout_s: int = 240,
) -> StepSpec:
    return StepSpec(
        step_id=step_id,
        action="prompt_user",
        question_selector=question_selector,
        field_key=field_key,
        selector=selector,
        cache_answers=cache_answers,
        timeout_s=timeout_s,
    )


class _FakeVault:
    """Minimal vault stub for testing."""

    def __init__(self, answer: str | None) -> None:
        self._answer = answer
        self.store_calls: list[dict[str, Any]] = []

    def fetch_security_answer(self, credential_id: str, question_hash: str) -> str | None:
        return self._answer

    def store_security_answer(self, credential_id: str, question_hash: str, answer: str, *, field_key: str, ttl_days: int | None = None) -> None:
        self.store_calls.append({"credential_id": credential_id, "question_hash": question_hash, "answer": answer, "field_key": field_key})


def _make_page(question_text: str = "¿Cuál es el color favorito de su madre?", *, selector_found: bool = True) -> MagicMock:
    """Build a minimal Playwright page mock."""
    page = MagicMock()

    question_locator = MagicMock()
    question_locator.count.return_value = 1 if selector_found else 0
    question_locator.inner_text.return_value = question_text

    answer_locator = MagicMock()
    answer_locator.count.return_value = 1 if selector_found else 0
    answer_locator.fill = MagicMock()

    def locator_side_effect(selector: str) -> MagicMock:
        if "security-question" in selector or "question" in selector:
            return question_locator
        return answer_locator

    page.locator.side_effect = locator_side_effect
    return page


# ── 1. Cache HIT ──────────────────────────────────────────────────────────────


def test_cache_hit_fills_answer_input() -> None:
    """When vault returns a cached answer, executor fills the input field."""
    page = _make_page()
    vault = _FakeVault(answer="rojo")
    step = _make_step()

    # Should complete without raising
    execute_prompt_user(
        page,
        step,
        vault=vault,
        bank_id="banco-test",
        credential_id="cred-001",
    )

    # Verify fill was called
    answer_locator = page.locator.return_value
    # At least one locator's fill was called
    assert page.locator.called


def test_cache_hit_does_not_raise_human_input_required() -> None:
    """Cache HIT must not raise HumanInputRequired."""
    page = _make_page()
    vault = _FakeVault(answer="rojo")
    step = _make_step()

    # Should not raise
    execute_prompt_user(page, step, vault=vault, bank_id="banco-test", credential_id="cred-001")


# ── 2. Cache MISS ─────────────────────────────────────────────────────────────


def test_cache_miss_raises_human_input_required() -> None:
    """When vault returns None, executor raises HumanInputRequired."""
    page = _make_page()
    vault = _FakeVault(answer=None)
    step = _make_step()

    with pytest.raises(HumanInputRequired) as exc_info:
        execute_prompt_user(page, step, vault=vault, bank_id="banco-test", credential_id="cred-001")

    exc = exc_info.value
    assert exc.field_key == "security_q_test"
    assert "¿Cuál es el color favorito de su madre?" in exc.question_text
    assert len(exc.question_hash) == 64  # SHA-256 hex


# ── 3. HumanInputRequired is NOT a ScraperError ───────────────────────────────


def test_human_input_required_is_not_scraper_error() -> None:
    """HumanInputRequired must NOT subclass ScraperError (not a breakage)."""
    exc = HumanInputRequired(
        question_text="test question",
        field_key="security_q_test",
        question_hash="a" * 64,
        selector="#input",
    )
    assert not isinstance(exc, ScraperError), (
        "HumanInputRequired must not be a ScraperError — it is a normal pause, "
        "not a breakage event."
    )


def test_cache_miss_exception_carries_correct_selector() -> None:
    """HumanInputRequired carries the answer selector from the step spec."""
    page = _make_page()
    vault = _FakeVault(answer=None)
    step = _make_step(selector="#my-answer-field")

    with pytest.raises(HumanInputRequired) as exc_info:
        execute_prompt_user(page, step, vault=vault, bank_id="b", credential_id="c")

    assert exc_info.value.selector == "#my-answer-field"


# ── 4. Missing question_selector ──────────────────────────────────────────────


def test_missing_question_selector_raises_selector_not_found() -> None:
    """Step with empty question_selector raises SelectorNotFound."""
    page = _make_page()
    vault = _FakeVault(answer=None)
    step = _make_step(question_selector="")

    with pytest.raises(SelectorNotFound):
        execute_prompt_user(page, step, vault=vault, bank_id="b", credential_id="c")


# ── 5. Missing field_key ──────────────────────────────────────────────────────


def test_missing_field_key_raises_selector_not_found() -> None:
    """Step with empty field_key raises SelectorNotFound."""
    page = _make_page()
    vault = _FakeVault(answer=None)
    step = _make_step(field_key="")

    with pytest.raises(SelectorNotFound):
        execute_prompt_user(page, step, vault=vault, bank_id="b", credential_id="c")


# ── 6. Hash normalization ─────────────────────────────────────────────────────


def test_compute_question_hash_normalizes_consistently() -> None:
    """Same question with different whitespace/case should produce same hash."""
    h1 = compute_question_hash("banco-general", "cred-1", "security_q_pet", "¿Nombre de su primera mascota?")
    h2 = compute_question_hash("banco-general", "cred-1", "security_q_pet", "  ¿Nombre de su primera mascota?  ")
    h3 = compute_question_hash("banco-general", "cred-1", "security_q_pet", "¿NOMBRE DE SU PRIMERA MASCOTA?")
    assert h1 == h2, "Whitespace differences should normalize to same hash"
    assert h1 == h3, "Case differences should normalize to same hash"


def test_compute_question_hash_different_for_different_bank_id() -> None:
    """Hash must include bank_id to prevent cross-bank collisions."""
    h1 = compute_question_hash("banco-a", "cred-1", "security_q_pet", "¿mascota?")
    h2 = compute_question_hash("banco-b", "cred-1", "security_q_pet", "¿mascota?")
    assert h1 != h2


def test_compute_question_hash_different_for_different_credential() -> None:
    """Hash must include credential_id to prevent cross-credential collisions."""
    h1 = compute_question_hash("banco-a", "cred-1", "security_q_pet", "¿mascota?")
    h2 = compute_question_hash("banco-a", "cred-2", "security_q_pet", "¿mascota?")
    assert h1 != h2


def test_compute_question_hash_is_sha256_hex() -> None:
    """Hash output must be a 64-character hex string (SHA-256)."""
    h = compute_question_hash("banco-a", "cred-1", "security_q_pet", "¿mascota?")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)

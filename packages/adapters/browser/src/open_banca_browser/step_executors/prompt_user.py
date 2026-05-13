"""prompt_user step executor — vault-cached answer fill or HumanInputRequired pause.

ADR-0021: step_type ``prompt_user`` allows the scraper to ask for user input
(e.g. security questions) without classifying the pause as a breakage.

Flow:
1. Read ``question_selector`` text from the DOM to get the question text.
2. Compute SHA-256 hash of the cache key: sha256(bank_id:credential_id:field_key:normalize(question_text)).
3. Call ``vault.fetch_security_answer(credential_id, question_hash)``.
   - Cache HIT  → fill ``selector`` input with the answer, log ``prompt_user_cache_hit``.
   - Cache MISS → raise ``HumanInputRequired`` (NOT a BreakageEvent).

HumanInputRequired is a normal pause, not an error. The Temporal layer handles it
by invoking HumanInputAwaitActivity and emitting webhook ``job.human_input_required``.
"""
from __future__ import annotations

import hashlib
import logging
import unicodedata
from collections.abc import Callable
from typing import Any, Protocol

from open_banca_browser.errors import HumanInputRequired, SelectorNotFound, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec

logger = logging.getLogger(__name__)


class SecurityAnswerVault(Protocol):
    """Protocol expected from vault object passed to execute_prompt_user."""

    def fetch_security_answer(self, credential_id: str, question_hash: str) -> str | None:
        ...

    def store_security_answer(
        self,
        credential_id: str,
        question_hash: str,
        answer: str,
        *,
        field_key: str,
        ttl_days: int | None,
    ) -> None:
        ...


def _normalize_question(text: str) -> str:
    """Normalize question text for stable cache key computation.

    Rules (pinned by ADR-0021):
      - NFC normalization
      - Lowercase
      - Strip Unicode punctuation (category P*)
      - Collapse whitespace
    """
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    # Strip Unicode punctuation
    text = "".join(
        ch for ch in text if not unicodedata.category(ch).startswith("P")
    )
    # Collapse whitespace
    return " ".join(text.split())


def compute_question_hash(bank_id: str, credential_id: str, field_key: str, question_text: str) -> str:
    """Compute the SHA-256 cache key for a security question.

    Cache key composition (ADR-0021):
        sha256(bank_id || ":" || credential_id || ":" || field_key || ":" || normalize(question_text))
    """
    normalized = _normalize_question(question_text)
    raw = f"{bank_id}:{credential_id}:{field_key}:{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def execute_prompt_user(
    page: Any,
    step: StepSpec,
    *,
    vault: SecurityAnswerVault,
    bank_id: str,
    credential_id: str,
) -> None:
    """Execute a prompt_user step.

    StepSpec extra fields (required):
        question_selector (str): CSS/XPath selector for the element showing the question text.
        field_key (str): Stable identifier for this question (regex ``^[a-z][a-z0-9_]{2,32}$``).
        selector (str): CSS/XPath selector for the answer input field.

    StepSpec extra fields (optional):
        cache_answers (bool): Whether to cache the answer. Default ``True``.
        timeout_s (int): Seconds before the human-wait times out. Default 240.

    Raises:
        SelectorNotFound: question_selector or answer selector not found.
        HumanInputRequired: Cache miss — caller (Temporal activity) must pause and request input.
            This is NOT a BreakageEvent — do NOT log it as ``scraper_breakage``.
        StepTimeout: Timeout waiting for DOM elements.
    """
    params = extra(step)
    question_selector: str = params.get("question_selector", "")
    field_key: str = params.get("field_key", "")
    answer_selector: str = params.get("selector", "")
    cache_answers: bool = params.get("cache_answers", True)
    timeout_s: int = int(params.get("timeout_s", 240))

    if not question_selector:
        raise SelectorNotFound(f"prompt_user step {step.step_id!r}: missing question_selector")
    if not field_key:
        raise SelectorNotFound(f"prompt_user step {step.step_id!r}: missing field_key")
    if not answer_selector:
        raise SelectorNotFound(f"prompt_user step {step.step_id!r}: missing selector")

    # 1. Extract question text from DOM
    try:
        locator = page.locator(question_selector)
        if locator.count() == 0:
            raise SelectorNotFound(
                f"prompt_user: question_selector not found: {question_selector!r}"
            )
        question_text: str = locator.inner_text(timeout=5_000).strip()
    except SelectorNotFound:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"prompt_user: timeout reading question_selector {question_selector!r}") from exc
        raise SelectorNotFound(f"prompt_user: error reading question_selector: {exc}") from exc

    # 2. Compute cache key hash
    question_hash = compute_question_hash(bank_id, credential_id, field_key, question_text)

    # 3. Attempt vault cache lookup
    cached_answer: str | None = None
    if cache_answers:
        try:
            cached_answer = vault.fetch_security_answer(credential_id, question_hash)
        except Exception:
            logger.warning(
                "prompt_user: vault lookup failed for field_key=%s (treating as miss)",
                field_key,
            )

    if cached_answer is not None:
        # Cache HIT — fill the input and continue
        logger.info(
            "prompt_user: cache hit field_key=%s step_id=%s",
            field_key,
            step.step_id,
        )
        try:
            ans_locator = page.locator(answer_selector)
            if ans_locator.count() == 0:
                raise SelectorNotFound(
                    f"prompt_user: answer selector not found: {answer_selector!r}"
                )
            ans_locator.fill(cached_answer, timeout=10_000)
        except SelectorNotFound:
            raise
        except Exception as exc:
            msg = str(exc).lower()
            if "timeout" in msg:
                raise StepTimeout(f"prompt_user: fill timeout on {answer_selector!r}") from exc
            raise StepTimeout(f"prompt_user: fill error: {exc}") from exc
        finally:
            del cached_answer  # reduce time in memory
        return

    # Cache MISS — pause and request human input
    # DO NOT raise a ScraperError / build a BreakageEvent here.
    # This is an expected workflow pause, not a failure.
    logger.info(
        "prompt_user: cache miss field_key=%s step_id=%s — raising HumanInputRequired",
        field_key,
        step.step_id,
    )
    raise HumanInputRequired(
        question_text=question_text,
        field_key=field_key,
        question_hash=question_hash,
        selector=answer_selector,
        timeout_s=timeout_s,
    )

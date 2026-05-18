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
from open_banca_browser.step_executors._utils import extra, root_locator
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


# Labels/copy that are NOT the security question (input hints, buttons, headers).
_GENERIC_QUESTION_PHRASES = frozenset(
    {
        "respuesta",
        "answer",
        "tu respuesta",
        "your answer",
        "validar",
        "ingresar en banca en línea",
        "ingresar en banca en linea",
        "abrir página web",
        "abrir pagina web",
        "¿la olvidaste?",
        "la olvidaste",
    }
)

_DEFAULT_QUESTION_FALLBACKS = (
    "p:has-text('?')",
    "[class*='pregunta']",
    "[class*='question']",
    ".login-step p",
    "#answer ~ p",
    "legend",
)


def _is_valid_question_text(text: str) -> bool:
    """Reject input labels and short chrome; keep real question sentences."""
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) < 12:
        return False
    lower = cleaned.lower()
    if lower in _GENERIC_QUESTION_PHRASES:
        return False
    # Single-word field labels
    if " " not in cleaned and "?" not in cleaned and "¿" not in cleaned:
        return False
    return True


def _score_question_candidate(text: str) -> int:
    if not _is_valid_question_text(text):
        return -1
    score = len(text)
    if "?" in text or "¿" in text:
        score += 500
    return score


def _pick_best_question(candidates: list[str]) -> str | None:
    best_text: str | None = None
    best_score = -1
    for text in candidates:
        score = _score_question_candidate(text)
        if score > best_score:
            best_score = score
            best_text = text.strip()
    return best_text


def _split_selectors(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _wait_for_answer_field(root: Any, answer_selector: str, timeout_ms: int) -> None:
    loc = root.locator(answer_selector).first
    loc.wait_for(state="attached", timeout=timeout_ms)


def _extract_from_answer_context(root: Any, answer_selector: str) -> list[str]:
    """Parse lines of text near the answer field (excludes input chrome)."""
    loc = root.locator(answer_selector)
    if loc.count() == 0:
        return []
    raw: str = loc.first.evaluate(
        """(el) => {
        const block = el.closest("form") || el.closest("main") || el.closest("[role='main']")
            || document.body;
        if (!block) return "";
        const clone = block.cloneNode(true);
        clone.querySelectorAll("input,button,select,textarea,label").forEach((n) => n.remove());
        return (clone.innerText || "").trim();
    }"""
    )
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def extract_question_text(
    root: Any,
    *,
    question_selector: str,
    answer_selector: str,
    question_wait_ms: int = 20_000,
    question_fallback_selectors: tuple[str, ...] = _DEFAULT_QUESTION_FALLBACKS,
) -> str:
    """Wait for the answer field, then resolve visible question copy from the DOM."""
    _wait_for_answer_field(root, answer_selector, question_wait_ms)

    candidates: list[str] = []
    for sel in _split_selectors(question_selector) + list(question_fallback_selectors):
        loc = root.locator(sel)
        count = loc.count()
        if count == 0:
            continue
        for idx in range(min(count, 8)):
            try:
                text = loc.nth(idx).inner_text(timeout=3_000).strip()
            except Exception:
                continue
            if text:
                candidates.append(text)

    candidates.extend(_extract_from_answer_context(root, answer_selector))

    best = _pick_best_question(candidates)
    if best:
        return best

    raise SelectorNotFound(
        f"prompt_user: could not extract question text near {answer_selector!r}"
    )


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

    root = root_locator(page, step)
    question_wait_ms = int(params.get("question_wait_ms", 20_000))

    # 1. Wait for answer field, then extract question text from DOM
    try:
        question_text = extract_question_text(
            root,
            question_selector=question_selector,
            answer_selector=answer_selector,
            question_wait_ms=question_wait_ms,
        )
    except SelectorNotFound:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(
                f"prompt_user: timeout waiting for answer field {answer_selector!r}"
            ) from exc
        raise SelectorNotFound(f"prompt_user: error extracting question: {exc}") from exc

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
            ans_locator = root.locator(answer_selector)
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
    frame_selector: str = params.get("frame_selector") or params.get("iframe_selector") or ""
    raise HumanInputRequired(
        question_text=question_text,
        field_key=field_key,
        question_hash=question_hash,
        selector=answer_selector,
        timeout_s=timeout_s,
        frame_selector=frame_selector,
    )


def fill_prompt_user_answer(
    page: Any,
    selector: str,
    answer: str,
    *,
    frame_selector: str = "",
) -> None:
    """Fill the answer input after human input was delivered."""
    try:
        root = (
            page.frame_locator(frame_selector)
            if frame_selector
            else page
        )
        ans_locator = root.locator(selector)
        if ans_locator.count() == 0:
            raise SelectorNotFound(f"prompt_user: answer selector not found: {selector!r}")
        ans_locator.fill(answer, timeout=10_000)
    except SelectorNotFound:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"prompt_user: fill timeout on {selector!r}") from exc
        raise StepTimeout(f"prompt_user: fill error: {exc}") from exc

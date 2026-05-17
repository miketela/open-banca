"""MapperAgent — browser-use + Claude Sonnet 4.6 vision via LiteLLM.

Architecture:
  1. ChatLiteLLM (browser_use) wraps the actual Claude API call.
  2. PIIRedactingChatModel wraps ChatLiteLLM to apply ADR-0020 filters.
  3. CostTrackingChatModel wraps PIIRedactingChatModel to enforce $0.50 cap.
  4. browser-use Agent uses the wrapped model; sensitive_data keeps creds opaque.
  5. After the Agent finishes, the raw output is parsed into domain.BankMap.
  6. Self-test dry-run on ScraperRunner validates the map before returning.

REQ-003 (Mapper), REQ-011 (cost cap), ADR-0014, ADR-0020.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar, overload

from browser_use.llm.messages import BaseMessage
from browser_use.llm.views import ChatInvokeCompletion
from pydantic import BaseModel

from open_banca_domain.entities.bank_map import BankMap
from open_banca_llm.mapper.cost_tracker import (
    DEFAULT_COST_CAP_USD,
    DEFAULT_WALLCLOCK_SECONDS,
    CostTracker,
)
from open_banca_llm.mapper.errors import (
    CostExceeded,
    MapperError,
    SelfTestFailed,
    WallclockExceeded,
)
from open_banca_llm.mapper.pii_filter_adapter import PIIRedactingChatModel, PiiRegion
from open_banca_llm.mapper.prompts import SYSTEM_PROMPT, build_task_prompt
from open_banca_observability.redact import RedactConfig


def _build_cost_counter() -> Any | None:
    """Return the ``llm_cost_usd_total`` OTel counter when OTel is enabled.

    Returns ``None`` (no-op) when ``OPEN_BANCA_OTEL_ENABLED`` is not set or
    when the OTel package is unavailable so the mapper never crashes due to
    observability infrastructure failures.
    """
    import os  # type: ignore[import-untyped]

    if os.environ.get("OPEN_BANCA_OTEL_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return None
    try:
        from open_banca_observability.metrics import (  # type: ignore[import-untyped]
            get_instruments,
            setup_meter,
        )

        return get_instruments(setup_meter("llm")).llm_cost_usd_total
    except Exception:  # type: ignore[broad-except]
        return None  # OTel setup failure must not crash the mapper

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Default Claude Sonnet 4.6 model string for LiteLLM.
_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

# Placeholder keys used in browser-use sensitive_data.
_PLACEHOLDER_USERNAME = "<USERNAME>"
_PLACEHOLDER_PASSWORD = "<PASSWORD>"


# ── Cost-tracking wrapper ─────────────────────────────────────────────────────


class CostTrackingChatModel:
    """Wraps a BaseChatModel; records token usage after each call.

    On every ainvoke() response, extracts usage from ChatInvokeCompletion
    and forwards to a CostTracker.  If the tracker raises CostExceeded or
    WallclockExceeded, the exception propagates to the caller (browser-use
    agent loop or should_stop callback).
    """

    # Required by BaseChatModel Protocol
    _verified_api_keys: bool = False

    def __init__(
        self,
        inner: Any,
        tracker: CostTracker,
        cost_counter: Any | None = None,
        agent_name: str = "mapper",
    ) -> None:
        """Create a CostTrackingChatModel.

        Args:
            inner: Wrapped BaseChatModel (e.g. PIIRedactingChatModel).
            tracker: CostTracker for cap enforcement.
            cost_counter: Optional OTel Counter for ``llm_cost_usd_total``.
                          When provided, emits a measurement on every LLM call.
                          Pass ``None`` (default) to disable metric emission.
            agent_name: Label used in the ``agent`` attribute of the metric.
        """
        self._inner: Any = inner
        self._tracker = tracker
        self._cost_counter: Any | None = cost_counter
        self._agent_name = agent_name

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def model_name(self) -> str:
        """Proxy attribute required by browser-use telemetry (cloud_events.py)."""
        return getattr(self._inner, "model_name", self._inner.model)

    @property
    def provider(self) -> str:
        return self._inner.provider  # type: ignore[attr-defined]

    @property
    def name(self) -> str:
        return self._inner.name  # type: ignore[attr-defined]

    @overload
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: type[T],
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        if output_format is not None:
            result: ChatInvokeCompletion[Any] = await self._inner.ainvoke(
                list(messages), output_format, **kwargs
            )
        else:
            result = await self._inner.ainvoke(list(messages), **kwargs)

        # Record usage after successful call
        if result.usage is not None:
            cost_usd = self._compute_cost(result)
            self._tracker.record(
                input_tokens=result.usage.prompt_tokens,
                output_tokens=result.usage.completion_tokens,
                cost_usd=cost_usd,
            )
            # Emit OTel metric when a counter has been injected (task #27).
            # Use cost_usd when available; fall back to tracker's estimate.
            if self._cost_counter is not None:
                emit_cost = cost_usd if cost_usd is not None else self._tracker.usage.cost_usd
                self._cost_counter.add(emit_cost, {"agent": self._agent_name})

        return result

    def _compute_cost(self, result: ChatInvokeCompletion[Any]) -> float | None:
        """Compute cost via the unified router (litellm pricing table)."""
        usage = result.usage
        if usage is None:
            return None
        from open_banca_llm.router import compute_cost as _router_compute_cost  # noqa: PLC0415

        cost = _router_compute_cost(
            self.model,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
        )
        return float(cost) if cost > 0 else None


# ── Fake model for tests (no real LLM calls) ─────────────────────────────────


@dataclass
class _FakeChatModelConfig:
    """Configuration for FakeChatModel responses."""

    responses: list[str] = field(default_factory=list)
    call_count: int = field(default=0, init=False)
    recorded_messages: list[list[BaseMessage]] = field(default_factory=list, init=False)


class FakeChatModel:
    """Implements BaseChatModel Protocol for tests — zero real LLM calls.

    Returns pre-configured response strings.  Used by test suite so that
    ``uv run pytest`` never makes real API calls.

    Args:
        responses: Sequence of string responses to return in order.
                   Last response is repeated when the list is exhausted.
    """

    # Required by BaseChatModel Protocol
    _verified_api_keys: bool = False

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or [
            '{"bank_id":"test","version":"1.0.0","schema_version":"1","steps":[]}'
        ]
        self._call_count = 0
        self.recorded_messages: list[list[BaseMessage]] = []

    @property
    def model(self) -> str:
        return "fake/test-model"

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def name(self) -> str:
        return "test-model"

    @overload
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: type[T],
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        from browser_use.llm.views import ChatInvokeUsage

        self.recorded_messages.append(list(messages))
        idx = min(self._call_count, len(self._responses) - 1)
        raw = self._responses[idx]
        self._call_count += 1

        usage = ChatInvokeUsage(
            prompt_tokens=10,
            prompt_cached_tokens=None,
            prompt_cache_creation_tokens=None,
            prompt_image_tokens=None,
            completion_tokens=20,
            total_tokens=30,
        )

        if output_format is not None:
            parsed = output_format.model_validate_json(raw)
            return ChatInvokeCompletion(completion=parsed, usage=usage, stop_reason="end_turn")

        return ChatInvokeCompletion(completion=raw, usage=usage, stop_reason="end_turn")


# ── Live-mapper terminal prompt for security questions (ADR-0021) ─────────────

SECURITY_ANSWER_TOOL_NAME = "ask_operator_for_security_answer"
_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,32}$")


def derive_field_key_from_question(question: str) -> str:
    """Derive a stable ``field_key`` from question text (ADR-0021 regex)."""
    text = unicodedata.normalize("NFC", question).lower()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    slug = "_".join(text.split()[:5]).strip("_")[:24]
    slug = re.sub(r"_+", "_", slug)
    key = f"security_q_{slug}" if slug else "security_q_unknown"
    if not _FIELD_KEY_RE.match(key):
        import hashlib

        digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:8]
        key = f"security_q_{digest}"
    return key[:32]


def store_mapper_security_answer_to_vault(
    *,
    vault: Any,
    bank_id: str,
    credential_ref: str,
    field_key: str,
    question_text: str,
    answer: str,
) -> None:
    """Persist a security-answer to the vault ``security_q`` namespace (ADR-0021)."""
    from open_banca_browser.step_executors.prompt_user import compute_question_hash  # noqa: PLC0415

    question_hash = compute_question_hash(
        bank_id=bank_id,
        credential_id=credential_ref,
        field_key=field_key,
        question_text=question_text,
    )
    vault.store_security_answer(
        credential_id=credential_ref,
        question_hash=question_hash,
        answer=answer,
        field_key=field_key,
    )
    logger.info(
        "MapperAgent: stored security answer in vault field_key=%s question_hash_prefix=%s",
        field_key,
        question_hash[:8],
    )


def prompt_security_answer_for_mapping(
    question: str,
    *,
    bank_id: str | None = None,
    prompt_fn: Any | None = None,
) -> str | None:
    """Ask the operator in the terminal for a security-question answer (mid-run mapper).

    Used by the browser-use custom action during live mapping. Vault persistence is handled
    by the caller when ``vault`` is available.
    """
    if prompt_fn is None:
        try:
            import typer  # type: ignore[import-untyped]

            prompt_fn = lambda msg, **kw: typer.prompt(msg, **kw)  # noqa: E731
        except ImportError:
            prompt_fn = lambda msg, **kw: input(f"{msg}: ")  # noqa: E731

    ctx = f" (bank_id={bank_id})" if bank_id else ""
    print(f"\n[Mapper] Pregunta de seguridad detectada{ctx}:")
    print(f"   {question.strip()}")

    try:
        answer = prompt_fn(
            "Respuesta (oculta; Enter vacío para omitir)",
            hide_input=True,
            default="",
        )
        if not answer or not str(answer).strip():
            logger.warning("prompt_security_answer_for_mapping: empty answer — skipped")
            return None
        return str(answer).strip()
    except (KeyboardInterrupt, EOFError):
        logger.info("prompt_security_answer_for_mapping: interrupted — skipped")
        return None


def build_mapper_browser_tools(
    *,
    bank_id: str | None = None,
    credential_ref: str | None = None,
    vault: Any | None = None,
    prompt_fn: Any | None = None,
) -> Any:
    """Browser-use Tools with ``ask_operator_for_security_answer`` for supervised mapping."""
    from browser_use import Tools  # type: ignore[import-untyped]
    from browser_use.agent.views import ActionResult  # type: ignore[import-untyped]
    from pydantic import Field

    tools = Tools()

    class AskSecurityAnswerParams(BaseModel):
        question: str = Field(
            description="Exact security question text shown on the page (copy from DOM/label)",
        )
        field_key: str | None = Field(
            default=None,
            description=(
                "Stable key for this question (regex ^[a-z][a-z0-9_]{2,32}$), e.g. "
                "security_q_grandpa_nickname. Omit to auto-derive from question text."
            ),
        )

    @tools.action(
        "Ask the human operator in the terminal for the answer to a bank security question. "
        "Use when you see a security-question text input and credentials placeholders "
        "do not apply. After receiving the answer, use input_text on the answer field "
        "and click submit/continue.",
        param_model=AskSecurityAnswerParams,
    )
    async def ask_operator_for_security_answer(
        params: AskSecurityAnswerParams,
        browser_session,
    ) -> Any:
        _ = browser_session  # required by browser-use action signature
        answer = prompt_security_answer_for_mapping(
            params.question,
            bank_id=bank_id,
            prompt_fn=prompt_fn,
        )
        if answer is None:
            return ActionResult(
                error="Operator skipped or gave an empty security answer",
                extracted_content=(
                    "No answer from operator. Retry ask_operator_for_security_answer "
                    "or wait for manual input."
                ),
            )

        field_key = (params.field_key or "").strip() or derive_field_key_from_question(
            params.question
        )
        vault_stored = False
        if vault is not None and bank_id and credential_ref:
            try:
                store_mapper_security_answer_to_vault(
                    vault=vault,
                    bank_id=bank_id,
                    credential_ref=credential_ref,
                    field_key=field_key,
                    question_text=params.question,
                    answer=answer,
                )
                vault_stored = True
            except Exception as exc:
                logger.warning(
                    "MapperAgent: failed to store security answer in vault "
                    "field_key=%s: %s",
                    field_key,
                    exc,
                )

        vault_note = (
            " Answer encrypted and stored in vault for this credential."
            if vault_stored
            else ""
        )
        return ActionResult(
            extracted_content=(
                "Operator provided the security answer via terminal."
                f"{vault_note} field_key={field_key}. "
                "Use input_text on the security answer field with this value, then submit. "
                f"Answer: {answer}"
            ),
            include_extracted_content_only_once=True,
        )

    return tools


# ── CLI interactive prompt for security question pre-load (ADR-0021) ─────────


def interactive_prompt(
    bank_id: str,
    field_key: str,
    question_selector: str,
    prompt_fn: Any | None = None,
) -> str | None:
    """Prompt operator in terminal for a security question answer (supervised CLI mode).

    Implements the "Pre-load via Mapper CLI" flow from mapper-agent.md §Security question
    detection (ADR-0021).

    Args:
        bank_id: Bank identifier for context.
        field_key: Suggested field key for this question.
        question_selector: CSS/XPath selector of the question element (for operator context).
        prompt_fn: Override the prompt function (used in tests to mock typer.prompt).
                   If None, uses typer.prompt (falling back to input() if typer unavailable).

    Returns:
        The answer string, or None if the operator skipped.
    """
    if prompt_fn is None:
        try:
            import typer  # type: ignore[import-untyped]
            prompt_fn = lambda msg, **kw: typer.prompt(msg, **kw)  # noqa: E731
        except ImportError:
            prompt_fn = lambda msg, **kw: input(f"{msg}: ")  # noqa: E731

    print(f"\n[Mapper] Detectada pregunta de seguridad (bank_id={bank_id}):")
    print(f"   field_key sugerido: {field_key}")
    print(f"   question_selector: {question_selector}")

    try:
        confirm = prompt_fn(
            "¿Quieres pre-cargar la respuesta al vault ahora? (Y/n)",
            default="Y",
        )
        if str(confirm).strip().lower() in {"n", "no"}:
            return None

        answer = prompt_fn(
            f"answer para '{field_key}' (oculto)",
            hide_input=True,
            default="",
        )
        if not answer:
            logger.warning("interactive_prompt: empty answer for field_key=%s — skipping", field_key)
            return None

        return str(answer)
    except (KeyboardInterrupt, EOFError):
        logger.info("interactive_prompt: interrupted for field_key=%s — skipping", field_key)
        return None


# ── MapperAgent ───────────────────────────────────────────────────────────────


class MapperAgent:
    """Generates a BankMap by driving a real browser via browser-use + Claude.

    Args:
        model: LiteLLM model string (default: anthropic/claude-sonnet-4-6).
        cost_cap_usd: Abort if cumulative LLM cost exceeds this (default $0.50).
        wallclock_cap_seconds: Abort if elapsed wall time exceeds this (default 5 min).
        pii_regions: Pixel regions to black-out in screenshots (ADR-0020 Capa 2).
        redact_config: PII redact config (RedactFilter). Defaults to env canaries.
        llm_override: Inject a custom BaseChatModel (tests pass FakeChatModel here).
        max_steps: Maximum browser-use agent steps (default 50).
        start_url_map: Optional dict mapping bank_id → start URL.
        interactive: If True and vault is provided, prompt operator in CLI for security
                     question answers (ADR-0021 pre-load path). Default False.
        vault: SecretVault instance for pre-loading security answers. Optional.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        cost_cap_usd: float = DEFAULT_COST_CAP_USD,
        wallclock_cap_seconds: float = DEFAULT_WALLCLOCK_SECONDS,
        pii_regions: list[PiiRegion] | None = None,
        redact_config: RedactConfig | None = None,
        llm_override: Any | None = None,
        max_steps: int = 50,
        start_url_map: dict[str, str] | None = None,
        interactive: bool = False,
        vault: Any | None = None,
    ) -> None:
        self._model_name = model
        self._cost_cap = cost_cap_usd
        self._wallclock_cap = wallclock_cap_seconds
        self._pii_regions = pii_regions or []
        self._redact_config = redact_config
        self._llm_override = llm_override
        self._max_steps = max_steps
        self._start_url_map = start_url_map or {}
        self._interactive = interactive
        self._vault = vault

    def _build_llm(self, tracker: CostTracker) -> CostTrackingChatModel:
        """Build the wrapped LLM chain: ChatLiteLLM → PIIRedact → CostTracking.

        When ``OPEN_BANCA_OTEL_ENABLED`` is set, injects an OTel counter so
        each LLM call emits to the ``llm_cost_usd_total`` metric (task #27).
        """
        if self._llm_override is not None:
            inner: Any = self._llm_override
        else:
            # Native ChatAnthropic instead of ChatLiteLLM: LiteLLM's tool-schema
            # translation triggers Anthropic "compiled grammar too large" with
            # browser-use's full action set. Native client avoids strict-mode.
            import os as _os
            from browser_use.llm.anthropic.chat import ChatAnthropic  # type: ignore[import-untyped]

            _anth_model = self._model_name.split("/", 1)[-1]
            inner = ChatAnthropic(
                model=_anth_model,
                api_key=_os.environ.get("ANTHROPIC_API_KEY", ""),
            )

        pii_wrapped = PIIRedactingChatModel(
            inner=inner,
            redact_config=self._redact_config,
            pii_regions=self._pii_regions,
        )

        return CostTrackingChatModel(
            inner=pii_wrapped,
            tracker=tracker,
            cost_counter=_build_cost_counter(),
            agent_name="mapper",
        )

    def _bank_start_url(self, bank_id: str) -> str:
        if bank_id in self._start_url_map:
            return self._start_url_map[bank_id]
        # Fallback: derive from bank_id slug
        slug = bank_id.lower().replace("_", "-").replace(" ", "-")
        return f"https://www.{slug}.com"

    async def map_bank(
        self,
        bank_id: str,
        credential_ref: str,
        sensitive_data: dict[str, str],
    ) -> BankMap:
        """Run the Mapper agent and return a validated BankMap.

        Args:
            bank_id: Bank identifier (e.g. "banco-general").
            credential_ref: Vault reference string (informational; creds resolved
                            externally and passed as sensitive_data).
            sensitive_data: Dict of {placeholder_key: actual_secret} passed to
                            browser-use Agent.  LLM never sees the actual values.

        Returns:
            BankMap: Validated domain entity.

        Raises:
            CostExceeded: If LLM cost exceeds cap.
            WallclockExceeded: If wall-clock time exceeds cap.
            SelfTestFailed: If the generated map fails dry-run validation.
            MapperError: Other mapper failures.
        """
        tracker = CostTracker(
            cost_cap_usd=self._cost_cap,
            wallclock_cap_seconds=self._wallclock_cap,
        )
        llm = self._build_llm(tracker)
        start_url = self._bank_start_url(bank_id)
        task = build_task_prompt(bank_id=bank_id, start_url=start_url)

        logger.info(
            "MapperAgent starting: bank_id=%s, model=%s, cost_cap=$%.2f",
            bank_id,
            self._model_name,
            self._cost_cap,
        )

        map_json_str: str | None = None

        if self._llm_override is not None:
            # Test / stub path: run directly without a real browser
            map_json_str = await self._run_stub(llm, task, sensitive_data)
        else:
            # Production path: use browser-use Agent with real Chromium
            map_json_str = await self._run_with_browser(
                llm,
                task,
                sensitive_data,
                tracker,
                bank_id=bank_id,
                credential_ref=credential_ref,
            )

        if map_json_str is None:
            raise MapperError(f"Mapper agent returned no output for bank_id={bank_id}")

        bank_map = self._parse_bank_map(map_json_str, bank_id)

        # Security question detection (ADR-0021): if the map contains prompt_user
        # steps and a vault + credential_ref are provided, offer CLI preload.
        bank_map = await self._handle_security_question_preload(
            bank_map=bank_map,
            bank_id=bank_id,
            credential_ref=credential_ref,
        )

        # Self-test: dry-run ScraperRunner over the generated map
        await self._self_test(bank_map)

        logger.info(
            "MapperAgent complete: bank_id=%s, steps=%d, cost=$%.4f",
            bank_id,
            len(bank_map.steps),
            tracker.usage.cost_usd,
        )
        return bank_map

    async def _handle_security_question_preload(
        self,
        bank_map: BankMap,
        bank_id: str,
        credential_ref: str,
    ) -> BankMap:
        """Offer CLI preload of security question answers when running supervised (ADR-0021).

        When the Mapper runs in CLI / supervised mode (``_interactive`` flag), and
        the generated map contains ``prompt_user`` steps, prompt the operator in
        terminal for each security question answer. Writes answers to the vault so
        the first autonomous scrape job finds cache hits.

        In autonomous mode (no vault, no interactive flag), returns the map unchanged.
        The runtime will handle the miss via HumanInputAwaitActivity.

        Args:
            bank_map: The generated BankMap (may contain prompt_user steps).
            bank_id: Bank identifier.
            credential_ref: Vault reference for the credential.

        Returns:
            The same BankMap (possibly with cache_answers=True added to prompt_user steps).
        """
        prompt_user_steps = [s for s in bank_map.steps if s.action == "prompt_user"]

        if not prompt_user_steps:
            return bank_map

        if not self._interactive or self._vault is None:
            logger.info(
                "MapperAgent: %d prompt_user step(s) detected for bank_id=%s. "
                "Running non-interactively — answers will be requested at runtime.",
                len(prompt_user_steps),
                bank_id,
            )
            return bank_map

        # Interactive supervised mode: prompt operator for each security question
        logger.info(
            "MapperAgent: %d security question(s) detected — CLI preload mode (ADR-0021)",
            len(prompt_user_steps),
        )

        for step in prompt_user_steps:
            extra = step.model_extra or {}
            field_key: str = extra.get("field_key", step.step_id)
            question_selector: str = extra.get("question_selector", "")

            answer = interactive_prompt(
                bank_id=bank_id,
                field_key=field_key,
                question_selector=question_selector,
            )
            if answer is None:
                logger.info(
                    "MapperAgent: operator skipped preload for field_key=%s", field_key
                )
                continue

            try:
                store_mapper_security_answer_to_vault(
                    vault=self._vault,
                    bank_id=bank_id,
                    credential_ref=credential_ref,
                    field_key=field_key,
                    question_text=field_key,
                    answer=answer,
                )
            except Exception as exc:
                logger.warning(
                    "MapperAgent: failed to preload vault for field_key=%s: %s",
                    field_key,
                    exc,
                )

        return bank_map

    async def _run_stub(
        self,
        llm: CostTrackingChatModel,
        task: str,
        sensitive_data: dict[str, str],
    ) -> str:
        """Stub path for tests: directly call llm.ainvoke() with a single prompt."""
        from browser_use.llm.messages import SystemMessage, UserMessage

        messages: list[BaseMessage] = [
            SystemMessage(role="system", content=SYSTEM_PROMPT),
            UserMessage(role="user", content=task),
        ]
        result = await llm.ainvoke(messages)
        # The FakeChatModel returns the map JSON directly
        return str(result.completion)

    async def _run_with_browser(
        self,
        llm: CostTrackingChatModel,
        task: str,
        sensitive_data: dict[str, str],
        tracker: CostTracker,
        *,
        bank_id: str | None = None,
        credential_ref: str | None = None,
    ) -> str:
        """Production path: use browser-use Agent with a real Chromium browser."""
        try:
            from browser_use import Agent  # type: ignore[import-untyped]
        except ImportError as exc:
            raise MapperError("browser-use not installed. Install from path dep or PyPI.") from exc

        mapper_tools = build_mapper_browser_tools(
            bank_id=bank_id,
            credential_ref=credential_ref,
            vault=self._vault,
        )

        # flash_mode + use_thinking=False shrink the tool schema below
        # Anthropic's "compiled grammar too large" threshold (req fails otherwise
        # on the strict tool-calling path with the default browser-use schema).
        agent = Agent(  # type: ignore[call-arg]
            task=task,
            llm=llm,  # type: ignore[arg-type]
            tools=mapper_tools,
            sensitive_data=sensitive_data,  # type: ignore[arg-type]
            override_system_message=SYSTEM_PROMPT,
            max_steps=self._max_steps,
            register_should_stop_callback=tracker.should_stop_async,
            flash_mode=True,
            use_thinking=False,
        )

        try:
            history = await agent.run(max_steps=self._max_steps)
        except Exception as exc:
            # Re-raise cost/wallclock errors; wrap everything else
            if isinstance(exc, (CostExceeded, WallclockExceeded)):
                raise
            raise MapperError(f"browser-use Agent raised: {exc}") from exc

        # Check if should_stop fired (cost/wallclock exceeded)
        if tracker.should_stop():
            usage = tracker.usage
            raise CostExceeded(usage.cost_usd, self._cost_cap)

        # Extract final result from agent history
        return self._extract_map_from_history(history)

    def _extract_map_from_history(self, history: Any) -> str:
        """Extract map JSON from browser-use AgentHistoryList."""
        # browser-use stores the done() result in the last action's result
        try:
            if hasattr(history, "final_result"):
                result = history.final_result()
                if result:
                    return str(result)
            # Fallback: search action history for done result
            if hasattr(history, "history"):
                for item in reversed(history.history):
                    if hasattr(item, "result") and item.result:
                        for r in item.result:
                            if hasattr(r, "extracted_content") and r.extracted_content:
                                return str(r.extracted_content)
        except Exception as exc:
            logger.warning("Could not extract map from history: %s", exc)

        raise MapperError("Could not extract map JSON from browser-use agent history")

    def _parse_bank_map(self, raw: str, bank_id: str) -> BankMap:
        """Parse and validate raw JSON string into a domain BankMap."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```")).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MapperError(f"Mapper output is not valid JSON: {exc}") from exc

        # Inject bank_id if missing (agent may omit it)
        data.setdefault("bank_id", bank_id)
        data.setdefault("schema_version", "1")
        data.setdefault("version", "1.0.0")

        try:
            return BankMap.model_validate(data)
        except Exception as exc:
            raise MapperError(f"Generated map failed BankMap schema validation: {exc}") from exc

    async def _self_test(self, bank_map: BankMap) -> None:
        """Run a dry-run ScraperRunner validation on the generated map.

        Uses stub mode (no real browser, no real credentials) to verify that
        the step structure is internally consistent.

        Per docs/02-components/mapper-agent.md §Validación post-mapping:
          "Dry-run sin creds — Scraper Runner ejecuta hasta el primer
           pause_for_otp con creds dummy; verifica que selectores resuelvan."

        Raises:
            SelfTestFailed: If ScraperRunner raises an unexpected error in stub mode.
        """
        try:
            from open_banca_browser.runner import ScraperRunner
            from open_banca_domain.entities.credential import Credential
        except ImportError as exc:
            logger.warning(
                "open-banca-browser not available for self-test (dry-run skipped): %s",
                exc,
            )
            return

        dummy_credential = Credential(
            id="dry-run-cred",
            bank=bank_map.bank_id,
            credential_ref="vault://dry-run/dummy",
            label="dry-run credential",
        )

        # Use stub mode (OPEN_BANCA_PLAYWRIGHT_REAL not set) — no real browser
        runner = ScraperRunner(
            secret_resolver=lambda _ref: "DRY_RUN_VALUE",
        )

        try:
            runner.execute_map(bank_map, dummy_credential)
        except Exception as exc:
            # In stub mode, errors indicate structural map problems
            err_str = str(exc)
            # "stub mode" means the runner ran without a browser — any raised
            # exception that is NOT a stub-normal completion is a real problem.
            if "stub" in err_str.lower():
                # Stub completed normally (expected in dry-run)
                return
            raise SelfTestFailed(err_str) from exc

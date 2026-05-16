"""ValidatorAgent — heuristic-first transaction validation with DeepSeek LLM fallback.

Validation strategy:
  1. Run heuristic checks (no LLM cost): balance != sum(txn), date holes,
     duplicates, currency mismatch.
  2. If result is ambiguous, call DeepSeek V3 for a structured verdict.
  3. Cost cap: $0.05/call. Raises CostCapExceeded if projected cost exceeds cap.

Model: DeepSeek V3 via OpenAI-compatible endpoint (text only, no vision).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

# ---------------------------------------------------------------------------
# Cost helpers — delegated to unified router
# ---------------------------------------------------------------------------

from open_banca_llm.router import compute_cost as _router_compute_cost
from open_banca_llm.router import get_cost_cap as _router_get_cost_cap
from open_banca_llm.router import resolve_model as _router_resolve_model
from open_banca_llm.router import to_pydantic_ai_model_str as _to_pai

# Backward-compat constant (DeepSeek default); agents should prefer dynamic cap.
VALIDATOR_COST_CAP_USD = Decimal("0.05")


class CostCapExceeded(Exception):
    """Raised when the projected or actual LLM cost exceeds the per-call cap."""

    def __init__(self, cost: Decimal, cap: Decimal) -> None:
        super().__init__(f"Cost cap exceeded: ${cost:.4f} > ${cap:.4f}")
        self.cost = cost
        self.cap = cap


def _compute_cost(input_tokens: int, output_tokens: int, model: str = "deepseek/deepseek-chat") -> Decimal:
    """Compute USD cost via the unified router."""
    return _router_compute_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)


# ---------------------------------------------------------------------------
# I/O types
# ---------------------------------------------------------------------------


class ValidationVerdict(StrEnum):
    """Overall verdict for a parsed transaction set."""

    valid = "valid"
    warn = "warn"
    fail = "fail"


class ValidationIssue(BaseModel):
    """A single validation issue."""

    code: str = Field(description="Machine-readable issue code")
    message: str = Field(description="Human-readable description")
    row_index: int | None = Field(default=None, description="Row index if row-specific")


class ValidationReport(BaseModel):
    """Structured output from ValidatorAgent."""

    verdict: ValidationVerdict
    issues: list[ValidationIssue] = Field(default_factory=list)
    llm_used: bool = Field(default=False, description="True if the LLM was invoked")
    cost_usd: float = Field(default=0.0, description="Estimated LLM cost for this call")


@dataclass
class TransactionInput:
    """Lightweight transaction data for validation (no domain import needed)."""

    raw_id: str
    date: str  # ISO 8601
    description: str
    amount: str  # Decimal string
    currency: str = "USD"
    account_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Heuristic checks
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _heuristic_checks(
    transactions: list[TransactionInput],
    reported_balance: Decimal | None,
) -> list[ValidationIssue]:
    """Run all heuristic checks and return a list of issues (no LLM)."""
    issues: list[ValidationIssue] = []

    if not transactions:
        issues.append(
            ValidationIssue(
                code="empty_transaction_set",
                message="No transactions provided for validation",
            )
        )
        return issues

    # --- Balance check ---
    if reported_balance is not None:
        try:
            txn_sum = sum(Decimal(t.amount) for t in transactions)
        except InvalidOperation:
            issues.append(
                ValidationIssue(
                    code="invalid_amount_format",
                    message="One or more transactions have non-decimal amount values",
                )
            )
            txn_sum = None

        if txn_sum is not None and abs(txn_sum - reported_balance) > Decimal("0.01"):
            issues.append(
                ValidationIssue(
                    code="balance_mismatch",
                    message=(
                        f"Sum of transactions ({txn_sum}) != reported balance ({reported_balance})"
                    ),
                )
            )

    # --- Date format check ---
    for idx, txn in enumerate(transactions):
        if not _DATE_RE.match(txn.date):
            issues.append(
                ValidationIssue(
                    code="invalid_date_format",
                    message=f"Transaction has non-ISO-8601 date: {txn.date!r}",
                    row_index=idx,
                )
            )

    # --- Date holes: sort and look for gaps > 60 days ---
    valid_dates = []
    for txn in transactions:
        if _DATE_RE.match(txn.date):
            valid_dates.append(txn.date)
    if valid_dates:
        valid_dates.sort()
        prev = _date.fromisoformat(valid_dates[0])
        for ds in valid_dates[1:]:
            curr = _date.fromisoformat(ds)
            gap = (curr - prev).days
            if gap > 60:
                issues.append(
                    ValidationIssue(
                        code="date_gap",
                        message=f"Gap of {gap} days between {prev} and {curr}",
                    )
                )
            prev = curr

    # --- Duplicate raw_id detection ---
    seen_ids: set[str] = set()
    for idx, txn in enumerate(transactions):
        if txn.raw_id in seen_ids:
            issues.append(
                ValidationIssue(
                    code="duplicate_transaction_id",
                    message=f"Duplicate raw_id detected: {txn.raw_id!r}",
                    row_index=idx,
                )
            )
        seen_ids.add(txn.raw_id)

    # --- Currency mismatch (all txns should share one currency) ---
    currencies = {t.currency for t in transactions}
    if len(currencies) > 1:
        issues.append(
            ValidationIssue(
                code="currency_mismatch",
                message=f"Multiple currencies found: {sorted(currencies)}",
            )
        )

    return issues


def _is_ambiguous(issues: list[ValidationIssue]) -> bool:
    """Return True if heuristic found no clear issues but result is uncertain.

    Ambiguity rule: empty transaction set or only soft issues (date_gap)
    that could be legitimate. Hard issues (balance_mismatch, duplicate,
    currency_mismatch, invalid_amount) => not ambiguous, already a fail.
    """
    hard_codes = {
        "balance_mismatch",
        "duplicate_transaction_id",
        "currency_mismatch",
        "invalid_amount_format",
        "empty_transaction_set",
    }
    has_hard = any(i.code in hard_codes for i in issues)
    if has_hard:
        return False
    # Soft issues only (date_gap, invalid_date_format) => ambiguous
    return bool(issues)


# ---------------------------------------------------------------------------
# LLM result schema for structured output
# ---------------------------------------------------------------------------


class _LLMValidationOutput(BaseModel):
    """Structured output that DeepSeek returns for ambiguous cases."""

    verdict: ValidationVerdict
    issues: list[ValidationIssue] = Field(default_factory=list)
    reasoning: str = Field(default="")


# ---------------------------------------------------------------------------
# ValidatorAgent
# ---------------------------------------------------------------------------


class ValidatorAgent:
    """Validator agent: heuristic checks first, LLM fallback for ambiguous cases.

    Args:
        model: PydanticAI model to use. In production: ``deepseek-chat`` via
               the OpenAI-compatible endpoint. In tests: inject a ``TestModel``
               or ``FunctionModel``.
        cost_cap_usd: Maximum USD cost per call (default 0.05).
    """

    def __init__(
        self,
        model: Model | str | None = None,
        cost_cap_usd: Decimal = VALIDATOR_COST_CAP_USD,
    ) -> None:
        self._model: Model | str | None = model
        self._cost_cap = cost_cap_usd
        self._cap_is_default = cost_cap_usd == VALIDATOR_COST_CAP_USD
        self._resolved_model_str: str | None = None
        self._agent: Agent[None, _LLMValidationOutput] | None = None

    def _ensure_model_resolved(self) -> str:
        """Resolve the model string (for cost computation) without building the Agent."""
        if self._resolved_model_str is not None:
            return self._resolved_model_str
        if self._model is not None:
            self._resolved_model_str = str(self._model)
        else:
            try:
                self._resolved_model_str = _router_resolve_model("validator")
            except Exception:
                self._resolved_model_str = "deepseek/deepseek-chat"
            if self._cap_is_default:
                self._cost_cap = _router_get_cost_cap("validator", self._resolved_model_str)
        return self._resolved_model_str

    def _get_agent(self) -> Agent[None, _LLMValidationOutput]:
        """Lazily create (or return cached) the PydanticAI Agent."""
        if self._agent is None:
            if self._model is not None:
                model: Model | str = self._model
            else:
                model_str = self._ensure_model_resolved()
                model = _to_pai(model_str)
            self._agent = Agent(
                model,
                output_type=_LLMValidationOutput,
                system_prompt=(
                    "You are a financial transaction validator. "
                    "Review the provided transaction data and balance discrepancies. "
                    "Return a structured verdict: 'valid', 'warn', or 'fail'. "
                    "List specific issues with machine-readable codes. "
                    "Be conservative: prefer 'warn' over 'fail' when genuinely ambiguous."
                ),
            )
        return self._agent

    async def validate(
        self,
        transactions: list[TransactionInput],
        reported_balance: Decimal | None = None,
        account_id: str = "",
    ) -> ValidationReport:
        """Validate transactions, returning a ValidationReport.

        Args:
            transactions: List of parsed transactions.
            reported_balance: The account balance as reported by the bank, if known.
            account_id: Account identifier for context.

        Returns:
            ValidationReport with verdict and issues.

        Raises:
            CostCapExceeded: If LLM cost exceeds the configured cap.
        """
        # Phase 1: heuristic checks
        heuristic_issues = _heuristic_checks(transactions, reported_balance)

        hard_codes = {
            "balance_mismatch",
            "duplicate_transaction_id",
            "currency_mismatch",
            "invalid_amount_format",
            "empty_transaction_set",
        }
        has_hard = any(i.code in hard_codes for i in heuristic_issues)

        # Clear hard failures → skip LLM
        if has_hard:
            return ValidationReport(
                verdict=ValidationVerdict.fail,
                issues=heuristic_issues,
                llm_used=False,
                cost_usd=0.0,
            )

        # No issues at all → clearly valid
        if not heuristic_issues:
            return ValidationReport(
                verdict=ValidationVerdict.valid,
                issues=[],
                llm_used=False,
                cost_usd=0.0,
            )

        # Ambiguous (only soft issues) → LLM call
        context = self._build_llm_context(transactions, reported_balance, heuristic_issues)

        model_str = self._ensure_model_resolved()

        # Estimate tokens (rough: 4 chars per token)
        estimated_input_tokens = len(context) // 4
        estimated_output_tokens = 200
        projected_cost = _compute_cost(estimated_input_tokens, estimated_output_tokens, model_str)

        if projected_cost > self._cost_cap:
            raise CostCapExceeded(projected_cost, self._cost_cap)

        result = await self._get_agent().run(context)

        # Compute actual cost from usage
        usage = result.usage()
        actual_cost = _compute_cost(
            usage.input_tokens or estimated_input_tokens,
            usage.output_tokens or estimated_output_tokens,
            model_str,
        )

        if actual_cost > self._cost_cap:
            raise CostCapExceeded(actual_cost, self._cost_cap)

        llm_output = result.output
        # Merge heuristic soft issues with LLM issues (deduplicate by code)
        all_issues = list(heuristic_issues)
        existing_codes = {i.code for i in all_issues}
        for issue in llm_output.issues:
            if issue.code not in existing_codes:
                all_issues.append(issue)

        return ValidationReport(
            verdict=llm_output.verdict,
            issues=all_issues,
            llm_used=True,
            cost_usd=float(actual_cost),
        )

    def _build_llm_context(
        self,
        transactions: list[TransactionInput],
        reported_balance: Decimal | None,
        soft_issues: list[ValidationIssue],
    ) -> str:
        """Build a text prompt for DeepSeek validation."""
        lines: list[str] = [
            f"Account has {len(transactions)} transactions.",
        ]
        if reported_balance is not None:
            try:
                txn_sum = sum(Decimal(t.amount) for t in transactions)
                lines.append(
                    f"Reported balance: {reported_balance}, "
                    f"computed sum: {txn_sum}, "
                    f"delta: {txn_sum - reported_balance}"
                )
            except InvalidOperation:
                lines.append(f"Reported balance: {reported_balance} (sum could not be computed)")

        lines.append("Detected soft issues:")
        for issue in soft_issues:
            lines.append(f"  - [{issue.code}] {issue.message}")

        lines.append("Sample transactions (first 10):")
        for txn in transactions[:10]:
            desc = txn.description[:60]
            lines.append(
                f"  {txn.date} | {txn.raw_id} | {txn.amount} {txn.currency} | {desc}"
            )

        return "\n".join(lines)

    def override_model(self, model: Model | str) -> ValidatorAgent:
        """Return a new ValidatorAgent with the model overridden (for testing)."""
        return ValidatorAgent(model=model, cost_cap_usd=self._cost_cap)

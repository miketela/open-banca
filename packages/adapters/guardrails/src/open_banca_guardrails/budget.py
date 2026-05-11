"""Budget enforcement for LLM and scrape operations.

Tracks cumulative cost per (job_id, op_type) in SQLite and raises
``BudgetExceeded`` when a check would push the job over its cap.

ADR-0020 / REQ-011:
  - $0.50 hard cap per LLM job (mapper, most expensive).
  - $0.10 hard cap per scrape job (incremental, defensive).
  - Configurable max tokens per agent per job.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from open_banca_guardrails.errors import BudgetExceeded


class BudgetEnforcer:
    """Checks and records LLM/scrape cost per job.

    The ``check`` method raises ``BudgetExceeded`` if adding ``est_cost`` to the
    already-consumed amount would exceed the cap.  It does NOT record the cost.

    The ``record`` method persists the actual cost after a successful operation.
    Together they decouple the pre-flight check from the post-call accounting.

    Args:
        conn: Open sqlite3 connection (guardrails DB).
        llm_job_cap_usd: Per-job LLM budget cap.
        scrape_cap_usd: Per-scrape budget cap.
        max_tokens_per_job: Token hard limit per agent per job.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        llm_job_cap_usd: Decimal = Decimal("0.50"),
        scrape_cap_usd: Decimal = Decimal("0.10"),
        max_tokens_per_job: int = 100_000,
    ) -> None:
        self._conn = conn
        self._llm_cap = llm_job_cap_usd
        self._scrape_cap = scrape_cap_usd
        self._max_tokens = max_tokens_per_job

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cap_for(self, op_type: str) -> Decimal:
        """Return the budget cap for the given operation type.

        Args:
            op_type: One of ``"llm"`` or ``"scrape"``.

        Returns:
            Decimal cap in USD.
        """
        if op_type == "scrape":
            return self._scrape_cap
        return self._llm_cap

    def consumed(self, job_id: str, op_type: str) -> Decimal:
        """Return total cost consumed for this (job_id, op_type) so far.

        Args:
            job_id: Job identifier.
            op_type: Operation type.

        Returns:
            Decimal amount consumed (USD).
        """
        row = self._conn.execute(
            "SELECT amount_usd FROM budget_consumed WHERE job_id = ? AND op_type = ?",
            (job_id, op_type),
        ).fetchone()
        if row is None:
            return Decimal("0")
        return Decimal(str(row[0]))

    def check(self, job_id: str, op_type: str, est_cost: Decimal) -> None:
        """Pre-flight check: raise ``BudgetExceeded`` if est_cost would exceed cap.

        Args:
            job_id: Job identifier.
            op_type: Operation type (``"llm"`` or ``"scrape"``).
            est_cost: Estimated cost of the upcoming operation (USD).

        Raises:
            BudgetExceeded: If consumed + est_cost > cap.
        """
        cap = self.cap_for(op_type)
        already = self.consumed(job_id, op_type)
        if already + est_cost > cap:
            raise BudgetExceeded(
                job_id=job_id,
                consumed_usd=already + est_cost,
                cap_usd=cap,
                op_type=op_type,
            )

    def record(self, job_id: str, op_type: str, actual_cost: Decimal) -> Decimal:
        """Record actual cost after a completed operation.

        Upserts the consumed total for (job_id, op_type) and returns the new total.

        Args:
            job_id: Job identifier.
            op_type: Operation type.
            actual_cost: Actual cost incurred (USD).

        Returns:
            New cumulative total for this (job_id, op_type).
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT amount_usd FROM budget_consumed WHERE job_id = ? AND op_type = ?",
                (job_id, op_type),
            ).fetchone()
            new_total = (Decimal(str(row[0])) if row else Decimal("0")) + actual_cost
            self._conn.execute(
                """
                INSERT INTO budget_consumed (job_id, op_type, amount_usd)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id, op_type) DO UPDATE SET amount_usd = excluded.amount_usd
                """,
                (job_id, op_type, float(new_total)),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return new_total

    def check_tokens(self, job_id: str, tokens_used: int) -> None:
        """Raise ``BudgetExceeded`` if token usage exceeds the per-job limit.

        Note: token counts are not persisted to SQLite; this is an in-call guard.
        Persistent token tracking is left to the mapper's cost_tracker.

        Args:
            job_id: Job identifier.
            tokens_used: Total tokens consumed so far in this job.

        Raises:
            BudgetExceeded: If tokens_used > max_tokens_per_job.
        """
        if tokens_used > self._max_tokens:
            raise BudgetExceeded(
                job_id=job_id,
                consumed_usd=Decimal("0"),
                cap_usd=Decimal("0"),
                op_type=f"tokens:{tokens_used}>{self._max_tokens}",
            )

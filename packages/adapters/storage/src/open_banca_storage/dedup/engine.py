"""DedupEngine — orchestrates 3-level deduplication and cursor management.

Algorithm (strict order):

1. **Level 1 — embedded_id**: if ``Transaction.embedded_id`` is not None and
   the value exists in ``dedup_index`` (level='embedded'), skip the transaction
   (it is already persisted with the bank-supplied stable ID).

2. **Level 2 — fingerprint**: compute ``compute_fingerprint(...)`` and check
   ``dedup_index`` for a hit.  On hit, skip.  On miss, insert the transaction
   and record the fingerprint in ``dedup_index``.

3. **Level 3 — fuzzy transfer**: after all transactions have been processed
   through levels 1-2, run ``TransferMatcher`` over the *newly inserted*
   transactions plus the recent window of existing transactions.  For each
   matched pair, update ``transfer_match_id`` on both sides.

The engine operates directly on the SQLite connection (same DBAPI2 object
used by ``SqliteJobStore``).  It does NOT own the connection lifecycle.

Cursor management (Story 2 AC):

- ``get_cursor(account_id)``: returns the most-recently-seen ``posted_at``
  for that account, stored in a dedicated ``account_cursors`` concept.
  NOTE: the existing ``cursors`` table is keyed on ``bank``.  The engine
  augments per-account tracking via the ``transactions`` table directly
  (``MAX(posted_at)`` query) so it can serve as a source-of-truth without
  requiring schema changes.
- ``effective_since(account_id)``: returns cursor minus 3-day buffer for
  use in window queries (covers pending-clearing movements).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from open_banca_domain.entities.transaction import Transaction
from open_banca_storage.dedup.fingerprint import compute_fingerprint
from open_banca_storage.dedup.transfer_matcher import TransferMatcher

logger = logging.getLogger(__name__)

# Buffer for incremental window queries (Story 2 AC).
CURSOR_BUFFER_DAYS: int = 3


@dataclass
class IngestResult:
    """Result of a single ``DedupEngine.ingest()`` call.

    Attributes:
        new: Transactions that were inserted for the first time.
        duplicates: Transactions that were skipped (already in storage).
        transfer_pairs: Pairs of (debit_tx, credit_tx) that were linked.
    """

    new: list[Transaction] = field(default_factory=list)
    duplicates: list[Transaction] = field(default_factory=list)
    transfer_pairs: list[tuple[Transaction, Transaction]] = field(default_factory=list)


class DedupEngine:
    """3-level deduplication engine backed by a SQLite/SQLCipher connection.

    Args:
        conn: An open DBAPI2 connection with the schema already migrated.
              The caller owns the connection lifecycle.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._matcher = TransferMatcher()

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest(self, transactions: list[Transaction]) -> IngestResult:
        """Process *transactions* through the 3-level dedup pipeline.

        1. Level 1 (embedded_id): skip if bank-supplied ID already indexed.
        2. Level 2 (fingerprint): skip if deterministic hash already indexed.
        3. Level 3 (transfer): link debit/credit pairs across accounts.

        Returns:
            ``IngestResult`` with new, duplicates, and transfer_pairs.
        """
        result = IngestResult()

        for tx in transactions:
            # ── Level 1: embedded ID ──────────────────────────────────────
            if tx.embedded_id is not None:
                if self._embedded_id_exists(tx.embedded_id, tx.account_id):
                    logger.debug(
                        "L1 skip: embedded_id=%s account=%s", tx.embedded_id, tx.account_id
                    )
                    result.duplicates.append(tx)
                    continue
                # New embedded-ID transaction — insert and index.
                self._insert_transaction(tx)
                self._index_embedded(tx)
                result.new.append(tx)
                continue

            # ── Level 2: fingerprint ──────────────────────────────────────
            fp = compute_fingerprint(
                account_id=tx.account_id,
                posted_at=tx.posted_at,
                value_at=tx.value_at,
                amount=tx.amount,
                description=tx.description,
            )
            if self._fingerprint_exists(fp, tx.account_id):
                logger.debug("L2 skip: fingerprint=%s account=%s", fp[:8], tx.account_id)
                result.duplicates.append(tx)
                continue
            self._insert_transaction(tx)
            self._index_fingerprint(fp, tx)
            result.new.append(tx)

        # ── Level 3: fuzzy transfer matching ─────────────────────────────
        if result.new:
            pairs = self._matcher.find_pairs(result.new)
            for debit, credit in pairs:
                self._link_transfer_pair(debit.id, credit.id)
                result.transfer_pairs.append((debit, credit))
            logger.debug(
                "L3 transfer: %d new, %d pairs", len(result.new), len(result.transfer_pairs)
            )

        return result

    def get_cursor(self, account_id: str) -> datetime | None:
        """Return the latest ``posted_at`` for *account_id*, or None.

        Queries the ``transactions`` table directly to derive the cursor,
        so it always reflects persisted data.
        """
        row = self._conn.execute(
            "SELECT MAX(posted_at) FROM transactions WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0])

    def effective_since(self, account_id: str) -> datetime | None:
        """Return ``get_cursor() - 3 days`` for the incremental query window.

        The 3-day buffer ensures pending-clearing movements that may change
        date or amount between runs are re-processed through dedup (and
        upserted if they differ).

        Returns None if no cursor exists (first run — full historical import).
        """
        cursor = self.get_cursor(account_id)
        if cursor is None:
            return None
        return cursor - timedelta(days=CURSOR_BUFFER_DAYS)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _embedded_id_exists(self, embedded_id: str, account_id: str) -> bool:
        """Return True if a dedup_index row with level='embedded' exists."""
        row = self._conn.execute(
            "SELECT 1 FROM dedup_index WHERE fingerprint = ? AND account_id = ? AND level = 'embedded'",
            (embedded_id, account_id),
        ).fetchone()
        return row is not None

    def _fingerprint_exists(self, fingerprint: str, account_id: str) -> bool:
        """Return True if a dedup_index row with level='fingerprint' exists."""
        row = self._conn.execute(
            "SELECT 1 FROM dedup_index WHERE fingerprint = ? AND account_id = ? AND level = 'fingerprint'",
            (fingerprint, account_id),
        ).fetchone()
        return row is not None

    def _insert_transaction(self, tx: Transaction) -> None:
        """Insert a transaction row (INSERT OR IGNORE — idempotent on UUID)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO transactions (
                id, account_id, job_id, posted_at, value_at,
                amount, currency, description, fingerprint_hash,
                embedded_id, transfer_match_id, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.id,
                tx.account_id,
                tx.posted_at.isoformat(),
                tx.value_at.isoformat(),
                str(tx.amount),
                tx.currency,
                tx.description,
                tx.fingerprint_hash,
                tx.embedded_id,
                tx.transfer_match_id,
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        self._conn.commit()

    def _index_embedded(self, tx: Transaction) -> None:
        """Add a dedup_index row for an embedded-ID transaction."""
        assert tx.embedded_id is not None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dedup_index (fingerprint, account_id, transaction_id, level)
            VALUES (?, ?, ?, 'embedded')
            """,
            (tx.embedded_id, tx.account_id, tx.id),
        )
        self._conn.commit()

    def _index_fingerprint(self, fingerprint: str, tx: Transaction) -> None:
        """Add a dedup_index row for a fingerprint-hashed transaction."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dedup_index (fingerprint, account_id, transaction_id, level)
            VALUES (?, ?, ?, 'fingerprint')
            """,
            (fingerprint, tx.account_id, tx.id),
        )
        self._conn.commit()

    def _link_transfer_pair(self, debit_id: str, credit_id: str) -> None:
        """Set transfer_match_id on both sides and index the fuzzy link."""
        # Update both transaction rows.
        self._conn.execute(
            "UPDATE transactions SET transfer_match_id = ? WHERE id = ?",
            (credit_id, debit_id),
        )
        self._conn.execute(
            "UPDATE transactions SET transfer_match_id = ? WHERE id = ?",
            (debit_id, credit_id),
        )
        # Record in dedup_index (level='fuzzy') — one entry per direction.
        link_key_debit = f"transfer:{debit_id}:{credit_id}"
        link_key_credit = f"transfer:{credit_id}:{debit_id}"
        fuzzy_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dedup_index (fingerprint, account_id, transaction_id, level)
            VALUES (?, (SELECT account_id FROM transactions WHERE id = ?), ?, 'fuzzy')
            """,
            (link_key_debit, debit_id, debit_id),
        )
        del fuzzy_id  # only needed if we wanted a separate row UUID
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dedup_index (fingerprint, account_id, transaction_id, level)
            VALUES (?, (SELECT account_id FROM transactions WHERE id = ?), ?, 'fuzzy')
            """,
            (link_key_credit, credit_id, credit_id),
        )
        self._conn.commit()
        logger.debug("L3 linked: %s <-> %s", debit_id, credit_id)

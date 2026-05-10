"""SQLCipher implementation of JobStorePort."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from open_banca_domain.entities.account import (
    AccountUnion,
    CheckingAccount,
    CreditCardAccount,
    SavingsAccount,
)
from open_banca_domain.entities.job import Job, JobMode, JobStatus
from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus
from open_banca_domain.entities.transaction import Transaction
from open_banca_storage.connection import Connection

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


class SqliteJobStore:
    """Implements JobStorePort using a SQLCipher connection.

    Args:
        conn: Open SQLCipher connection with key already applied and
              schema already migrated.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def save_job(self, job: Job) -> None:
        """Insert or replace a Job row (upsert by primary key)."""
        self._conn.execute(
            """
            INSERT INTO jobs (
                id, status, bank, credential_ref, mode,
                since_cursor, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status       = excluded.status,
                since_cursor = excluded.since_cursor,
                error        = excluded.error,
                updated_at   = excluded.updated_at
            """,
            (
                job.id,
                str(job.status),
                job.bank,
                job.credential_ref,
                str(job.mode),
                job.since_cursor,
                job.error,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        logger.debug("save_job: %s status=%s", job.id, job.status)

    def load_job(self, job_id: str) -> Job | None:
        """Load a Job by ID, returning None if not found."""
        row = self._conn.execute(
            "SELECT id, status, bank, credential_ref, mode, since_cursor, error, created_at, updated_at FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def list_jobs(self) -> list[Job]:
        """Return all jobs ordered by created_at ascending."""
        rows = self._conn.execute(
            "SELECT id, status, bank, credential_ref, mode, since_cursor, error, created_at, updated_at FROM jobs ORDER BY created_at ASC"
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    # ── Accounts ──────────────────────────────────────────────────────────────

    def save_account(self, account: AccountUnion) -> None:
        """Upsert an account (keyed on bank + bank_account_id)."""
        now = _utcnow_iso()
        base = account  # all variants share the _AccountBase fields via the union

        extra: dict[str, Any] = {}
        if isinstance(account, CreditCardAccount):
            extra = {
                "credit_limit": str(account.credit_limit),
                "available_credit": str(account.available_credit),
                "cut_date": account.cut_date.isoformat(),
                "min_payment": str(account.min_payment),
                "payment_due_date": account.payment_due_date.isoformat(),
                "statement_balance": str(account.statement_balance),
            }

        self._conn.execute(
            """
            INSERT INTO accounts (
                id, bank, bank_account_id, account_type, currency, balance,
                credit_limit, available_credit, cut_date, min_payment,
                payment_due_date, statement_balance,
                opened_at, last_synced_at
            ) VALUES (
                :id, :bank, :bank_account_id, :account_type, :currency, :balance,
                :credit_limit, :available_credit, :cut_date, :min_payment,
                :payment_due_date, :statement_balance,
                :opened_at, :last_synced_at
            )
            ON CONFLICT(bank, bank_account_id) DO UPDATE SET
                account_type     = excluded.account_type,
                currency         = excluded.currency,
                balance          = excluded.balance,
                credit_limit     = excluded.credit_limit,
                available_credit = excluded.available_credit,
                cut_date         = excluded.cut_date,
                min_payment      = excluded.min_payment,
                payment_due_date = excluded.payment_due_date,
                statement_balance = excluded.statement_balance,
                last_synced_at   = excluded.last_synced_at
            """,
            {
                "id": base.id,
                "bank": _bank_from_account(account),
                "bank_account_id": base.bank_account_id,
                "account_type": base.account_type,
                "currency": base.currency,
                "balance": str(base.balance),
                "credit_limit": extra.get("credit_limit"),
                "available_credit": extra.get("available_credit"),
                "cut_date": extra.get("cut_date"),
                "min_payment": extra.get("min_payment"),
                "payment_due_date": extra.get("payment_due_date"),
                "statement_balance": extra.get("statement_balance"),
                "opened_at": base.opened_at.isoformat(),
                "last_synced_at": now,
            },
        )
        self._conn.commit()
        logger.debug("save_account: %s type=%s", base.id, base.account_type)

    # ── Transactions ──────────────────────────────────────────────────────────

    def save_transaction(self, tx: Transaction) -> None:
        """Insert a transaction row (idempotent — ignores duplicate IDs)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO transactions (
                id, account_id, job_id, posted_at, value_at,
                amount, currency, description, fingerprint_hash,
                embedded_id, transfer_match_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.id,
                tx.account_id,
                # job_id is NULL when called via the port without job context.
                # Use save_transaction_with_job() when the job_id is known.
                None,
                tx.posted_at.isoformat(),
                tx.value_at.isoformat(),
                str(tx.amount),  # Decimal → TEXT
                tx.currency,
                tx.description,
                tx.fingerprint_hash,
                tx.embedded_id,
                tx.transfer_match_id,
                _utcnow_iso(),
            ),
        )
        self._conn.commit()
        logger.debug("save_transaction: %s amount=%s", tx.id, tx.amount)

    def save_transaction_with_job(self, tx: Transaction, job_id: str) -> None:
        """Insert a transaction row with an explicit job_id (preferred)."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO transactions (
                id, account_id, job_id, posted_at, value_at,
                amount, currency, description, fingerprint_hash,
                embedded_id, transfer_match_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tx.id,
                tx.account_id,
                job_id,
                tx.posted_at.isoformat(),
                tx.value_at.isoformat(),
                str(tx.amount),
                tx.currency,
                tx.description,
                tx.fingerprint_hash,
                tx.embedded_id,
                tx.transfer_match_id,
                _utcnow_iso(),
            ),
        )
        self._conn.commit()

    # ── Cursors ───────────────────────────────────────────────────────────────

    def get_cursor(self, bank: str) -> str | None:
        """Return the last stored cursor for *bank*, or None if absent."""
        row = self._conn.execute("SELECT cursor FROM cursors WHERE bank = ?", (bank,)).fetchone()
        return row[0] if row else None

    def save_cursor(self, bank: str, cursor: str) -> None:
        """Upsert the cursor for *bank*."""
        self._conn.execute(
            """
            INSERT INTO cursors (bank, cursor, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(bank) DO UPDATE SET
                cursor     = excluded.cursor,
                updated_at = excluded.updated_at
            """,
            (bank, cursor, _utcnow_iso()),
        )
        self._conn.commit()

    # ── Extended query methods (used by use cases) ────────────────────────────

    def list_accounts_by_bank(self, bank: str) -> list[AccountUnion]:
        """Return all accounts for a given bank."""
        rows = self._conn.execute(
            """
            SELECT id, bank, bank_account_id, account_type, currency, balance,
                   credit_limit, available_credit, cut_date, min_payment,
                   payment_due_date, statement_balance, opened_at
            FROM accounts
            WHERE bank = ?
            ORDER BY bank_account_id ASC
            """,
            (bank,),
        ).fetchall()
        return [_row_to_account(row) for row in rows]

    def list_transactions_by_job(self, job_id: str) -> list[Transaction]:
        """Return all transactions associated with a job."""
        rows = self._conn.execute(
            """
            SELECT id, account_id, posted_at, value_at, amount, currency,
                   description, fingerprint_hash, embedded_id, transfer_match_id
            FROM transactions
            WHERE job_id = ?
            ORDER BY posted_at ASC
            """,
            (job_id,),
        ).fetchall()
        return [_row_to_transaction(row) for row in rows]

    # ── Remap proposals ───────────────────────────────────────────────────────

    def save_proposal(self, proposal: RemapProposal) -> None:
        """Insert or update a remap proposal."""
        self._conn.execute(
            """
            INSERT INTO remap_proposals (
                id, bank, breakage_id, judge_decision, confidence, risk,
                patch_diff, status, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status     = excluded.status,
                expires_at = excluded.expires_at
            """,
            (
                proposal.id,
                proposal.bank,
                proposal.breakage_id,
                proposal.judge_decision,
                proposal.confidence,
                proposal.risk,
                proposal.patch_diff,
                str(proposal.status),
                proposal.expires_at.isoformat(),
                _utcnow_iso(),
            ),
        )
        self._conn.commit()

    def load_proposal(self, proposal_id: str) -> RemapProposal | None:
        """Load a remap proposal by ID, returning None if not found."""
        row = self._conn.execute(
            """
            SELECT id, bank, breakage_id, judge_decision, confidence, risk,
                   patch_diff, status, expires_at
            FROM remap_proposals
            WHERE id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_proposal(row)

    # ── Idempotency ───────────────────────────────────────────────────────────

    def get_idempotency(self, key: str) -> dict[str, str] | None:
        """Return stored idempotency record for key, or None if absent."""
        row = self._conn.execute(
            "SELECT key, request_hash, job_id FROM idempotency_keys WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {"key": row[0], "request_hash": row[1], "job_id": row[2]}

    def save_idempotency(self, key: str, request_hash: str, job_id: str) -> None:
        """Persist an idempotency key → job_id mapping."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO idempotency_keys (key, request_hash, job_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, request_hash, job_id, _utcnow_iso()),
        )
        self._conn.commit()


# ── Audit log helpers ─────────────────────────────────────────────────────────


class AuditLogger:
    """Append-only writer for the audit_log table.

    The DB trigger enforces immutability at the SQLite level; this class
    provides a typed interface from application code.

    Args:
        conn: Open SQLCipher connection.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(
        self,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append one audit event; return the new row's UUID."""
        row_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO audit_log (id, actor, action, entity_type, entity_id, at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                actor,
                action,
                entity_type,
                entity_id,
                _utcnow_iso(),
                json.dumps(metadata) if metadata else None,
            ),
        )
        self._conn.commit()
        return row_id


# ── Webhook outbox helpers ────────────────────────────────────────────────────


class WebhookOutbox:
    """Writer / reader for the webhook_outbox transactional outbox.

    Args:
        conn: Open SQLCipher connection.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def enqueue(
        self,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        signature: str,
    ) -> str:
        """Insert a pending webhook event; return the row UUID."""
        row_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO webhook_outbox (
                id, job_id, event_type, payload_json, signature,
                attempts, next_retry_at, delivered_at, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, ?)
            """,
            (row_id, job_id, event_type, json.dumps(payload), signature, _utcnow_iso()),
        )
        self._conn.commit()
        return row_id

    def mark_delivered(self, row_id: str) -> None:
        """Mark a webhook as successfully delivered."""
        self._conn.execute(
            "UPDATE webhook_outbox SET delivered_at = ? WHERE id = ?",
            (_utcnow_iso(), row_id),
        )
        self._conn.commit()

    def increment_attempts(self, row_id: str, next_retry_at: str) -> None:
        """Increment attempt counter and set next retry timestamp."""
        self._conn.execute(
            "UPDATE webhook_outbox SET attempts = attempts + 1, next_retry_at = ? WHERE id = ?",
            (next_retry_at, row_id),
        )
        self._conn.commit()

    def pending(self) -> list[dict[str, Any]]:
        """Return all undelivered webhook rows ready to retry now."""
        rows = self._conn.execute(
            """
            SELECT id, job_id, event_type, payload_json, signature, attempts
            FROM webhook_outbox
            WHERE delivered_at IS NULL
              AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
            ORDER BY created_at ASC
            """
        ).fetchall()
        return [
            {
                "id": r[0],
                "job_id": r[1],
                "event_type": r[2],
                "payload": json.loads(r[3]),
                "signature": r[4],
                "attempts": r[5],
            }
            for r in rows
        ]


# ── Private helpers ───────────────────────────────────────────────────────────


def _row_to_job(row: tuple[Any, ...]) -> Job:
    """Convert a DB row tuple to a Job entity."""
    (
        id_,
        status,
        bank,
        credential_ref,
        mode,
        since_cursor,
        error,
        created_at,
        updated_at,
    ) = row
    return Job(
        id=id_,
        status=JobStatus(status),
        bank=bank,
        credential_ref=credential_ref,
        mode=JobMode(mode),
        since_cursor=since_cursor,
        error=error,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
    )


def _bank_from_account(account: AccountUnion) -> str:
    """Extract the bank identifier from an account entity.

    KNOWN LIMITATION (Task 4): AccountUnion entities do not carry a 'bank'
    field — the domain model identifies accounts by (bank, bank_account_id)
    but the bank is provided by the orchestration context, not the entity.
    The use-case layer (Task 5+) should extend the port or pass bank separately.

    Current fallback strategy (in priority order):
    1. If the account object has a 'bank' attribute (injected at runtime by
       a use-case), use it.
    2. If bank_account_id has the format "<bank>:<type>:<number>", extract
       the prefix as the bank identifier.
    3. Fall back to "unknown" — callers that hit this path should switch to
       the extended port or annotate the account before saving.
    """
    bank_attr = getattr(account, "bank", None)
    if bank_attr is not None:
        return str(bank_attr)
    # Convention: "banco_general:savings:001" → "banco_general"
    return account.bank_account_id.split(":")[0] if ":" in account.bank_account_id else "unknown"


def _decimal_from_text(text: str) -> Decimal:
    """Parse a Decimal stored as TEXT."""
    return Decimal(text)


def _row_to_account(row: tuple[Any, ...]) -> AccountUnion:
    """Convert a DB row tuple to an AccountUnion entity."""
    from datetime import date

    (
        id_,
        _bank,
        bank_account_id,
        account_type,
        currency,
        balance,
        credit_limit,
        available_credit,
        cut_date,
        min_payment,
        payment_due_date,
        statement_balance,
        opened_at,
    ) = row

    common = {
        "id": id_,
        "bank_account_id": bank_account_id,
        "account_type": account_type,
        "currency": currency,
        "balance": Decimal(balance),
        "opened_at": date.fromisoformat(opened_at),
    }

    if account_type == "credit_card":
        return CreditCardAccount(
            **common,
            credit_limit=Decimal(credit_limit or "0"),
            available_credit=Decimal(available_credit or "0"),
            cut_date=date.fromisoformat(cut_date) if cut_date else date.today(),
            min_payment=Decimal(min_payment or "0"),
            payment_due_date=date.fromisoformat(payment_due_date) if payment_due_date else date.today(),
            statement_balance=Decimal(statement_balance or "0"),
        )
    if account_type == "checking":
        return CheckingAccount(**common)
    return SavingsAccount(**common)


def _row_to_transaction(row: tuple[Any, ...]) -> Transaction:
    """Convert a DB row tuple to a Transaction entity."""
    (
        id_,
        account_id,
        posted_at,
        value_at,
        amount,
        currency,
        description,
        fingerprint_hash,
        embedded_id,
        transfer_match_id,
    ) = row
    return Transaction(
        id=id_,
        account_id=account_id,
        posted_at=datetime.fromisoformat(posted_at),
        value_at=datetime.fromisoformat(value_at),
        amount=Decimal(amount),
        currency=currency,
        description=description,
        fingerprint_hash=fingerprint_hash,
        embedded_id=embedded_id,
        transfer_match_id=transfer_match_id,
    )


def _row_to_proposal(row: tuple[Any, ...]) -> RemapProposal:
    """Convert a DB row tuple to a RemapProposal entity."""
    (
        id_,
        bank,
        breakage_id,
        judge_decision,
        confidence,
        risk,
        patch_diff,
        status,
        expires_at,
    ) = row
    return RemapProposal(
        id=id_,
        bank=bank,
        breakage_id=breakage_id,
        judge_decision=judge_decision,
        confidence=confidence,
        risk=risk,
        patch_diff=patch_diff,
        status=RemapStatus(status),
        expires_at=datetime.fromisoformat(expires_at),
    )


def canonical_request_hash(body_json: str) -> str:
    """Return SHA-256 hex digest of a canonical request body string."""
    return hashlib.sha256(body_json.encode()).hexdigest()

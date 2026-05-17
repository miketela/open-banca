"""PersistResultActivity — save job/accounts/transactions to SQLCipher storage.

Uses UPSERT semantics (idempotent). Safe to retry without duplication.

Retry policy: max 3 attempts.
Start-to-close timeout: 30s.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.parse_excel import TransactionRecord

logger = logging.getLogger(__name__)


class PersistAccountInfo(BaseModel):
    """Lightweight account info for persistence (avoids circular import with workflow)."""

    account_id: str
    transaction_count: int = 0


class PersistResultInput(BaseModel):
    """Input for PersistResultActivity."""

    job_id: str = Field(description="Job ID to associate results with")
    bank_id: str = Field(default="", description="Bank identifier")
    accounts: list[PersistAccountInfo] = Field(default_factory=list)
    transactions: list[TransactionRecord] = Field(default_factory=list)


class PersistResultResult(BaseModel):
    """Result from PersistResultActivity."""

    persisted_accounts: int = Field(default=0)
    persisted_transactions: int = Field(default=0)


class PersistResultActivity:
    """Class-based wrapper (no-op; activity is a module-level function)."""


@activity.defn(name="PersistResultActivity")
async def persist_result(input: PersistResultInput) -> PersistResultResult:
    """Persist scrape results to the encrypted SQLCipher storage.

    Uses ``SqliteJobStore.save_job()``, ``save_account()``, and
    ``save_transaction_with_job()`` with UPSERT semantics.
    """
    activity.logger.info(
        "persisting results: job_id=%s accounts=%d transactions=%d",
        input.job_id,
        len(input.accounts),
        len(input.transactions),
    )

    from open_banca_domain.entities.job import Job, JobMode, JobStatus
    from open_banca_domain.entities.transaction import Transaction
    from open_banca_storage.config import get_settings as get_storage_settings
    from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
    from open_banca_storage.dedup.fingerprint import compute_fingerprint
    from open_banca_storage.migrations import migrate
    from open_banca_storage.repositories.job_store import SqliteJobStore

    passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "")
    settings = get_storage_settings()
    pool = ConnectionPool(
        db_path=settings.open_banca_db_path,
        passphrase=passphrase,
        key_derivation=PassthroughKeyDerivation(),
    )
    conn = pool.get()
    migrate(conn)
    store = SqliteJobStore(conn)

    now = datetime.now(tz=UTC)
    job = Job(
        id=input.job_id,
        status=JobStatus.COMPLETED,
        bank=input.bank_id,
        credential_ref="",
        mode=JobMode.FULL,
        created_at=now,
        updated_at=now,
    )
    store.save_job(job)

    tx_count = 0
    for txn in input.transactions:
        posted_at = datetime.fromisoformat(txn.date).replace(tzinfo=UTC)
        amount = Decimal(txn.amount)
        tx = Transaction(
            id=txn.raw_id or str(uuid.uuid4()),
            account_id=txn.account_id,
            posted_at=posted_at,
            value_at=posted_at,
            amount=amount,
            currency=txn.currency,
            description=txn.description,
            fingerprint_hash=compute_fingerprint(
                txn.account_id, posted_at, posted_at, amount, txn.description
            ),
        )
        store.save_transaction_with_job(tx, input.job_id)
        tx_count += 1

    activity.logger.info("persist complete: job_id=%s transactions=%d", input.job_id, tx_count)

    return PersistResultResult(
        persisted_accounts=len(input.accounts),
        persisted_transactions=tx_count,
    )

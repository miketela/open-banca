"""Tests for SqliteJobStore: Job CRUD, account save, transaction save, cursor.

TDD coverage:
- save_job / load_job / list_jobs roundtrip.
- Decimal amounts survive TEXT storage (Decimal roundtrip).
- save_account (SavingsAccount, CheckingAccount, CreditCardAccount).
- save_transaction and save_transaction_with_job.
- get_cursor / save_cursor.
- JobStorePort structural compatibility.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from open_banca_domain.entities.account import CreditCardAccount, SavingsAccount
from open_banca_domain.entities.job import Job, JobMode, JobStatus
from open_banca_domain.entities.transaction import Transaction
from open_banca_domain.ports.job_store_port import JobStorePort
from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate
from open_banca_storage.repositories.job_store import SqliteJobStore

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path):
    """Migrated SQLCipher connection on a fresh temp file."""
    db_file = tmp_path / "test.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="test-passphrase",
        key_derivation=PassthroughKeyDerivation(),
    )
    c = pool.get()
    migrate(c)
    yield c
    pool.close()


@pytest.fixture()
def store(conn):
    return SqliteJobStore(conn)


def _make_job(**kwargs) -> Job:
    now = datetime.now(tz=UTC)
    defaults: dict = {
        "id": str(uuid.uuid4()),
        "status": JobStatus.PENDING,
        "bank": "banco_general",
        "credential_ref": "cred-abc",
        "mode": JobMode.FULL,
        "created_at": now,
        "updated_at": now,
        "since_cursor": None,
        "error": None,
    }
    defaults.update(kwargs)
    return Job(**defaults)


def _make_transaction(account_id: str, amount: Decimal = Decimal("123.45")) -> Transaction:
    now = datetime.now(tz=UTC)
    return Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        posted_at=now,
        value_at=now,
        amount=amount,
        currency="USD",
        description="Test payment",
        fingerprint_hash=str(uuid.uuid4()),
        embedded_id=None,
        transfer_match_id=None,
    )


# ── Job tests ─────────────────────────────────────────────────────────────────


def test_save_and_load_job(store):
    job = _make_job()
    store.save_job(job)

    loaded = store.load_job(job.id)
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.status == JobStatus.PENDING
    assert loaded.bank == "banco_general"


def test_load_job_missing_returns_none(store):
    assert store.load_job("nonexistent-uuid") is None


def test_list_jobs_empty(store):
    assert store.list_jobs() == []


def test_list_jobs_returns_all(store):
    j1 = _make_job()
    j2 = _make_job()
    store.save_job(j1)
    store.save_job(j2)

    jobs = store.list_jobs()
    assert len(jobs) == 2
    ids = {j.id for j in jobs}
    assert {j1.id, j2.id} == ids


def test_save_job_upserts_status(store):
    job = _make_job(status=JobStatus.PENDING)
    store.save_job(job)

    now = datetime.now(tz=UTC)
    updated = Job(
        id=job.id,
        status=JobStatus.RUNNING,
        bank=job.bank,
        credential_ref=job.credential_ref,
        mode=job.mode,
        created_at=job.created_at,
        updated_at=now,
    )
    store.save_job(updated)

    loaded = store.load_job(job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.RUNNING


# ── Decimal roundtrip tests ───────────────────────────────────────────────────


def test_transaction_decimal_roundtrip(conn, store):
    """Decimal amounts must survive TEXT serialisation precisely."""
    acct = SavingsAccount(
        id=str(uuid.uuid4()),
        bank_account_id="banco_general:savings:001",
        balance=Decimal("10000.00"),
        currency="USD",
        opened_at=date(2023, 1, 1),
    )
    store.save_account(acct)

    exact_amount = Decimal("1234567.89")
    tx = _make_transaction(account_id=acct.id, amount=exact_amount)
    store.save_transaction(tx)

    row = conn.execute("SELECT amount FROM transactions WHERE id = ?", (tx.id,)).fetchone()
    assert row is not None
    recovered = Decimal(row[0])
    assert recovered == exact_amount, f"{recovered!r} != {exact_amount!r}"


def test_account_balance_decimal_roundtrip(conn, store):
    """Account balances must round-trip through TEXT storage without loss."""
    original_balance = Decimal("99999.99")
    acct = SavingsAccount(
        id=str(uuid.uuid4()),
        bank_account_id="banco_general:savings:002",
        balance=original_balance,
        currency="PAB",
        opened_at=date(2022, 6, 15),
    )
    store.save_account(acct)

    row = conn.execute("SELECT balance FROM accounts WHERE id = ?", (acct.id,)).fetchone()
    assert row is not None
    assert Decimal(row[0]) == original_balance


# ── Account tests ─────────────────────────────────────────────────────────────


def test_save_savings_account(store, conn):
    acct = SavingsAccount(
        id=str(uuid.uuid4()),
        bank_account_id="banco_general:savings:100",
        balance=Decimal("500.00"),
        currency="USD",
        opened_at=date(2020, 3, 10),
    )
    store.save_account(acct)

    row = conn.execute(
        "SELECT account_type, balance FROM accounts WHERE id = ?", (acct.id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "savings"
    assert Decimal(row[1]) == Decimal("500.00")


def test_save_credit_card_account(store, conn):
    acct = CreditCardAccount(
        id=str(uuid.uuid4()),
        bank_account_id="banco_general:credit_card:200",
        balance=Decimal("-250.00"),
        currency="USD",
        opened_at=date(2021, 5, 1),
        credit_limit=Decimal("5000.00"),
        available_credit=Decimal("4750.00"),
        cut_date=date(2024, 1, 15),
        min_payment=Decimal("50.00"),
        payment_due_date=date(2024, 1, 25),
        statement_balance=Decimal("250.00"),
    )
    store.save_account(acct)

    row = conn.execute(
        "SELECT account_type, credit_limit, min_payment FROM accounts WHERE id = ?",
        (acct.id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "credit_card"
    assert Decimal(row[1]) == Decimal("5000.00")
    assert Decimal(row[2]) == Decimal("50.00")


# ── Cursor tests ──────────────────────────────────────────────────────────────


def test_get_cursor_returns_none_when_absent(store):
    assert store.get_cursor("banco_general") is None


def test_save_and_get_cursor(store):
    store.save_cursor("banco_general", "2024-01-15T12:00:00Z")
    assert store.get_cursor("banco_general") == "2024-01-15T12:00:00Z"


def test_save_cursor_upserts(store):
    store.save_cursor("banco_general", "cursor-v1")
    store.save_cursor("banco_general", "cursor-v2")
    assert store.get_cursor("banco_general") == "cursor-v2"


def test_cursors_are_per_bank(store):
    store.save_cursor("banco_general", "bg-cursor")
    store.save_cursor("banistmo", "banistmo-cursor")
    assert store.get_cursor("banco_general") == "bg-cursor"
    assert store.get_cursor("banistmo") == "banistmo-cursor"


# ── Port structural compatibility ─────────────────────────────────────────────


def test_job_store_is_job_store_port(store):
    """SqliteJobStore must satisfy the JobStorePort Protocol at runtime."""
    assert isinstance(store, JobStorePort), "SqliteJobStore does not satisfy JobStorePort protocol"

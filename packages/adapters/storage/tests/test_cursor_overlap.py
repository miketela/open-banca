"""Tests for task-32: cursor overlap dedup — incremental scrape re-ingests no duplicates.

Test matrix:
  - test_cursor_persisted_after_ingest: cursor advances to max posted_at after ingest.
  - test_cursor_overlap_dedup: scrape 1 + scrape 2 with 3d overlap → no duplicates.
  - test_cursor_buffer_3days_effective: effective_since = cursor - CURSOR_BUFFER_DAYS.
  - test_cursor_none_first_run: no transactions → cursor is None.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from open_banca_domain.entities.transaction import Transaction
from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.dedup.engine import CURSOR_BUFFER_DAYS, DedupEngine
from open_banca_storage.dedup.fingerprint import compute_fingerprint
from open_banca_storage.migrations import migrate

# ── Helpers ───────────────────────────────────────────────────────────────────


def _insert_account(conn, acc_id: str | None = None) -> str:
    """Insert a savings account row and return its UUID."""
    aid = acc_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO accounts
            (id, bank, bank_account_id, account_type, currency, balance, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (aid, "banco_general", f"bg:savings:{aid[:8]}", "savings", "USD", "0.00", "2020-01-01"),
    )
    conn.commit()
    return aid


def _make_tx(
    account_id: str,
    *,
    amount: Decimal = Decimal("100.00"),
    description: str = "Test payment",
    posted_at: datetime | None = None,
) -> Transaction:
    now = posted_at or datetime.now(tz=UTC)
    fp = compute_fingerprint(
        account_id=account_id,
        posted_at=now,
        value_at=now,
        amount=amount,
        description=description,
    )
    return Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        posted_at=now,
        value_at=now,
        amount=amount,
        currency="USD",
        description=description,
        fingerprint_hash=fp,
        embedded_id=None,
        transfer_match_id=None,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path: Path):
    """Fresh migrated SQLCipher connection."""
    db_file = tmp_path / "test_cursor_overlap.db"
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
def engine(conn):
    return DedupEngine(conn)


@pytest.fixture()
def account_id(conn) -> str:
    return _insert_account(conn)


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_cursor_persisted_after_ingest(engine, account_id) -> None:
    """Cursor advances to max posted_at after transactions are ingested."""
    base = datetime(2025, 5, 1, 0, 0, 0, tzinfo=UTC)
    tx1 = _make_tx(account_id, posted_at=base)
    tx2 = _make_tx(account_id, posted_at=base + timedelta(days=5))

    engine.ingest([tx1, tx2])

    cursor = engine.get_cursor(account_id)
    assert cursor is not None
    # Cursor should be max posted_at = base + 5 days
    assert cursor.date() == (base + timedelta(days=5)).date()


def test_cursor_none_first_run(engine, account_id) -> None:
    """No transactions → cursor is None (first run indicator)."""
    assert engine.get_cursor(account_id) is None
    assert engine.effective_since(account_id) is None


def test_cursor_buffer_3days_effective(engine, account_id) -> None:
    """effective_since = cursor - CURSOR_BUFFER_DAYS (3 days)."""
    base = datetime(2025, 5, 20, 12, 0, 0, tzinfo=UTC)
    tx = _make_tx(account_id, posted_at=base)
    engine.ingest([tx])

    effective = engine.effective_since(account_id)
    assert effective is not None
    expected = base - timedelta(days=CURSOR_BUFFER_DAYS)
    assert abs((effective - expected).total_seconds()) < 1.0


def test_cursor_overlap_dedup_no_duplicates(engine, account_id) -> None:
    """Scrape 1 ingests days 1-10. Scrape 2 re-ingests days 8-10 (3d buffer overlap).

    The overlapping transactions from scrape 2 must be treated as duplicates
    and NOT inserted again.  Only truly new transactions (days 11-15) should
    be counted as new.
    """
    base = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)

    # Scrape 1: transactions on days 1, 5, 10
    scrape1_txs = [
        _make_tx(account_id, description="Payment A", posted_at=base + timedelta(days=1)),
        _make_tx(account_id, description="Payment B", posted_at=base + timedelta(days=5)),
        _make_tx(account_id, description="Payment C", posted_at=base + timedelta(days=10)),
    ]
    result1 = engine.ingest(scrape1_txs)
    assert len(result1.new) == 3
    assert len(result1.duplicates) == 0

    # Cursor after scrape 1 = base + 10d
    cursor = engine.get_cursor(account_id)
    assert cursor is not None
    effective = engine.effective_since(account_id)
    assert effective is not None
    # effective_since = (base + 10d) - 3d = base + 7d
    assert effective.date() == (base + timedelta(days=7)).date()

    # Scrape 2: overlapping window from effective_since (base + 7d) to base + 15d
    # Includes the original tx at day 10 (overlap) + new txs at days 11, 15
    overlap_tx = _make_tx(account_id, description="Payment C", posted_at=base + timedelta(days=10))
    new_tx1 = _make_tx(account_id, description="Payment D", posted_at=base + timedelta(days=11))
    new_tx2 = _make_tx(account_id, description="Payment E", posted_at=base + timedelta(days=15))

    result2 = engine.ingest([overlap_tx, new_tx1, new_tx2])
    # overlap_tx duplicates payment C → should be in duplicates, not new
    assert len(result2.duplicates) == 1, f"Expected 1 duplicate, got {result2.duplicates}"
    assert len(result2.new) == 2, f"Expected 2 new, got {result2.new}"

    # Final cursor should advance to base + 15d
    final_cursor = engine.get_cursor(account_id)
    assert final_cursor is not None
    assert final_cursor.date() == (base + timedelta(days=15)).date()


def test_cursor_overlap_embedded_id_dedup(engine, account_id) -> None:
    """Overlap with embedded_id transactions: same embedded_id → no duplicate stored."""
    base = datetime(2025, 7, 1, 0, 0, 0, tzinfo=UTC)

    # Scrape 1: embedded-id transactions
    tx_original = Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        posted_at=base + timedelta(days=5),
        value_at=base + timedelta(days=5),
        amount=Decimal("500.00"),
        currency="USD",
        description="Wire transfer",
        fingerprint_hash=compute_fingerprint(
            account_id=account_id,
            posted_at=base + timedelta(days=5),
            value_at=base + timedelta(days=5),
            amount=Decimal("500.00"),
            description="Wire transfer",
        ),
        embedded_id="WIRE-2025-0001",
        transfer_match_id=None,
    )
    result1 = engine.ingest([tx_original])
    assert len(result1.new) == 1

    # Scrape 2: same embedded_id in overlap window
    tx_overlap = Transaction(
        id=str(uuid.uuid4()),  # different UUID (bank generated new one)
        account_id=account_id,
        posted_at=base + timedelta(days=5),
        value_at=base + timedelta(days=5),
        amount=Decimal("500.00"),
        currency="USD",
        description="Wire transfer",
        fingerprint_hash=compute_fingerprint(
            account_id=account_id,
            posted_at=base + timedelta(days=5),
            value_at=base + timedelta(days=5),
            amount=Decimal("500.00"),
            description="Wire transfer",
        ),
        embedded_id="WIRE-2025-0001",  # same embedded_id
        transfer_match_id=None,
    )
    result2 = engine.ingest([tx_overlap])
    assert len(result2.new) == 0
    assert len(result2.duplicates) == 1

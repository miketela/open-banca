"""Tests for the 3-level deduplication engine (Task 17).

TDD coverage:
- Level 1 (embedded_id): two ingests with same embedded_id → 1 transaction.
- Level 2 (fingerprint): two ingests with same description+amount+date → 1.
- Level 3 (transfer): outflow A + inflow B same amount/date → transfer pair.
- Cursor incremental: second ingest from cursor brings only new transactions.
- Cursor buffer 3 days: effective_since = cursor - 3d.
- Unicode normalization: full-width digits dedup correctly via NFKC.
- Hypothesis: 100 arbitrary transactions, dedup idempotent.
- compute_fingerprint: deterministic across calls.
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from open_banca_domain.entities.transaction import Transaction
from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.dedup.engine import CURSOR_BUFFER_DAYS, DedupEngine
from open_banca_storage.dedup.fingerprint import compute_fingerprint
from open_banca_storage.migrations import migrate

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fresh_engine(db_path: Path) -> tuple[DedupEngine, Any]:
    """Create a fresh migrated DB and return (engine, conn)."""
    pool = ConnectionPool(
        db_path=db_path,
        passphrase="test-passphrase",
        key_derivation=PassthroughKeyDerivation(),
    )
    c = pool.get()
    migrate(c)
    return DedupEngine(c), c


def _insert_account(conn: Any, acc_id: str | None = None) -> str:
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
    embedded_id: str | None = None,
    posted_at: datetime | None = None,
    fingerprint_hash: str | None = None,
) -> Transaction:
    now = posted_at or datetime.now(tz=UTC)
    fp = fingerprint_hash or compute_fingerprint(
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
        embedded_id=embedded_id,
        transfer_match_id=None,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path):
    """Migrated SQLCipher connection on a fresh temp file."""
    db_file = tmp_path / "test_dedup.db"
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
    """DedupEngine backed by the test connection."""
    return DedupEngine(conn)


@pytest.fixture()
def account_id(conn) -> str:
    """Insert a savings account and return its UUID."""
    return _insert_account(conn)


@pytest.fixture()
def account_b_id(conn) -> str:
    """Insert a second savings account and return its UUID."""
    return _insert_account(conn)


# ── Level 1 (embedded_id) ─────────────────────────────────────────────────────


def test_dedup_level1_embedded_id(engine, account_id, conn):
    """Two ingests with same embedded_id → only 1 transaction persisted."""
    emb = "BANCO-TX-00001"
    tx1 = _make_tx(account_id, embedded_id=emb)
    tx2 = _make_tx(account_id, embedded_id=emb, amount=Decimal("100.00"))

    result1 = engine.ingest([tx1])
    assert len(result1.new) == 1
    assert len(result1.duplicates) == 0

    result2 = engine.ingest([tx2])
    assert len(result2.new) == 0
    assert len(result2.duplicates) == 1
    assert result2.duplicates[0].embedded_id == emb

    # Only one row in the DB.
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)
    ).fetchone()[0]
    assert count == 1


def test_dedup_level1_different_embedded_ids(engine, account_id, conn):
    """Different embedded IDs → both transactions persisted."""
    tx1 = _make_tx(account_id, embedded_id="TX-001")
    tx2 = _make_tx(account_id, embedded_id="TX-002")

    engine.ingest([tx1, tx2])

    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)
    ).fetchone()[0]
    assert count == 2


# ── Level 2 (fingerprint) ─────────────────────────────────────────────────────


def test_dedup_level2_fingerprint(engine, account_id, conn):
    """Two ingests with same description+amount+date → only 1 transaction."""
    posted = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    fp = compute_fingerprint(
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=Decimal("50.00"),
        description="Compra supermercado",
    )
    tx1 = _make_tx(
        account_id,
        amount=Decimal("50.00"),
        description="Compra supermercado",
        posted_at=posted,
        fingerprint_hash=fp,
    )
    # tx2: same canonical fields, different UUID.
    tx2 = Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=Decimal("50.00"),
        currency="USD",
        description="Compra supermercado",
        fingerprint_hash=fp,
        embedded_id=None,
        transfer_match_id=None,
    )
    assert tx1.id != tx2.id
    assert tx1.fingerprint_hash == tx2.fingerprint_hash

    result1 = engine.ingest([tx1])
    assert len(result1.new) == 1

    result2 = engine.ingest([tx2])
    assert len(result2.new) == 0
    assert len(result2.duplicates) == 1

    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)
    ).fetchone()[0]
    assert count == 1


def test_dedup_level2_different_amounts(engine, account_id, conn):
    """Different amounts → different fingerprints → both stored."""
    posted = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    tx1 = _make_tx(account_id, amount=Decimal("50.00"), posted_at=posted)
    tx2 = _make_tx(account_id, amount=Decimal("75.00"), posted_at=posted)

    engine.ingest([tx1, tx2])
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)
    ).fetchone()[0]
    assert count == 2


# ── Level 3 (transfer) ────────────────────────────────────────────────────────


def test_dedup_level3_transfer(engine, account_id, account_b_id, conn):
    """Outflow from account A + inflow to account B of same amount/date → transfer pair."""
    posted = datetime(2024, 3, 10, 9, 0, 0, tzinfo=UTC)
    debit = _make_tx(account_id, amount=Decimal("-200.00"), description="Transfer out", posted_at=posted)
    credit = _make_tx(account_b_id, amount=Decimal("200.00"), description="Transfer in", posted_at=posted)

    result = engine.ingest([debit, credit])

    assert len(result.new) == 2
    assert len(result.transfer_pairs) == 1

    pair_debit, pair_credit = result.transfer_pairs[0]
    assert pair_debit.amount < Decimal("0")
    assert pair_credit.amount > Decimal("0")

    # Both rows in the DB should have transfer_match_id set.
    debit_row = conn.execute(
        "SELECT transfer_match_id FROM transactions WHERE id = ?", (debit.id,)
    ).fetchone()
    credit_row = conn.execute(
        "SELECT transfer_match_id FROM transactions WHERE id = ?", (credit.id,)
    ).fetchone()

    assert debit_row is not None and debit_row[0] == credit.id
    assert credit_row is not None and credit_row[0] == debit.id


def test_dedup_level3_no_match_different_amounts(engine, account_id, account_b_id):
    """Outflow and inflow with different amounts → no transfer pair."""
    posted = datetime(2024, 3, 10, 9, 0, 0, tzinfo=UTC)
    debit = _make_tx(account_id, amount=Decimal("-200.00"), posted_at=posted)
    credit = _make_tx(account_b_id, amount=Decimal("150.00"), posted_at=posted)

    result = engine.ingest([debit, credit])
    assert len(result.transfer_pairs) == 0


def test_dedup_level3_no_match_same_account(engine, account_id):
    """Same-account debit/credit pair → not a transfer."""
    posted = datetime(2024, 3, 10, 9, 0, 0, tzinfo=UTC)
    debit = _make_tx(account_id, amount=Decimal("-100.00"), posted_at=posted)
    credit = _make_tx(account_id, amount=Decimal("100.00"), posted_at=posted)

    result = engine.ingest([debit, credit])
    assert len(result.transfer_pairs) == 0


def test_dedup_level3_date_window_exceeded(engine, account_id, account_b_id):
    """Debit and credit more than 3 days apart → no transfer pair."""
    debit_dt = datetime(2024, 3, 10, 9, 0, 0, tzinfo=UTC)
    credit_dt = debit_dt + timedelta(days=4)  # Beyond ±3 day window.
    debit = _make_tx(account_id, amount=Decimal("-100.00"), posted_at=debit_dt)
    credit = _make_tx(account_b_id, amount=Decimal("100.00"), posted_at=credit_dt)

    result = engine.ingest([debit, credit])
    assert len(result.transfer_pairs) == 0


def test_dedup_level3_within_date_window(engine, account_id, account_b_id):
    """Debit and credit 3 days apart → transfer pair matched."""
    debit_dt = datetime(2024, 3, 10, 9, 0, 0, tzinfo=UTC)
    credit_dt = debit_dt + timedelta(days=3)  # Exactly at boundary.
    debit = _make_tx(account_id, amount=Decimal("-100.00"), posted_at=debit_dt)
    credit = _make_tx(account_b_id, amount=Decimal("100.00"), posted_at=credit_dt)

    result = engine.ingest([debit, credit])
    assert len(result.transfer_pairs) == 1


# ── Cursor (incremental) ──────────────────────────────────────────────────────


def test_cursor_incremental(engine, account_id):
    """Second ingest after cursor → only new transactions counted."""
    base = datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
    old_tx = _make_tx(account_id, posted_at=base)
    new_tx = _make_tx(account_id, posted_at=base + timedelta(days=10))

    engine.ingest([old_tx])

    cursor = engine.get_cursor(account_id)
    assert cursor is not None
    assert cursor.date() == base.date()

    result2 = engine.ingest([new_tx])
    assert len(result2.new) == 1

    cursor2 = engine.get_cursor(account_id)
    assert cursor2 is not None
    assert cursor2 > cursor


def test_cursor_no_transactions_returns_none(engine, account_id):
    """get_cursor returns None when no transactions exist for account."""
    assert engine.get_cursor(account_id) is None


def test_cursor_buffer_3days(engine, account_id):
    """effective_since = cursor - 3 days."""
    base = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
    tx = _make_tx(account_id, posted_at=base)
    engine.ingest([tx])

    effective = engine.effective_since(account_id)
    assert effective is not None
    expected = base - timedelta(days=CURSOR_BUFFER_DAYS)
    # Compare to second precision.
    assert abs((effective - expected).total_seconds()) < 1.0


def test_cursor_buffer_none_when_no_data(engine, account_id):
    """effective_since returns None when no cursor exists (first run)."""
    assert engine.effective_since(account_id) is None


# ── Unicode normalization ─────────────────────────────────────────────────────


def test_unicode_normalization(engine, account_id, conn):
    """Descriptions with full-width digits dedup correctly via NFKC normalisation.

    Full-width digits (U+FF10-U+FF19) and ASCII digits (U+0030-U+0039) are
    NFKC-compatibility equivalents.  A bank may emit either form; the engine
    must treat them as the same transaction.
    """
    posted = datetime(2024, 2, 20, 8, 0, 0, tzinfo=UTC)
    desc_ascii = "Pago ref 123"
    # Build the full-width variant at runtime using chr() to avoid ruff RUF001.
    # U+FF11 = FULLWIDTH DIGIT ONE, ..., U+FF13 = FULLWIDTH DIGIT THREE.
    fullwidth_123 = chr(0xFF11) + chr(0xFF12) + chr(0xFF13)
    desc_fullwidth = f"Pago ref {fullwidth_123}"

    # Ensure the raw strings differ.
    assert desc_ascii != desc_fullwidth
    # Ensure NFKC makes them identical.
    assert unicodedata.normalize("NFKC", desc_ascii) == unicodedata.normalize("NFKC", desc_fullwidth)

    fp_ascii = compute_fingerprint(
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=Decimal("99.00"),
        description=desc_ascii,
    )
    fp_fullwidth = compute_fingerprint(
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=Decimal("99.00"),
        description=desc_fullwidth,
    )
    assert fp_ascii == fp_fullwidth, (
        "NFKC normalisation failed: ASCII and full-width descriptions produce different fingerprints"
    )

    tx1 = _make_tx(account_id, amount=Decimal("99.00"), description=desc_ascii, posted_at=posted)
    tx2 = Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=Decimal("99.00"),
        currency="USD",
        description=desc_fullwidth,
        fingerprint_hash=fp_fullwidth,
        embedded_id=None,
        transfer_match_id=None,
    )

    result1 = engine.ingest([tx1])
    assert len(result1.new) == 1

    result2 = engine.ingest([tx2])
    assert len(result2.new) == 0
    assert len(result2.duplicates) == 1


# ── compute_fingerprint: determinism ─────────────────────────────────────────


def test_fingerprint_deterministic():
    """Same inputs always produce the same fingerprint (no randomness)."""
    account_id = "acc-determinism"
    posted = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    kwargs = dict(
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=Decimal("42.00"),
        description="Determinism check",
    )
    fp1 = compute_fingerprint(**kwargs)
    fp2 = compute_fingerprint(**kwargs)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex length.


def test_fingerprint_differs_on_amount_change():
    """Different amounts → different fingerprints."""
    posted = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    fp1 = compute_fingerprint("acc", posted, posted, Decimal("10.00"), "desc")
    fp2 = compute_fingerprint("acc", posted, posted, Decimal("10.01"), "desc")
    assert fp1 != fp2


def test_fingerprint_differs_on_account_change():
    """Different accounts → different fingerprints."""
    posted = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    fp1 = compute_fingerprint("acc-A", posted, posted, Decimal("10.00"), "desc")
    fp2 = compute_fingerprint("acc-B", posted, posted, Decimal("10.00"), "desc")
    assert fp1 != fp2


# ── Hypothesis: idempotency ───────────────────────────────────────────────────


@st.composite
def _transaction_strategy(draw: st.DrawFn, account_id: str) -> Transaction:
    """Build an arbitrary Transaction for property testing."""
    amount = draw(
        st.decimals(min_value=Decimal("-9999.99"), max_value=Decimal("9999.99"), places=2).filter(
            lambda d: d != Decimal("0")
        )
    )
    description = draw(st.text(min_size=1, max_size=80))
    days_offset = draw(st.integers(min_value=0, max_value=365))
    base = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    posted = base + timedelta(days=days_offset)
    fp = compute_fingerprint(
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=amount,
        description=description,
    )
    return Transaction(
        id=str(uuid.uuid4()),
        account_id=account_id,
        posted_at=posted,
        value_at=posted,
        amount=amount,
        currency="USD",
        description=description,
        fingerprint_hash=fp,
        embedded_id=None,
        transfer_match_id=None,
    )


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.data())
def test_hypothesis_dedup_idempotent(tmp_path: Path, data: st.DataObject) -> None:
    """Ingesting the same batch twice produces no new transactions on the second pass."""
    db_file = tmp_path / f"hyp_{uuid.uuid4()}.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="hyp-pass",
        key_derivation=PassthroughKeyDerivation(),
    )
    c = pool.get()
    migrate(c)

    # Insert a dummy account.
    acc_id = _insert_account(c)
    eng = DedupEngine(c)

    size = data.draw(st.integers(min_value=1, max_value=20))
    transactions = [data.draw(_transaction_strategy(acc_id)) for _ in range(size)]

    eng.ingest(transactions)
    # Second ingest of the same batch must produce zero new transactions.
    result2 = eng.ingest(transactions)

    assert len(result2.new) == 0, (
        f"Idempotency violated: second ingest produced {len(result2.new)} new transactions"
    )
    pool.close()

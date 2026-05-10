"""Property-based tests for Decimal TEXT storage using Hypothesis.

Verifies that arbitrary Decimal values survive a str() → Decimal() roundtrip
without precision loss, and that transaction amounts stored in the DB round-trip
correctly for a wide range of financial values.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from open_banca_domain.entities.account import SavingsAccount
from open_banca_domain.entities.transaction import Transaction
from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate
from open_banca_storage.repositories.job_store import SqliteJobStore

# ── Hypothesis unit tests (no DB) ─────────────────────────────────────────────


@given(
    st.decimals(
        allow_nan=False,
        allow_infinity=False,
        min_value=Decimal("-999999999.99"),
        max_value=Decimal("999999999.99"),
    )
)
def test_decimal_str_roundtrip_pure(value: Decimal) -> None:
    """str(Decimal) → Decimal must be lossless for any finite value."""
    recovered = Decimal(str(value))
    assert recovered == value, f"Roundtrip failed: {value!r} → {recovered!r}"


# ── Hypothesis DB integration tests ───────────────────────────────────────────
# Hypothesis @given cannot receive pytest fixtures as parameters alongside the
# hypothesis-provided strategy values.  Use a class-level setup approach with
# a module-scoped resource created once via autouse fixture.


_shared_db_state: dict = {}


@pytest.fixture(scope="module", autouse=True)
def setup_shared_db(tmp_path_factory):
    """Set up a module-scoped DB for hypothesis DB tests."""
    db_file = tmp_path_factory.mktemp("hyp") / "hyp_test.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="hyp-test-key",
        key_derivation=PassthroughKeyDerivation(),
    )
    conn = pool.get()
    migrate(conn)

    # Insert the shared account used by hypothesis examples
    acct = SavingsAccount(
        id="hyp-acct-1",
        bank_account_id="banco_general:savings:hyp",
        balance=Decimal("0"),
        currency="USD",
        opened_at=date(2023, 1, 1),
    )
    store = SqliteJobStore(conn)
    store.save_account(acct)

    _shared_db_state["conn"] = conn
    _shared_db_state["store"] = store

    yield

    pool.close()
    _shared_db_state.clear()


@settings(max_examples=50)
@given(
    st.decimals(
        allow_nan=False,
        allow_infinity=False,
        min_value=Decimal("-999999.99"),
        max_value=Decimal("999999.99"),
    )
)
def test_transaction_amount_db_roundtrip(value: Decimal) -> None:
    """Transaction amounts round-trip through TEXT storage in the DB."""
    conn = _shared_db_state["conn"]
    store = _shared_db_state["store"]

    tx = Transaction(
        id=str(uuid.uuid4()),
        account_id="hyp-acct-1",
        posted_at=datetime.now(tz=UTC),
        value_at=datetime.now(tz=UTC),
        amount=value,
        currency="USD",
        description="hypothesis test",
        fingerprint_hash=str(uuid.uuid4()),
    )
    store.save_transaction(tx)

    row = conn.execute("SELECT amount FROM transactions WHERE id = ?", (tx.id,)).fetchone()
    assert row is not None
    recovered = Decimal(row[0])
    assert recovered == value, f"DB roundtrip failed: {value!r} → {recovered!r}"

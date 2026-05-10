"""Tests for shared schemas: account + transaction factories and re-exports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from open_banca_schemas import (
    AccountUnionSchema,
    CheckingAccountSchema,
    CreditCardAccountSchema,
    SavingsAccountSchema,
    TransactionSchema,
    make_checking_account,
    make_credit_card_account,
    make_savings_account,
    make_transaction,
)

# ── Account factories ─────────────────────────────────────────────────────────


def test_make_savings_account_defaults():
    acct = make_savings_account()
    assert acct.account_type == "savings"
    assert acct.currency == "USD"
    assert isinstance(acct, SavingsAccountSchema)


def test_make_checking_account_defaults():
    acct = make_checking_account()
    assert acct.account_type == "checking"
    assert isinstance(acct, CheckingAccountSchema)


def test_make_credit_card_account_defaults():
    acct = make_credit_card_account()
    assert acct.account_type == "credit_card"
    assert acct.credit_limit == Decimal("5000.00")
    assert isinstance(acct, CreditCardAccountSchema)


def test_make_savings_account_custom_balance():
    acct = make_savings_account(balance=Decimal("9999.99"), currency="PAB")
    assert acct.balance == Decimal("9999.99")
    assert acct.currency == "PAB"


def test_account_union_schema_discriminates():
    """AccountUnionSchema correctly parses savings, checking, credit_card."""
    from pydantic import TypeAdapter
    adapter = TypeAdapter(AccountUnionSchema)
    raw_savings = {
        "id": str(uuid.uuid4()),
        "bank_account_id": "bg:savings:001",
        "balance": "100.00",
        "currency": "USD",
        "opened_at": "2020-01-01",
        "account_type": "savings",
    }
    parsed = adapter.validate_python(raw_savings)
    assert parsed.account_type == "savings"


# ── Transaction factory ───────────────────────────────────────────────────────


def test_make_transaction_computes_fingerprint():
    acc_id = str(uuid.uuid4())
    tx = make_transaction(account_id=acc_id)
    assert len(tx.fingerprint_hash) == 64  # SHA-256 hex length.
    assert tx.account_id == acc_id


def test_make_transaction_custom_fields():
    acc_id = str(uuid.uuid4())
    posted = datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC)
    tx = make_transaction(
        account_id=acc_id,
        posted_at=posted,
        amount=Decimal("-250.00"),
        description="Pago de servicios",
        embedded_id="BANK-TX-XYZ",
    )
    assert tx.amount == Decimal("-250.00")
    assert tx.embedded_id == "BANK-TX-XYZ"
    assert tx.posted_at == posted


def test_make_transaction_fingerprint_deterministic():
    """Two calls with identical fields produce the same fingerprint."""
    acc_id = str(uuid.uuid4())
    posted = datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC)
    kwargs = dict(account_id=acc_id, posted_at=posted, amount=Decimal("50.00"), description="Test")
    tx1 = make_transaction(**kwargs)
    tx2 = make_transaction(**kwargs)
    assert tx1.fingerprint_hash == tx2.fingerprint_hash


def test_transaction_schema_is_transaction_type():
    """TransactionSchema is a type alias for the domain Transaction."""
    acc_id = str(uuid.uuid4())
    tx = make_transaction(account_id=acc_id)
    assert isinstance(tx, TransactionSchema)

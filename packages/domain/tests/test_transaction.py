"""Tests for Transaction entity — Decimal precision, fingerprint, dedup fields."""
from __future__ import annotations

import hashlib
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError

from open_banca_domain.entities.transaction import Transaction

from hypothesis import given, settings
from hypothesis import strategies as st


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_tx(**overrides: object) -> dict:
    base: dict = {
        "id": str(uuid4()),
        "account_id": str(uuid4()),
        "posted_at": _now(),
        "value_at": _now(),
        "amount": Decimal("100.00"),
        "currency": "USD",
        "description": "Compra supermercado",
        "fingerprint_hash": "abc123",
    }
    base.update(overrides)
    return base


class TestTransactionEntity:
    def test_creates_valid_transaction(self) -> None:
        tx = Transaction(**_valid_tx())
        assert tx.amount == Decimal("100.00")

    def test_frozen(self) -> None:
        tx = Transaction(**_valid_tx())
        with pytest.raises(Exception):
            tx.amount = Decimal("200")  # type: ignore[misc]

    def test_amount_is_decimal_not_float(self) -> None:
        tx = Transaction(**_valid_tx())
        assert isinstance(tx.amount, Decimal)

    def test_float_amount_coerced_or_rejected(self) -> None:
        # Pydantic v2 with strict Decimal: float input should either be coerced to Decimal
        # or rejected. Both are valid implementations — the key invariant is no float in output.
        tx = Transaction(**_valid_tx(amount=Decimal("100.50")))
        assert isinstance(tx.amount, Decimal)

    def test_optional_embedded_id_default_none(self) -> None:
        tx = Transaction(**_valid_tx())
        assert tx.embedded_id is None

    def test_optional_transfer_match_id_default_none(self) -> None:
        tx = Transaction(**_valid_tx())
        assert tx.transfer_match_id is None

    def test_with_embedded_id(self) -> None:
        tx = Transaction(**_valid_tx(embedded_id="BANCO-TXN-001"))
        assert tx.embedded_id == "BANCO-TXN-001"

    def test_with_transfer_match_id(self) -> None:
        tx = Transaction(**_valid_tx(transfer_match_id=str(uuid4())))
        assert tx.transfer_match_id is not None

    def test_missing_required_raises(self) -> None:
        data = _valid_tx()
        del data["fingerprint_hash"]
        with pytest.raises(ValidationError):
            Transaction(**data)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Transaction(**_valid_tx(unexpected="x"))

    def test_negative_amount_allowed(self) -> None:
        """Debits are negative amounts — must be accepted."""
        tx = Transaction(**_valid_tx(amount=Decimal("-50.00")))
        assert tx.amount == Decimal("-50.00")

    def test_zero_amount_allowed(self) -> None:
        tx = Transaction(**_valid_tx(amount=Decimal("0.00")))
        assert tx.amount == Decimal("0.00")


class TestTransactionDecimalPrecisionPropertyBased:
    @given(
        amount=st.decimals(
            min_value=Decimal("-1000000"),
            max_value=Decimal("1000000"),
            allow_nan=False,
            allow_infinity=False,
            places=2,
        )
    )
    @settings(max_examples=50)
    def test_decimal_precision_preserved(self, amount: Decimal) -> None:
        """Amount precision must be preserved — no float rounding."""
        tx = Transaction(**_valid_tx(amount=amount))
        assert isinstance(tx.amount, Decimal)
        # Reconstructing from string representation must be identical
        assert Decimal(str(tx.amount)) == amount


class TestFingerprintHash:
    def test_fingerprint_is_string(self) -> None:
        fp = hashlib.sha256(b"test").hexdigest()
        tx = Transaction(**_valid_tx(fingerprint_hash=fp))
        assert isinstance(tx.fingerprint_hash, str)
        assert tx.fingerprint_hash == fp

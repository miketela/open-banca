"""Tests for Account discriminated union — savings, checking, credit_card."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from open_banca_domain.entities.account import (
    AccountUnion,
    CheckingAccount,
    CreditCardAccount,
    SavingsAccount,
)


def _savings(**overrides: object) -> dict:
    base: dict = {
        "id": str(uuid4()),
        "bank_account_id": "001-123456-7",
        "account_type": "savings",
        "balance": Decimal("1234.56"),
        "currency": "USD",
        "opened_at": date(2020, 1, 1),
    }
    base.update(overrides)
    return base


def _checking(**overrides: object) -> dict:
    base = _savings(**overrides)
    base["account_type"] = "checking"
    return base


def _credit_card(**overrides: object) -> dict:
    base: dict = {
        "id": str(uuid4()),
        "bank_account_id": "CC-9876",
        "account_type": "credit_card",
        "balance": Decimal("500.00"),
        "currency": "USD",
        "opened_at": date(2021, 6, 1),
        "credit_limit": Decimal("5000.00"),
        "available_credit": Decimal("4500.00"),
        "cut_date": date(2024, 12, 20),
        "min_payment": Decimal("50.00"),
        "payment_due_date": date(2024, 12, 31),
        "statement_balance": Decimal("500.00"),
    }
    base.update(overrides)
    return base


class TestSavingsAccount:
    def test_creates_savings(self) -> None:
        acc = SavingsAccount(**_savings())
        assert acc.account_type == "savings"

    def test_frozen(self) -> None:
        acc = SavingsAccount(**_savings())
        with pytest.raises(Exception):
            acc.balance = Decimal("0")  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SavingsAccount(**_savings(unknown="x"))

    def test_no_credit_fields(self) -> None:
        acc = SavingsAccount(**_savings())
        assert not hasattr(acc, "credit_limit")


class TestCheckingAccount:
    def test_creates_checking(self) -> None:
        acc = CheckingAccount(**_checking())
        assert acc.account_type == "checking"


class TestCreditCardAccount:
    def test_creates_credit_card(self) -> None:
        cc = CreditCardAccount(**_credit_card())
        assert cc.account_type == "credit_card"
        assert cc.credit_limit == Decimal("5000.00")
        assert cc.available_credit == Decimal("4500.00")

    def test_frozen(self) -> None:
        cc = CreditCardAccount(**_credit_card())
        with pytest.raises(Exception):
            cc.credit_limit = Decimal("9000")  # type: ignore[misc]

    def test_missing_credit_limit_raises(self) -> None:
        data = _credit_card()
        del data["credit_limit"]
        with pytest.raises(ValidationError):
            CreditCardAccount(**data)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CreditCardAccount(**_credit_card(bad_field="x"))


class TestAccountUnion:
    def test_discriminator_routes_savings(self) -> None:
        adapter: TypeAdapter[AccountUnion] = TypeAdapter(AccountUnion)
        acc = adapter.validate_python(_savings())
        assert isinstance(acc, SavingsAccount)

    def test_discriminator_routes_checking(self) -> None:
        adapter: TypeAdapter[AccountUnion] = TypeAdapter(AccountUnion)
        acc = adapter.validate_python(_checking())
        assert isinstance(acc, CheckingAccount)

    def test_discriminator_routes_credit_card(self) -> None:
        adapter: TypeAdapter[AccountUnion] = TypeAdapter(AccountUnion)
        acc = adapter.validate_python(_credit_card())
        assert isinstance(acc, CreditCardAccount)

    def test_invalid_type_raises(self) -> None:
        adapter: TypeAdapter[AccountUnion] = TypeAdapter(AccountUnion)
        with pytest.raises(ValidationError):
            adapter.validate_python({**_savings(), "account_type": "loan"})


class TestAccountDiscriminatorPropertyBased:
    @given(account_type=st.sampled_from(["savings", "checking"]))
    @settings(max_examples=20)
    def test_base_accounts_accept_decimal_balance(self, account_type: str) -> None:
        adapter: TypeAdapter[AccountUnion] = TypeAdapter(AccountUnion)
        data = _savings(account_type=account_type, balance=Decimal("999.99"))
        acc = adapter.validate_python(data)
        assert acc.balance == Decimal("999.99")

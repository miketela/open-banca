"""Account entities with discriminated union per ADR-0012."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _AccountBase(BaseModel):
    """Shared fields for all account types."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    bank_account_id: str
    balance: Decimal
    currency: str
    opened_at: date


class SavingsAccount(_AccountBase):
    account_type: Literal["savings"] = "savings"


class CheckingAccount(_AccountBase):
    account_type: Literal["checking"] = "checking"


class CreditCardAccount(_AccountBase):
    """Credit card account with extended billing fields."""

    account_type: Literal["credit_card"] = "credit_card"
    credit_limit: Decimal
    available_credit: Decimal
    cut_date: date
    min_payment: Decimal
    payment_due_date: date
    statement_balance: Decimal


# Canonical discriminated union per ADR-0012.
AccountUnion = Annotated[
    SavingsAccount | CheckingAccount | CreditCardAccount,
    Field(discriminator="account_type"),
]

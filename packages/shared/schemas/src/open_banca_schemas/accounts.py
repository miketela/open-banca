"""Account schemas — re-exports + factory helpers.

Re-exports the canonical ``AccountUnion`` discriminated union from
``open_banca_domain`` along with concrete subtypes and convenience
factory functions for building test fixtures and use-case layer code.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import Field

from open_banca_domain.entities.account import (
    AccountUnion,
    CheckingAccount,
    CreditCardAccount,
    SavingsAccount,
)

# Public re-exports (aliased for cross-package clarity).
SavingsAccountSchema = SavingsAccount
CheckingAccountSchema = CheckingAccount
CreditCardAccountSchema = CreditCardAccount

# Canonical discriminated union (same definition as domain, re-exported).
AccountUnionSchema = Annotated[
    SavingsAccount | CheckingAccount | CreditCardAccount,
    Field(discriminator="account_type"),
]

# Keep the direct domain alias available for isinstance checks.
__all__ = [
    "AccountUnion",
    "AccountUnionSchema",
    "CheckingAccount",
    "CheckingAccountSchema",
    "CreditCardAccount",
    "CreditCardAccountSchema",
    "SavingsAccount",
    "SavingsAccountSchema",
    "make_checking_account",
    "make_credit_card_account",
    "make_savings_account",
]


# ── Factory helpers ───────────────────────────────────────────────────────────


def make_savings_account(
    *,
    id: str | None = None,
    bank_account_id: str = "banco_general:savings:001",
    balance: Decimal = Decimal("0.00"),
    currency: str = "USD",
    opened_at: date | None = None,
) -> SavingsAccount:
    """Construct a ``SavingsAccount`` with sensible defaults."""
    return SavingsAccount(
        id=id or str(uuid.uuid4()),
        bank_account_id=bank_account_id,
        balance=balance,
        currency=currency,
        opened_at=opened_at or date(2020, 1, 1),
    )


def make_checking_account(
    *,
    id: str | None = None,
    bank_account_id: str = "banco_general:checking:001",
    balance: Decimal = Decimal("0.00"),
    currency: str = "USD",
    opened_at: date | None = None,
) -> CheckingAccount:
    """Construct a ``CheckingAccount`` with sensible defaults."""
    return CheckingAccount(
        id=id or str(uuid.uuid4()),
        bank_account_id=bank_account_id,
        balance=balance,
        currency=currency,
        opened_at=opened_at or date(2020, 1, 1),
    )


def make_credit_card_account(
    *,
    id: str | None = None,
    bank_account_id: str = "banco_general:credit_card:001",
    balance: Decimal = Decimal("0.00"),
    currency: str = "USD",
    opened_at: date | None = None,
    credit_limit: Decimal = Decimal("5000.00"),
    available_credit: Decimal = Decimal("5000.00"),
    cut_date: date | None = None,
    min_payment: Decimal = Decimal("0.00"),
    payment_due_date: date | None = None,
    statement_balance: Decimal = Decimal("0.00"),
) -> CreditCardAccount:
    """Construct a ``CreditCardAccount`` with sensible defaults."""
    today = date.today()
    return CreditCardAccount(
        id=id or str(uuid.uuid4()),
        bank_account_id=bank_account_id,
        balance=balance,
        currency=currency,
        opened_at=opened_at or date(2020, 1, 1),
        credit_limit=credit_limit,
        available_credit=available_credit,
        cut_date=cut_date or today,
        min_payment=min_payment,
        payment_due_date=payment_due_date or today,
        statement_balance=statement_balance,
    )

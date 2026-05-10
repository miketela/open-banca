"""open-banca shared Pydantic schemas: cross-package request/response models and events.

Re-exports canonical domain types for use by packages that should not
import directly from ``open_banca_domain`` (e.g. the API layer).

Factory helpers are provided for constructing common entities in tests
and use-case layer without repeating boilerplate.
"""

from __future__ import annotations

from open_banca_schemas.accounts import (
    AccountUnionSchema,
    CheckingAccountSchema,
    CreditCardAccountSchema,
    SavingsAccountSchema,
    make_checking_account,
    make_credit_card_account,
    make_savings_account,
)
from open_banca_schemas.transactions import (
    TransactionSchema,
    make_transaction,
)

__all__ = [
    "AccountUnionSchema",
    "CheckingAccountSchema",
    "CreditCardAccountSchema",
    "SavingsAccountSchema",
    "TransactionSchema",
    "make_checking_account",
    "make_credit_card_account",
    "make_savings_account",
    "make_transaction",
]

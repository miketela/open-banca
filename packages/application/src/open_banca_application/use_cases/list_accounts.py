"""ListAccounts use case — query all accounts for a bank."""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from open_banca_domain.entities.account import AccountUnion
from open_banca_domain.ports.job_store_port import JobStorePort


class ListAccountsInput(BaseModel):
    bank: str


@dataclass
class ListAccountsOutput:
    accounts: list[AccountUnion] = field(default_factory=list)


class ListAccounts:
    """Returns all known accounts for a given bank."""

    def __init__(self, job_store: JobStorePort) -> None:
        self._job_store = job_store

    def execute(self, input: ListAccountsInput) -> ListAccountsOutput:
        accounts = self._job_store.list_accounts_by_bank(input.bank)  # type: ignore[attr-defined]
        return ListAccountsOutput(accounts=accounts)

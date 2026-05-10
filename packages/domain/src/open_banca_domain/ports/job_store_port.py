"""JobStorePort — persistence for jobs, accounts, transactions, cursors."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from open_banca_domain.entities.account import AccountUnion
from open_banca_domain.entities.job import Job
from open_banca_domain.entities.transaction import Transaction


@runtime_checkable
class JobStorePort(Protocol):
    """CRUD operations for the operational data store."""

    def save_job(self, job: Job) -> None: ...

    def load_job(self, job_id: str) -> Job | None: ...

    def list_jobs(self) -> list[Job]: ...

    def save_account(self, account: AccountUnion) -> None: ...

    def save_transaction(self, tx: Transaction) -> None: ...

    def get_cursor(self, bank: str) -> str | None: ...

    def save_cursor(self, bank: str, cursor: str) -> None: ...

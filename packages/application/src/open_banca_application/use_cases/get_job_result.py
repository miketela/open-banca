"""GetJobResult use case — load a completed job with its transactions."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from open_banca_domain.entities.account import AccountUnion
from open_banca_domain.entities.job import Job
from open_banca_domain.entities.transaction import Transaction
from open_banca_domain.ports.job_store_port import JobStorePort


class GetJobResultInput(BaseModel):
    job_id: str


@dataclass
class GetJobResultOutput:
    job: Job
    transactions: list[Transaction] = field(default_factory=list)
    accounts: list[AccountUnion] = field(default_factory=list)


class GetJobResult:
    """Loads job metadata plus all associated transactions and accounts."""

    def __init__(self, job_store: JobStorePort) -> None:
        self._job_store = job_store

    def execute(self, input: GetJobResultInput) -> GetJobResultOutput:
        job = self._job_store.load_job(input.job_id)
        if job is None:
            raise ValueError(f"Job not found: {input.job_id}")

        transactions = self._job_store.list_transactions_by_job(input.job_id)  # type: ignore[attr-defined]
        return GetJobResultOutput(job=job, transactions=transactions)

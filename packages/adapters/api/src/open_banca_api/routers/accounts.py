"""GET /accounts — list known accounts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import get_job_store
from open_banca_application.use_cases.list_accounts import ListAccounts, ListAccountsInput

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(verify_bearer)],
)


@router.get(
    "",
    summary="List known accounts",
    description="Returns all accounts cached after successful scrapes, optionally filtered by bank.",
)
async def list_accounts(
    bank: str | None = None,
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
) -> list[dict[str, Any]]:
    """GET /accounts — list all known accounts from storage."""
    if bank is not None:
        uc = ListAccounts(job_store=job_store)  # type: ignore[arg-type]
        result = uc.execute(ListAccountsInput(bank=bank))
        accounts = result.accounts
    else:
        # When no bank filter, query all accounts directly from storage
        accounts = job_store.list_accounts_by_bank("")  # type: ignore[attr-defined]
        # If list_accounts_by_bank("") returns nothing, fallback to list all via SQL
        # This is a best-effort; proper "list all" method can be added in a future task
        if not accounts:
            # Try to get all jobs and collect their banks
            all_jobs = job_store.list_jobs()  # type: ignore[attr-defined]
            seen_banks: set[str] = {j.bank for j in all_jobs}
            all_accounts: list[Any] = []
            for bank_id in seen_banks:
                all_accounts.extend(job_store.list_accounts_by_bank(bank_id))  # type: ignore[attr-defined]
            accounts = all_accounts

    return [acc.model_dump() for acc in accounts]

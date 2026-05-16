"""ListAccountsActivity — discover accounts from map.json or bank dashboard.

Reads the ``accounts`` or ``entry_points`` field from the bank's map.json.
If no static list exists, returns a fallback default account.

Retry policy: max 2 attempts.
Start-to-close timeout: 30s.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field
from temporalio import activity

logger = logging.getLogger(__name__)

_BANKS_DIR = Path(__file__).resolve().parents[5] / "banks"


class AccountInfo(BaseModel):
    """Discovered account metadata."""

    account_id: str = Field(description="Bank-side account identifier")
    account_type: str = Field(default="savings", description="savings | checking | credit_card")
    label: str = Field(default="", description="Human-readable label")


class ListAccountsInput(BaseModel):
    """Input for ListAccountsActivity."""

    bank_id: str = Field(description="Bank identifier (e.g. 'banco_general')")
    browser_session_token: str | None = Field(
        default=None,
        description="Optional session token for dynamic discovery",
    )


class ListAccountsResult(BaseModel):
    """Result from ListAccountsActivity."""

    accounts: list[AccountInfo] = Field(default_factory=list)


class ListAccountsActivity:
    """Class-based wrapper (no-op; activity is a module-level function)."""


@activity.defn(name="ListAccountsActivity")
async def list_accounts(input: ListAccountsInput) -> ListAccountsResult:  # noqa: A002
    """Discover available accounts for a bank from map.json.

    Resolution order:
      1. Read map.json → extract 'accounts' or 'entry_points' field.
      2. If map.json not found or field missing → return single default account.
    """
    activity.logger.info("listing accounts: bank_id=%s", input.bank_id)

    map_path = _BANKS_DIR / input.bank_id / "map.json"

    if map_path.exists():
        try:
            data = json.loads(map_path.read_text(encoding="utf-8"))
            accounts = _extract_accounts(data, input.bank_id)
            if accounts:
                activity.logger.info(
                    "found %d accounts in map.json for bank_id=%s",
                    len(accounts), input.bank_id,
                )
                return ListAccountsResult(accounts=accounts)
        except Exception as exc:
            activity.logger.warning(
                "failed to parse map.json for bank_id=%s: %s", input.bank_id, exc
            )

    activity.logger.info(
        "no static account list for bank_id=%s — using default", input.bank_id
    )
    return ListAccountsResult(
        accounts=[AccountInfo(account_id="default-account", label="Default")]
    )


def _extract_accounts(data: dict, bank_id: str) -> list[AccountInfo]:
    """Extract account list from map.json data."""
    accounts: list[AccountInfo] = []

    for key in ("accounts", "entry_points"):
        entries = data.get(key)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    aid = entry.get("account_id") or entry.get("id") or entry.get("name", "")
                    if aid:
                        accounts.append(
                            AccountInfo(
                                account_id=str(aid),
                                account_type=entry.get("account_type", "savings"),
                                label=entry.get("label", ""),
                            )
                        )
                elif isinstance(entry, str):
                    accounts.append(AccountInfo(account_id=entry))
            if accounts:
                return accounts

    return accounts

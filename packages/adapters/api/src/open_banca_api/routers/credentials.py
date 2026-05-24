"""POST /credentials — store encrypted bank credentials in SecretVault."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import get_secret_vault
from open_banca_api.schemas.credentials import (
    CredentialStoreRequest,
    CredentialStoreResponse,
    StoredCredentialItem,
)

logger = logging.getLogger(__name__)

_BANKS_WITH_PIN: frozenset[str] = frozenset({"banco_general", "banistmo", "bac"})

router = APIRouter(
    prefix="",
    tags=["credentials"],
    dependencies=[Depends(verify_bearer)],
)


@router.post(
    "/credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=CredentialStoreResponse,
    summary="Store encrypted bank credentials",
    description=(
        "Encrypts username/password (and optional PIN) via SecretVault.store_credential. "
        "Returns credential_ref for use in POST /scrape. "
        "Alternative: `uv run open-banca register-credentials --bank <bank_id>`."
    ),
)
def store_credentials(
    body: CredentialStoreRequest,
    vault: Annotated[object, Depends(get_secret_vault)],
) -> CredentialStoreResponse:
    """POST /credentials — wrap SecretVault.store_credential for each field."""
    effective_label = body.label or body.bank_id

    fields: list[tuple[str, str]] = [
        (f"{effective_label}:username", body.username),
        (f"{effective_label}:password", body.password),
    ]
    if body.pin:
        fields.append((f"{effective_label}:pin", body.pin))
    elif body.bank_id.lower() in _BANKS_WITH_PIN:
        logger.info(
            "No PIN provided for bank=%s; scrape may prompt for OTP/PIN at runtime.",
            body.bank_id,
        )

    stored: list[StoredCredentialItem] = []
    for field_label, plaintext in fields:
        cred = vault.store_credential(  # type: ignore[attr-defined]
            bank=body.bank_id,
            plaintext=plaintext,
            label=field_label,
        )
        stored.append(StoredCredentialItem(id=cred.id, label=cred.label))

    logger.info(
        "Stored %d credential(s) for bank=%s label=%s",
        len(stored),
        body.bank_id,
        effective_label,
    )

    return CredentialStoreResponse(
        bank_id=body.bank_id,
        credential_ref=effective_label,
        stored=stored,
    )

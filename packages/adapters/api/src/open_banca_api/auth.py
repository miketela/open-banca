"""Bearer token authentication dependency."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from open_banca_api.config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


def verify_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Raise 401 when token is missing or incorrect."""
    if credentials is None or credentials.credentials != settings.open_banca_api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

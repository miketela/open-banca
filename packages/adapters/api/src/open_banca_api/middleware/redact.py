"""Redact middleware — removes SECRET_CANARY_VALUE and PII_CANARY_* from response bodies.

Security requirement: REQ-021 / CLAUDE.md §Security Critical Rules #1.
Canary CI test must verify that these values never leak through the API surface.
"""
from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from open_banca_api.config import get_settings

logger = logging.getLogger(__name__)

_REDACTED = "[REDACTED]"


class RedactMiddleware(BaseHTTPMiddleware):
    """Scan every response body for canary values and replace them with [REDACTED].

    Operates on JSON and plain-text responses. Binary / streaming responses are
    passed through unchanged (they don't contain text canary leaks).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: object) -> Response:  # type: ignore[override]
        response: Response = await call_next(request)  # type: ignore[operator]

        content_type = response.headers.get("content-type", "")
        is_text = "json" in content_type or "text" in content_type

        if not is_text:
            return response

        # Buffer the response body
        body_bytes = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body_bytes += chunk if isinstance(chunk, bytes) else chunk.encode()

        body_str = body_bytes.decode("utf-8", errors="replace")
        redacted_body = self._redact(body_str)

        if redacted_body != body_str:
            logger.warning(
                "redact_middleware: canary value detected and redacted in response to %s %s",
                request.method,
                request.url.path,
            )

        return Response(
            content=redacted_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    def _redact(self, text: str) -> str:
        """Replace all canary values in *text* with [REDACTED]."""
        settings = get_settings()
        canaries: list[str] = []

        if settings.secret_canary_value:
            canaries.append(settings.secret_canary_value)

        canaries.extend(settings.pii_canary_values())

        for canary in canaries:
            if canary and canary in text:
                text = text.replace(canary, _REDACTED)

        return text


def _safe_json_dumps(value: object) -> str:
    """Best-effort JSON serialization for log sanitization."""
    try:
        return json.dumps(value)
    except Exception:
        return str(value)

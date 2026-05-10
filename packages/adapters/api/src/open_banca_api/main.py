"""FastAPI application factory for open-banca API.

Design notes:
- OpenAPI 3.1 (FastAPI >=0.110 default).
- Bearer auth on all endpoints except /health and /time.
- slowapi rate limiting: 60/minute on POST /scrape.
- RedactMiddleware scrubs SECRET_CANARY_VALUE and PII_CANARY_* from bodies.
- NO Temporal / DB connections at boot (task #31 handles wiring).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from open_banca_api.config import get_settings
from open_banca_api.middleware.redact import RedactMiddleware
from open_banca_api.routers import accounts, banks, jobs, maps, scrape, system, webhooks


def _get_bearer_or_ip(request: Request) -> str:
    """Rate-limit key: bearer token when present, otherwise remote IP."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return get_remote_address(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — no-op for task #3 skeleton."""
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    limiter = Limiter(key_func=_get_bearer_or_ip, default_limits=["60/minute"])

    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        openapi_version="3.1.0",
        description=(
            "Self-hosted API for automated bank data capture in Panama using AI agents. "
            "AGPL-3.0."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Attach rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Security middleware — must come after exception handlers
    app.add_middleware(RedactMiddleware)

    # Routers
    app.include_router(system.router)
    app.include_router(scrape.router)
    app.include_router(jobs.router)
    app.include_router(maps.router)
    app.include_router(banks.router)
    app.include_router(accounts.router)
    app.include_router(webhooks.router)

    return app


app = create_app()

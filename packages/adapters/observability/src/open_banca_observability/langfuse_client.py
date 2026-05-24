"""Optional Langfuse client factory with OTel bridge.

Returns a live Langfuse client when ``LANGFUSE_HOST`` is set in the
environment; otherwise returns ``None`` (no-op) so callers need no
conditional guards.

Langfuse is profile-gated in ``docker-compose.yml`` and DISABLED by default.
Code that calls :func:`get_langfuse_client` must tolerate a ``None`` return.

Usage::

    from open_banca_observability.langfuse_client import get_langfuse_client

    lf = get_langfuse_client()
    if lf is not None:
        trace = lf.trace(name="scrape-job", user_id="operator")

Environment variables (all optional):

    LANGFUSE_HOST         e.g. ``http://localhost:3000`` — enables Langfuse.
    LANGFUSE_PUBLIC_KEY   Public key for authentication.
    LANGFUSE_SECRET_KEY   Secret key for authentication.
    LANGFUSE_RELEASE      Release tag attached to all traces (optional).

Security (ADR-0020): the Langfuse SDK is configured with an OTel-compatible
handler so that ``RedactFilter`` scrubs every payload before it is serialised
and sent to the Langfuse server.  No canary or PII values should appear in
Langfuse traces.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_langfuse_client() -> Any | None:
    """Build and return a Langfuse client, or ``None`` if Langfuse is disabled.

    Importing this function never raises even when the ``langfuse`` package
    is not installed — in that case the function returns ``None`` and logs
    a debug message.

    Returns:
        A ``langfuse.Langfuse`` instance, or ``None`` when ``LANGFUSE_HOST``
        is not set or the ``langfuse`` package is unavailable.
    """
    host = os.environ.get("LANGFUSE_HOST", "").strip()
    if not host:
        logger.debug("LANGFUSE_HOST not set — Langfuse disabled (noop)")
        return None

    try:
        import langfuse  # type: ignore[import-untyped]
    except ImportError:
        logger.debug(
            "langfuse package not installed — Langfuse disabled. "
            "Install with: uv add 'open-banca-observability[langfuse]'"
        )
        return None

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    release = os.environ.get("LANGFUSE_RELEASE", None)

    client = langfuse.Langfuse(
        host=host,
        public_key=public_key,
        secret_key=secret_key,
        release=release,
        # Flush synchronously during graceful shutdown.
        flush_at=15,
        flush_interval=0.5,
    )

    logger.info("Langfuse client initialised", extra={"langfuse_host": host})
    return client


def flush_langfuse(client: Any | None) -> None:
    """Flush any buffered Langfuse events; safe to call with ``None``.

    Args:
        client: ``langfuse.Langfuse`` instance returned by :func:`get_langfuse_client`,
                or ``None``.
    """
    if client is not None:
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Langfuse flush failed: %s", exc)

"""Worker entry point: python -m open_banca_webhooks.worker

Polls the outbox every POLL_INTERVAL_SECONDS and delivers pending webhooks.
Configuration via environment variables:
  OPEN_BANCA_WEBHOOK_TARGET_URL   - Required: delivery endpoint
  OPEN_BANCA_WEBHOOK_SECRET       - Required: HMAC signing secret
  OPEN_BANCA_WEBHOOK_DB_PATH      - Optional: SQLite DB path (default: ./webhook_outbox.db)
  OPEN_BANCA_WEBHOOK_POLL_INTERVAL - Optional: poll interval seconds (default: 5)
  OPEN_BANCA_WEBHOOK_HTTP_TIMEOUT  - Optional: HTTP timeout seconds (default: 10)
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from types import FrameType

from open_banca_webhooks.dispatcher import WebhookDispatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

_stop_event = threading.Event()


def _handle_signal(sig: int, frame: FrameType | None) -> None:
    logger.info("webhook_worker: received signal %d, stopping...", sig)
    _stop_event.set()


def run() -> None:
    """Run the webhook delivery worker loop."""
    target_url = os.environ.get("OPEN_BANCA_WEBHOOK_TARGET_URL", "")
    secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "")
    db_path = os.environ.get("OPEN_BANCA_WEBHOOK_DB_PATH", "./webhook_outbox.db")
    poll_interval = float(os.environ.get("OPEN_BANCA_WEBHOOK_POLL_INTERVAL", "5"))
    http_timeout = float(os.environ.get("OPEN_BANCA_WEBHOOK_HTTP_TIMEOUT", "10"))

    if not target_url:
        raise ValueError("OPEN_BANCA_WEBHOOK_TARGET_URL is required")
    if not secret:
        raise ValueError("OPEN_BANCA_WEBHOOK_SECRET is required")

    dispatcher = WebhookDispatcher(
        target_url=target_url,
        secret=secret,
        db_path=db_path,
        http_timeout=http_timeout,
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        "webhook_worker: started, target=%s, poll_interval=%ss, db=%s",
        target_url,
        poll_interval,
        db_path,
    )

    while not _stop_event.is_set():
        try:
            count = dispatcher.deliver_pending()
            if count:
                logger.info("webhook_worker: delivered %d webhooks", count)
        except Exception:
            logger.exception("webhook_worker: unexpected error in delivery loop")
        _stop_event.wait(timeout=poll_interval)

    logger.info("webhook_worker: stopped")


if __name__ == "__main__":
    run()

"""ExecuteScrapeMapActivity — run ScraperRunner against map.json (MVP, no sidecar).

Single activity replaces Login/Navigate/Download skeleton chain. Human-input pauses
poll SQLCipher via ``human_input_answers`` (POST /jobs/{id}/human-input writes the row).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

logger = logging.getLogger(__name__)

_BANKS_DIR = Path(__file__).resolve().parents[5] / "banks"


class ExecuteScrapeMapInput(BaseModel):
    """Input for ExecuteScrapeMapActivity."""

    job_id: str
    bank_id: str
    credential_ref: str
    sandbox_container_id: str | None = Field(
        default=None,
        description="Reserved for future sandbox browser routing",
    )


class ExecuteScrapeMapResult(BaseModel):
    """Result from ExecuteScrapeMapActivity."""

    status: str = Field(description="completed | failed | human_input_timeout | breakage")
    excel_path: str | None = None
    content_hash: str | None = None
    file_size_bytes: int = 0
    errors: list[str] = Field(default_factory=list)
    steps_completed: int = 0
    breakage_count: int = 0


def _load_bank_map(bank_id: str):
    from open_banca_domain.entities.bank_map import BankMap  # noqa: PLC0415

    map_path = _BANKS_DIR / bank_id / "map.json"
    if not map_path.exists():
        raise ApplicationError(
            f"map.json not found for bank_id={bank_id!r}",
            non_retryable=True,
            type="map_not_found",
        )
    data = json.loads(map_path.read_text(encoding="utf-8"))
    return BankMap.model_validate(data)


def _open_storage():
    from open_banca_storage import ConnectionPool, SecretVault, migrate  # noqa: PLC0415
    from open_banca_storage.config import get_settings as get_storage_settings  # noqa: PLC0415
    from open_banca_storage.repositories.job_store import SqliteJobStore  # noqa: PLC0415

    # Must match API default in open_banca_api.dependencies.get_storage_connection
    passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "dev-insecure-passphrase")
    settings = get_storage_settings()
    db_path = Path(
        os.environ.get("OPEN_BANCA_DB_PATH", str(settings.open_banca_db_path))
    )
    # Mirror API KDF selection: Argon2id when available, Passthrough otherwise.
    try:
        from open_banca_storage.kdf import Argon2idKeyDerivation  # noqa: PLC0415
        kdf = Argon2idKeyDerivation()
    except Exception:
        from open_banca_storage.connection import PassthroughKeyDerivation  # noqa: PLC0415
        kdf = PassthroughKeyDerivation()  # type: ignore[assignment]
    pool = ConnectionPool(
        db_path=db_path,
        passphrase=passphrase,
        key_derivation=kdf,
    )
    conn = pool.get()
    migrate(conn)
    vault = SecretVault(pool=pool, master_passphrase=passphrase)
    return pool, conn, SqliteJobStore(conn), vault


def _resolve_credential_id(vault_conn, credential_ref: str) -> str:
    row = vault_conn.execute(
        "SELECT id FROM credentials WHERE label = ? ORDER BY created_at DESC LIMIT 1",
        (f"{credential_ref}:username",),
    ).fetchone()
    if row is None:
        row = vault_conn.execute(
            "SELECT id FROM credentials WHERE credential_ref = ? LIMIT 1",
            (credential_ref,),
        ).fetchone()
    if row is None:
        raise KeyError(f"No credential id for ref={credential_ref!r}")
    return str(row[0])


def _make_secret_resolver(vault, credential_ref: str):
    placeholders = {
        "<USERNAME>": f"{credential_ref}:username",
        "<PASSWORD>": f"{credential_ref}:password",
    }

    def resolve(value_ref: str) -> str:
        label = placeholders.get(value_ref, value_ref)
        return vault.fetch_credential(label)

    return resolve


def _emit_human_input_webhook(
    job_id: str,
    field_key: str,
    question_text: str,
    question_hash: str,
    timeout_s: int,
) -> None:
    from open_banca_orchestrator.activities.human_input_await import (  # noqa: PLC0415
        _emit_human_input_required_webhook,
    )

    expires_at = datetime.now(tz=UTC).timestamp() + timeout_s
    _emit_human_input_required_webhook(
        job_id=job_id,
        field_key=field_key,
        question_text=question_text,
        question_hash=question_hash,
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
    )


def _run_scraper_sync(inp: ExecuteScrapeMapInput) -> ExecuteScrapeMapResult:
    from open_banca_browser.errors import HumanInputRequired  # noqa: PLC0415
    from open_banca_browser.human_input_waiter import (  # noqa: PLC0415
        HumanInputTimeoutError,
        PollingHumanInputWaiter,
    )
    from open_banca_browser.runner import ScraperRunner  # noqa: PLC0415
    from open_banca_domain.entities.credential import Credential  # noqa: PLC0415
    from open_banca_domain.entities.job import JobStatus  # noqa: PLC0415

    pool, conn, job_store, vault = _open_storage()
    try:
        job = job_store.load_job(inp.job_id)
        if job is not None:
            job_store.update_job_status(inp.job_id, JobStatus.RUNNING)

        bank_map = _load_bank_map(inp.bank_id)
        credential_id = _resolve_credential_id(conn, inp.credential_ref)
        credential = Credential(
            id=credential_id,
            bank=inp.bank_id,
            credential_ref=inp.credential_ref,
            label=f"{inp.credential_ref}:username",
        )

        webhook_emitted: set[str] = set()

        def on_human_input_required(required: HumanInputRequired) -> None:
            job_store.save_human_input_pending(
                inp.job_id,
                required.field_key,
                required.question_hash,
                question_text=required.question_text,
            )
            job_store.update_job_status(inp.job_id, JobStatus.HUMAN_INPUT_REQUIRED)
            if required.field_key not in webhook_emitted:
                _emit_human_input_webhook(
                    inp.job_id,
                    required.field_key,
                    required.question_text,
                    required.question_hash,
                    required.timeout_s,
                )
                webhook_emitted.add(required.field_key)

        waiter = PollingHumanInputWaiter(
            job_id=inp.job_id,
            poll_port=job_store,
            heartbeat_fn=lambda: activity.heartbeat(
                {"waiting_for": "human_input", "job_id": inp.job_id}
            ),
            on_required=on_human_input_required,
        )

        headless = os.environ.get("OPEN_BANCA_PLAYWRIGHT_HEADLESS", "1").strip() != "0"
        runner = ScraperRunner(
            job_id_provider=lambda: inp.job_id,
            secret_resolver=_make_secret_resolver(vault, inp.credential_ref),
            human_input_waiter=waiter,
            vault=vault,
            credential_id=credential_id,
            heartbeat_fn=lambda: activity.heartbeat(
                {"job_id": inp.job_id, "phase": "execute_map"}
            ),
            headless=headless,
        )

        scrape = runner.execute_map(bank_map, credential)
    except HumanInputTimeoutError as exc:
        job_store.update_job_status(inp.job_id, JobStatus.FAILED, error=str(exc))
        return ExecuteScrapeMapResult(
            status="human_input_timeout",
            errors=[str(exc)],
        )
    except Exception as exc:
        logger.exception("execute_scrape_map failed job_id=%s", inp.job_id)
        job_store.update_job_status(inp.job_id, JobStatus.FAILED, error=str(exc))
        return ExecuteScrapeMapResult(status="failed", errors=[str(exc)])
    finally:
        vault.close()

    if scrape.breakage_events:
        job_store.update_job_status(
            inp.job_id,
            JobStatus.FAILED,
            error=scrape.breakage_events[0].error_class,
        )
        return ExecuteScrapeMapResult(
            status="breakage",
            errors=[e.error_class for e in scrape.breakage_events],
            steps_completed=int(scrape.metadata.get("steps_completed", 0)),
            breakage_count=len(scrape.breakage_events),
        )

    excel_path: str | None = None
    content_hash: str | None = None
    file_size = len(scrape.raw_data)
    if scrape.raw_data:
        suffix = ".xlsx" if scrape.raw_data[:2] == b"PK" else ".bin"
        fd, excel_path = tempfile.mkstemp(
            prefix=f"open-banca-{inp.job_id}-", suffix=suffix
        )
        os.close(fd)
        Path(excel_path).write_bytes(scrape.raw_data)
        import hashlib

        content_hash = hashlib.sha256(scrape.raw_data).hexdigest()

    job_store.update_job_status(inp.job_id, JobStatus.RUNNING)
    return ExecuteScrapeMapResult(
        status="completed",
        excel_path=excel_path,
        content_hash=content_hash,
        file_size_bytes=file_size,
        steps_completed=int(scrape.metadata.get("steps_completed", len(bank_map.steps))),
    )


@activity.defn(name="ExecuteScrapeMapActivity")
async def execute_scrape_map(input: ExecuteScrapeMapInput) -> ExecuteScrapeMapResult:  # noqa: A002
    """Execute the bank map via ScraperRunner (Playwright, 0 LLM)."""
    activity.logger.info(
        "execute_scrape_map: job_id=%s bank_id=%s", input.job_id, input.bank_id
    )
    return await asyncio.to_thread(_run_scraper_sync, input)

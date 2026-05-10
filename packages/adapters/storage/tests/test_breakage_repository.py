"""Tests for BreakageRepository — persistence and webhook outbox enqueue.

TDD coverage (task-10):
- test_breakage_persistence: emit → audit_log row with action='breakage.emitted'
  and metadata_json containing the breakage event fields.
- test_webhook_outbox_enqueue: emit → webhook_outbox row with event_type='job.failed'
  and payload containing job_id.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate
from open_banca_storage.repositories.breakage_repository import BreakageRepository


@pytest.fixture()
def conn_with_schema(tmp_path):  # type: ignore[no-untyped-def]
    """Provide a migrated in-process SQLCipher connection."""
    db_file = tmp_path / "breakage_test.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="test-key",
        key_derivation=PassthroughKeyDerivation(),
    )
    conn = pool.get()
    migrate(conn)
    yield conn
    pool.close()


@pytest.fixture()
def sample_event() -> BreakageEvent:
    """A minimal BreakageEvent for testing."""
    return BreakageEvent(
        job_id="job-breakage-001",
        step_index=3,
        step_type="click",
        error_class="step_timeout",
        screenshot_ref="sha256:abc123",
        dom_excerpt="<body><button>Submit</button></body>",
        occurred_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
        http_status=None,
    )


@pytest.fixture()
def repo(conn_with_schema) -> BreakageRepository:  # type: ignore[no-untyped-def]
    """BreakageRepository bound to the test connection."""
    return BreakageRepository(conn_with_schema)


def test_breakage_persistence(conn_with_schema, repo, sample_event) -> None:  # type: ignore[no-untyped-def]
    """After notify(), audit_log must contain a row with action='breakage.emitted'."""
    # We need a jobs row first (FK constraint on webhook_outbox.job_id).
    _insert_stub_job(conn_with_schema, sample_event.job_id)

    repo.notify(event=sample_event, job_id=sample_event.job_id)

    rows = conn_with_schema.execute(
        "SELECT actor, action, entity_type, entity_id, metadata_json "
        "FROM audit_log WHERE action = 'breakage.emitted'"
    ).fetchall()

    assert len(rows) == 1
    actor, action, entity_type, entity_id, metadata_json = rows[0]
    assert actor == "browser_runner"
    assert action == "breakage.emitted"
    assert entity_type == "job"
    assert entity_id == sample_event.job_id

    metadata = json.loads(metadata_json)
    assert metadata["job_id"] == sample_event.job_id
    assert metadata["step_index"] == sample_event.step_index
    assert metadata["step_type"] == sample_event.step_type
    assert metadata["error_class"] == sample_event.error_class
    assert metadata["screenshot_ref"] == sample_event.screenshot_ref


def test_webhook_outbox_enqueue(conn_with_schema, repo, sample_event) -> None:  # type: ignore[no-untyped-def]
    """After notify(), webhook_outbox must contain a job.failed row."""
    _insert_stub_job(conn_with_schema, sample_event.job_id)

    repo.notify(event=sample_event, job_id=sample_event.job_id)

    rows = conn_with_schema.execute(
        "SELECT job_id, event_type, payload_json, delivered_at "
        "FROM webhook_outbox WHERE event_type = 'job.failed'"
    ).fetchall()

    assert len(rows) == 1
    job_id, event_type, payload_json, delivered_at = rows[0]
    assert job_id == sample_event.job_id
    assert event_type == "job.failed"
    assert delivered_at is None  # not yet delivered

    payload = json.loads(payload_json)
    assert payload["job_id"] == sample_event.job_id
    assert "breakage" in payload
    assert payload["breakage"]["step_type"] == sample_event.step_type


def test_both_rows_written_atomically(conn_with_schema, repo, sample_event) -> None:  # type: ignore[no-untyped-def]
    """audit_log and webhook_outbox rows must both exist after a single notify()."""
    _insert_stub_job(conn_with_schema, sample_event.job_id)

    repo.notify(event=sample_event, job_id=sample_event.job_id)

    audit_count = conn_with_schema.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'breakage.emitted'"
    ).fetchone()[0]
    outbox_count = conn_with_schema.execute(
        "SELECT COUNT(*) FROM webhook_outbox WHERE event_type = 'job.failed'"
    ).fetchone()[0]

    assert audit_count == 1
    assert outbox_count == 1


# ── helpers ───────────────────────────────────────────────────────────────────

def _insert_stub_job(conn, job_id: str) -> None:  # type: ignore[no-untyped-def]
    """Insert a minimal jobs row to satisfy the FK constraint on webhook_outbox."""
    now = datetime.now(tz=UTC).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO jobs
            (id, status, bank, credential_ref, mode, since_cursor, error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (job_id, "running", "test_bank", "vault://test", "full", now, now),
    )
    conn.commit()

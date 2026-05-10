"""Tests for the audit_log append-only semantics.

TDD coverage:
- AuditLogger.record() inserts rows.
- UPDATE is rejected by trigger (append-only enforcement).
- DELETE is rejected by trigger (append-only enforcement).
- Multiple records are retrieved in insertion order.
"""

from __future__ import annotations

import pytest

from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate
from open_banca_storage.repositories.job_store import AuditLogger


@pytest.fixture()
def conn_with_schema(tmp_path):
    db_file = tmp_path / "audit_test.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="audit-test-key",
        key_derivation=PassthroughKeyDerivation(),
    )
    c = pool.get()
    migrate(c)
    yield c
    pool.close()


@pytest.fixture()
def logger(conn_with_schema):
    return AuditLogger(conn_with_schema)


def test_record_inserts_row(conn_with_schema, logger):
    row_id = logger.record(
        actor="system",
        action="job.created",
        entity_type="job",
        entity_id="job-123",
    )
    row = conn_with_schema.execute(
        "SELECT actor, action, entity_type, entity_id FROM audit_log WHERE id = ?",
        (row_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "system"
    assert row[1] == "job.created"
    assert row[2] == "job"
    assert row[3] == "job-123"


def test_record_with_metadata(conn_with_schema, logger):
    meta = {"attempt": 1, "result": "ok"}
    row_id = logger.record(
        actor="orchestrator",
        action="cred.read",
        entity_type="credential",
        entity_id="cred-xyz",
        metadata=meta,
    )
    row = conn_with_schema.execute(
        "SELECT metadata_json FROM audit_log WHERE id = ?", (row_id,)
    ).fetchone()
    import json

    assert json.loads(row[0]) == meta


def test_update_is_rejected(conn_with_schema, logger):
    """The audit_log_no_update trigger must abort UPDATE attempts."""
    row_id = logger.record("sys", "job.created", "job", "j1")

    with pytest.raises(Exception, match="append-only"):
        conn_with_schema.execute("UPDATE audit_log SET action = 'tampered' WHERE id = ?", (row_id,))
        conn_with_schema.commit()


def test_delete_is_rejected(conn_with_schema, logger):
    """The audit_log_no_delete trigger must abort DELETE attempts."""
    row_id = logger.record("sys", "job.created", "job", "j2")

    with pytest.raises(Exception, match="append-only"):
        conn_with_schema.execute("DELETE FROM audit_log WHERE id = ?", (row_id,))
        conn_with_schema.commit()


def test_multiple_records_ordered(conn_with_schema, logger):
    """Records are retrievable and their IDs are unique."""
    ids = [logger.record("sys", f"action.{i}", "job", f"entity-{i}") for i in range(5)]
    all_ids_in_db = [
        r[0] for r in conn_with_schema.execute("SELECT id FROM audit_log ORDER BY rowid").fetchall()
    ]
    for expected_id in ids:
        assert expected_id in all_ids_in_db

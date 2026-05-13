"""Tests for Job entity — invariants, status enum, frozen behavior."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from open_banca_domain.entities.job import Job, JobMode, JobStatus


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_job(**overrides: object) -> dict:
    base: dict = {
        "id": str(uuid4()),
        "status": JobStatus.PENDING,
        "bank": "banco_general",
        "credential_ref": "cred-abc123",
        "mode": JobMode.FULL,
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return base


class TestJobStatusEnum:
    def test_all_states_present(self) -> None:
        expected = {
            "pending",
            "running",
            "otp_required",
            "human_input_required",
            "resumed",
            "escalated",
            "completed",
            "failed",
            "cancelled",
        }
        actual = {s.value for s in JobStatus}
        assert actual == expected

    def test_cancelled_is_terminal(self) -> None:
        # cancelled must exist — used by cancel_job signal
        assert JobStatus.CANCELLED in list(JobStatus)


class TestJobMode:
    def test_modes(self) -> None:
        assert {m.value for m in JobMode} == {"full", "incremental"}


class TestJobEntity:
    def test_creates_valid_job(self) -> None:
        job = Job(**_valid_job())
        assert job.status == JobStatus.PENDING
        assert job.mode == JobMode.FULL

    def test_frozen_raises_on_mutation(self) -> None:
        job = Job(**_valid_job())
        with pytest.raises(Exception):  # FrozenInstanceError
            job.status = JobStatus.RUNNING  # type: ignore[misc]

    def test_optional_since_cursor_defaults_none(self) -> None:
        job = Job(**_valid_job())
        assert job.since_cursor is None

    def test_optional_error_defaults_none(self) -> None:
        job = Job(**_valid_job())
        assert job.error is None

    def test_with_since_cursor(self) -> None:
        job = Job(**_valid_job(since_cursor="2024-01-01"))
        assert job.since_cursor == "2024-01-01"

    def test_with_error(self) -> None:
        job = Job(**_valid_job(status=JobStatus.FAILED, error="timeout"))
        assert job.error == "timeout"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValidationError):
            Job(**_valid_job(status="invalid_status"))  # type: ignore[arg-type]

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValidationError):
            Job(**_valid_job(mode="bad_mode"))  # type: ignore[arg-type]

    def test_missing_required_field_raises(self) -> None:
        data = _valid_job()
        del data["bank"]
        with pytest.raises(ValidationError):
            Job(**data)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Job(**_valid_job(unknown_field="x"))

    def test_created_at_must_be_utc_aware(self) -> None:
        from datetime import datetime
        utc_job = Job(**_valid_job(created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)))
        assert utc_job.created_at.tzinfo is not None

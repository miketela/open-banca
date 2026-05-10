"""Tests for BankMap, Credential, BreakageEvent, RemapProposal, WebhookEvent."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_domain.entities.credential import Credential
from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus
from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# BankMap
# ---------------------------------------------------------------------------
class TestBankMap:
    def test_creates_bank_map(self) -> None:
        bm = BankMap(
            bank_id="banco_general",
            version="1.0.0",
            steps=[StepSpec(step_id="login", action="navigate", target="https://bg.com")],
            schema_version="1",
        )
        assert bm.bank_id == "banco_general"

    def test_frozen(self) -> None:
        bm = BankMap(
            bank_id="banco_general",
            version="1.0.0",
            steps=[],
            schema_version="1",
        )
        with pytest.raises(Exception):
            bm.version = "2.0.0"  # type: ignore[misc]

    def test_optional_signature_default_none(self) -> None:
        bm = BankMap(
            bank_id="bg",
            version="1.0.0",
            steps=[],
            schema_version="1",
        )
        assert bm.signature is None

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            BankMap(
                bank_id="bg",
                version="1.0.0",
                steps=[],
                schema_version="1",
                bad_field="x",  # pyrefly: ignore  # intentional: testing extra='forbid'
            )


# ---------------------------------------------------------------------------
# Credential
# ---------------------------------------------------------------------------
class TestCredential:
    def test_creates_credential(self) -> None:
        cred = Credential(
            id=str(uuid4()),
            bank="banco_general",
            credential_ref="vault:cred:abc123",
            label="Mi cuenta BG",
        )
        assert cred.bank == "banco_general"

    def test_no_plaintext_field(self) -> None:
        """Credential must not have a plaintext/password field."""
        cred = Credential(
            id=str(uuid4()),
            bank="bg",
            credential_ref="vault:x",
            label="test",
        )
        assert not hasattr(cred, "password")
        assert not hasattr(cred, "plaintext")

    def test_frozen(self) -> None:
        cred = Credential(
            id=str(uuid4()),
            bank="bg",
            credential_ref="ref",
            label="l",
        )
        with pytest.raises(Exception):
            cred.bank = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BreakageEvent
# ---------------------------------------------------------------------------
class TestBreakageEvent:
    def _valid(self, **overrides: object) -> dict:
        base: dict = {
            "job_id": str(uuid4()),
            "step_index": 2,
            "step_type": "NavigateActivity",
            "error_class": "TimeoutError",
            "screenshot_ref": "s3://screenshots/abc.png",
            "dom_excerpt": "<div>...</div>",
            "occurred_at": _now(),
        }
        base.update(overrides)
        return base

    def test_creates_breakage_event(self) -> None:
        ev = BreakageEvent(**self._valid())
        assert ev.step_type == "NavigateActivity"

    def test_optional_http_status_default_none(self) -> None:
        ev = BreakageEvent(**self._valid())
        assert ev.http_status is None

    def test_with_http_status(self) -> None:
        ev = BreakageEvent(**self._valid(http_status=503))
        assert ev.http_status == 503

    def test_frozen(self) -> None:
        ev = BreakageEvent(**self._valid())
        with pytest.raises(Exception):
            ev.error_class = "OtherError"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            BreakageEvent(**self._valid(extra="x"))


# ---------------------------------------------------------------------------
# RemapProposal
# ---------------------------------------------------------------------------
class TestRemapProposal:
    def _valid(self, **overrides: object) -> dict:
        base: dict = {
            "id": str(uuid4()),
            "bank": "banco_general",
            "breakage_id": str(uuid4()),
            "judge_decision": "remap_required",
            "confidence": 0.9,
            "risk": "low",
            "patch_diff": "--- a/map.json\n+++ b/map.json",
            "status": RemapStatus.PENDING,
            "expires_at": _now(),
        }
        base.update(overrides)
        return base

    def test_creates_proposal(self) -> None:
        p = RemapProposal(**self._valid())
        assert p.status == RemapStatus.PENDING

    def test_status_enum_exhaustive(self) -> None:
        expected = {"pending", "approved", "rejected", "applied", "expired"}
        actual = {s.value for s in RemapStatus}
        assert actual == expected

    def test_frozen(self) -> None:
        p = RemapProposal(**self._valid())
        with pytest.raises(Exception):
            p.status = RemapStatus.APPROVED  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            RemapProposal(**self._valid(bad="x"))


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------
class TestWebhookEvent:
    def _valid(self, **overrides: object) -> dict:
        base: dict = {
            "id": str(uuid4()),
            "event_type": WebhookEventType.JOB_CREATED,
            "payload": {"job_id": str(uuid4())},
            "signature": "t=123,v1=abc",
            "dispatched_at": _now(),
            "retries": 0,
        }
        base.update(overrides)
        return base

    def test_creates_webhook_event(self) -> None:
        ev = WebhookEvent(**self._valid())
        assert ev.event_type == WebhookEventType.JOB_CREATED

    def test_all_7_event_types_present(self) -> None:
        expected = {
            "job.created",
            "job.otp_required",
            "job.progress",
            "job.completed",
            "job.failed",
            "job.remap_proposed",
            "job.human_required",
        }
        actual = {e.value for e in WebhookEventType}
        assert actual == expected

    def test_optional_job_id_default_none(self) -> None:
        ev = WebhookEvent(**self._valid())
        assert ev.job_id is None

    def test_with_job_id(self) -> None:
        jid = str(uuid4())
        ev = WebhookEvent(**self._valid(job_id=jid))
        assert ev.job_id == jid

    def test_frozen(self) -> None:
        ev = WebhookEvent(**self._valid())
        with pytest.raises(Exception):
            ev.retries = 5  # type: ignore[misc]

    def test_invalid_event_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            WebhookEvent(**self._valid(event_type="job.invalid"))

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            WebhookEvent(**self._valid(unknown="x"))

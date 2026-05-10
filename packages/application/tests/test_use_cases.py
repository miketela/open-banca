"""Tests for all 6 application use cases using protocol fakes."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from open_banca_application.use_cases.apply_remap_proposal import (
    ApplyRemapInput,
    ApplyRemapProposal,
    RemapAction,
)
from open_banca_application.use_cases.confirm_otp import ConfirmOTP, ConfirmOTPInput
from open_banca_application.use_cases.get_job_result import GetJobResult, GetJobResultInput
from open_banca_application.use_cases.list_accounts import ListAccounts, ListAccountsInput
from open_banca_application.use_cases.register_credential import (
    RegisterCredential,
    RegisterCredentialInput,
)
from open_banca_application.use_cases.start_scrape_job import StartScrapeJob, StartScrapeJobInput
from open_banca_domain.entities.account import AccountUnion, SavingsAccount
from open_banca_domain.entities.credential import Credential
from open_banca_domain.entities.job import Job, JobMode, JobStatus
from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus
from open_banca_domain.entities.transaction import Transaction


def _now() -> datetime:
    return datetime.now(UTC)


def _make_job(status: JobStatus = JobStatus.PENDING) -> Job:
    return Job(
        id=str(uuid4()),
        status=status,
        bank="banco_general",
        credential_ref="cred-ref",
        mode=JobMode.FULL,
        created_at=_now(),
        updated_at=_now(),
    )


def _make_credential() -> Credential:
    return Credential(
        id=str(uuid4()),
        bank="banco_general",
        credential_ref="vault:cred:xyz",
        label="Test Cred",
    )


def _make_account() -> SavingsAccount:
    from datetime import date
    return SavingsAccount(
        id=str(uuid4()),
        bank_account_id="001-111",
        account_type="savings",
        balance=Decimal("1000.00"),
        currency="USD",
        opened_at=date(2020, 1, 1),
    )


def _make_transaction() -> Transaction:
    return Transaction(
        id=str(uuid4()),
        account_id=str(uuid4()),
        posted_at=_now(),
        value_at=_now(),
        amount=Decimal("50.00"),
        currency="USD",
        description="Test tx",
        fingerprint_hash="fp123",
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeOrchestrator:
    def __init__(self) -> None:
        self.started_jobs: list[dict] = []
        self.otp_confirmed: list[str] = []
        self.remap_approved: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        job_id = str(uuid4())
        self.started_jobs.append({"bank": bank, "credential_ref": credential_ref, "mode": mode, "job_id": job_id})
        return job_id

    def signal_otp_confirmed(self, job_id: str) -> None:
        self.otp_confirmed.append(job_id)

    def signal_remap_approved(self, proposal_id: str) -> None:
        self.remap_approved.append(("approved", proposal_id))

    def cancel_job(self, job_id: str) -> None:
        self.cancelled.append(job_id)

    def query_status(self, job_id: str) -> str:
        return "pending"


class FakeJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._accounts: list[AccountUnion] = []
        self._transactions: list[Transaction] = []
        self._cursors: dict[str, str] = {}
        self._proposals: dict[str, RemapProposal] = {}

    def save_job(self, job: Job) -> None:
        self._jobs[job.id] = job

    def load_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def save_account(self, account: AccountUnion) -> None:
        self._accounts.append(account)

    def save_transaction(self, tx: Transaction) -> None:
        self._transactions.append(tx)

    def get_cursor(self, bank: str) -> str | None:
        return self._cursors.get(bank)

    def save_cursor(self, bank: str, cursor: str) -> None:
        self._cursors[bank] = cursor

    def list_accounts_by_bank(self, bank: str) -> list[AccountUnion]:
        return list(self._accounts)

    def list_transactions_by_job(self, job_id: str) -> list[Transaction]:
        return list(self._transactions)

    def save_proposal(self, proposal: RemapProposal) -> None:
        self._proposals[proposal.id] = proposal

    def load_proposal(self, proposal_id: str) -> RemapProposal | None:
        return self._proposals.get(proposal_id)


class FakeSecretStore:
    def __init__(self) -> None:
        self._stored: list[dict] = []
        self._return_cred: Credential | None = None

    def store_credential(self, bank: str, plaintext: str, label: str) -> Credential:
        self._stored.append({"bank": bank, "label": label})
        return Credential(
            id=str(uuid4()),
            bank=bank,
            credential_ref=f"vault:cred:{uuid4()}",
            label=label,
        )

    def fetch_credential(self, credential_ref: str) -> str:
        return "decrypted-secret"

    def rotate_master(self) -> None:
        pass


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def retry_pending(self) -> None:
        pass


# ---------------------------------------------------------------------------
# StartScrapeJob
# ---------------------------------------------------------------------------
class TestStartScrapeJob:
    def setup_method(self) -> None:
        self.orchestrator = FakeOrchestrator()
        self.job_store = FakeJobStore()
        self.event_bus = FakeEventBus()
        self.use_case = StartScrapeJob(
            orchestrator=self.orchestrator,
            job_store=self.job_store,
            event_bus=self.event_bus,
        )

    def test_creates_and_returns_job_id(self) -> None:
        result = self.use_case.execute(
            StartScrapeJobInput(
                bank="banco_general",
                credential_ref="cred-ref",
                mode="full",
            )
        )
        assert result.job_id is not None
        assert len(self.orchestrator.started_jobs) == 1

    def test_emits_job_created_event(self) -> None:
        self.use_case.execute(
            StartScrapeJobInput(
                bank="banco_general",
                credential_ref="cred-ref",
                mode="full",
            )
        )
        assert len(self.event_bus.published) == 1

    def test_invalid_mode_raises(self) -> None:
        from pydantic import ValidationError as PydanticVE
        with pytest.raises((ValueError, PydanticVE)):
            StartScrapeJobInput(bank="bg", credential_ref="ref", mode="invalid_mode")


# ---------------------------------------------------------------------------
# ConfirmOTP
# ---------------------------------------------------------------------------
class TestConfirmOTP:
    def setup_method(self) -> None:
        self.orchestrator = FakeOrchestrator()
        self.use_case = ConfirmOTP(orchestrator=self.orchestrator)

    def test_signals_otp_confirmed(self) -> None:
        job_id = str(uuid4())
        self.use_case.execute(ConfirmOTPInput(job_id=job_id))
        assert job_id in self.orchestrator.otp_confirmed

    def test_returns_success(self) -> None:
        result = self.use_case.execute(ConfirmOTPInput(job_id=str(uuid4())))
        assert result.success is True


# ---------------------------------------------------------------------------
# ApplyRemapProposal
# ---------------------------------------------------------------------------
class TestApplyRemapProposal:
    def setup_method(self) -> None:
        self.job_store = FakeJobStore()
        self.orchestrator = FakeOrchestrator()
        self.event_bus = FakeEventBus()
        self.use_case = ApplyRemapProposal(
            job_store=self.job_store,
            orchestrator=self.orchestrator,
            event_bus=self.event_bus,
        )
        # Seed a proposal
        self.proposal = RemapProposal(
            id=str(uuid4()),
            bank="banco_general",
            breakage_id=str(uuid4()),
            judge_decision="remap_required",
            confidence=0.9,
            risk="low",
            patch_diff="--- a\n+++ b",
            status=RemapStatus.PENDING,
            expires_at=_now(),
        )
        self.job_store.save_proposal(self.proposal)

    def test_approve_updates_status(self) -> None:
        result = self.use_case.execute(
            ApplyRemapInput(proposal_id=self.proposal.id, action=RemapAction.APPROVE)
        )
        assert result.success is True
        assert result.new_status == "approved"

    def test_reject_updates_status(self) -> None:
        result = self.use_case.execute(
            ApplyRemapInput(proposal_id=self.proposal.id, action=RemapAction.REJECT)
        )
        assert result.success is True
        assert result.new_status == "rejected"

    def test_approve_signals_orchestrator(self) -> None:
        self.use_case.execute(
            ApplyRemapInput(proposal_id=self.proposal.id, action=RemapAction.APPROVE)
        )
        assert len(self.orchestrator.remap_approved) == 1

    def test_missing_proposal_raises(self) -> None:
        with pytest.raises(ValueError):
            self.use_case.execute(
                ApplyRemapInput(proposal_id=str(uuid4()), action=RemapAction.APPROVE)
            )


# ---------------------------------------------------------------------------
# ListAccounts
# ---------------------------------------------------------------------------
class TestListAccounts:
    def setup_method(self) -> None:
        self.job_store = FakeJobStore()
        self.use_case = ListAccounts(job_store=self.job_store)

    def test_returns_empty_list(self) -> None:
        result = self.use_case.execute(ListAccountsInput(bank="banco_general"))
        assert result.accounts == []

    def test_returns_accounts_for_bank(self) -> None:
        acc = _make_account()
        self.job_store.save_account(acc)
        result = self.use_case.execute(ListAccountsInput(bank="banco_general"))
        assert len(result.accounts) == 1


# ---------------------------------------------------------------------------
# GetJobResult
# ---------------------------------------------------------------------------
class TestGetJobResult:
    def setup_method(self) -> None:
        self.job_store = FakeJobStore()
        self.use_case = GetJobResult(job_store=self.job_store)

    def test_returns_job_with_transactions(self) -> None:
        job = _make_job(JobStatus.COMPLETED)
        self.job_store.save_job(job)
        tx = _make_transaction()
        self.job_store.save_transaction(tx)
        result = self.use_case.execute(GetJobResultInput(job_id=job.id))
        assert result.job.id == job.id
        assert len(result.transactions) == 1

    def test_missing_job_raises(self) -> None:
        with pytest.raises(ValueError):
            self.use_case.execute(GetJobResultInput(job_id=str(uuid4())))


# ---------------------------------------------------------------------------
# RegisterCredential
# ---------------------------------------------------------------------------
class TestRegisterCredential:
    def setup_method(self) -> None:
        self.secret_store = FakeSecretStore()
        self.use_case = RegisterCredential(secret_store=self.secret_store)

    def test_stores_and_returns_credential(self) -> None:
        result = self.use_case.execute(
            RegisterCredentialInput(
                bank="banco_general",
                plaintext="s3cr3t",
                label="Mi cuenta",
            )
        )
        assert result.credential.bank == "banco_general"
        assert result.credential.label == "Mi cuenta"
        # Must not store plaintext in the returned credential
        assert not hasattr(result.credential, "plaintext")
        assert not hasattr(result.credential, "password")

    def test_secret_store_receives_call(self) -> None:
        self.use_case.execute(
            RegisterCredentialInput(
                bank="bg",
                plaintext="pass",
                label="label",
            )
        )
        assert len(self.secret_store._stored) == 1
        assert self.secret_store._stored[0]["bank"] == "bg"

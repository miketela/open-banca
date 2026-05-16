"""Tests for all 11 domain ports — Protocol conformance + runtime_checkable."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from open_banca_domain.entities.bank_map import BankMap, ParserConfig
from open_banca_domain.entities.credential import Credential
from open_banca_domain.entities.job import Job
from open_banca_domain.entities.transaction import Transaction
from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType
from open_banca_domain.ports.browser_driver_port import BrowserDriverPort
from open_banca_domain.ports.clock_port import ClockPort
from open_banca_domain.ports.event_bus_port import EventBusPort
from open_banca_domain.ports.excel_parser_port import ExcelParserPort
from open_banca_domain.ports.job_store_port import JobStorePort
from open_banca_domain.ports.llm_port import LLMPort, LLMResponse
from open_banca_domain.ports.orchestrator_port import OrchestratorPort
from open_banca_domain.ports.sandbox_port import SandboxPort
from open_banca_domain.ports.scraper_port import ScrapeResult, ScraperPort
from open_banca_domain.ports.secret_store_port import SecretStorePort
from open_banca_domain.ports.workflow_engine_port import WorkflowEnginePort


def _now() -> datetime:
    return datetime.now(UTC)


def _make_credential() -> Credential:
    return Credential(id=str(uuid4()), bank="bg", credential_ref="ref", label="l")


# ---------------------------------------------------------------------------
# Minimal concrete fakes that implement each Protocol
# ---------------------------------------------------------------------------


class FakeScraper:
    def execute_map(self, map: BankMap, credential: Credential) -> ScrapeResult:
        return ScrapeResult(raw_data=b"", breakage_events=[], metadata={})


class FakeOrchestrator:
    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        return str(uuid4())

    def signal_otp_confirmed(self, job_id: str) -> None: ...
    def signal_remap_approved(self, proposal_id: str) -> None: ...
    def cancel_job(self, job_id: str) -> None: ...
    def query_status(self, job_id: str) -> str:
        return "pending"


class FakeJobStore:
    def save_job(self, job: Job) -> None: ...
    def load_job(self, job_id: str) -> Job | None:
        return None

    def list_jobs(self) -> list[Job]:
        return []

    def save_account(self, account: Any) -> None: ...
    def save_transaction(self, tx: Transaction) -> None: ...
    def get_cursor(self, bank: str) -> str | None:
        return None

    def save_cursor(self, bank: str, cursor: str) -> None: ...


class FakeSecretStore:
    def store_credential(self, bank: str, plaintext: str, label: str) -> Credential:
        return _make_credential()

    def fetch_credential(self, credential_ref: str) -> str:
        return "secret"

    def rotate_master(self) -> None: ...


class FakeLLM:
    def invoke(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        sensitive_data: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="ok", input_tokens=1, output_tokens=1, model=model)


class FakeBrowserDriver:
    def launch_session(self, job_id: str) -> str:
        return "session-1"

    def navigate(self, session_id: str, url: str) -> None: ...
    def fill_sensitive(self, session_id: str, selector: str, value: str) -> None: ...
    def click(self, session_id: str, selector: str) -> None: ...
    def screenshot(self, session_id: str) -> bytes:
        return b""

    def close(self, session_id: str) -> None: ...


class FakeSandbox:
    def spawn(self, job_id: str) -> str:
        return "container-1"

    def kill(self, container_id: str) -> None: ...
    def attach_network_policy(self, container_id: str, policy: str) -> None: ...


class FakeExcelParser:
    def parse(self, workbook_bytes: bytes, parser_spec: ParserConfig) -> list[Transaction]:
        return []


class FakeEventBus:
    def publish(self, event: WebhookEvent) -> None: ...
    def retry_pending(self) -> None: ...


class FakeClock:
    def now(self) -> datetime:
        return _now()


class FakeWorkflowEngine:
    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        return str(uuid4())

    def signal_otp_confirmed(self, job_id: str) -> None: ...
    def signal_remap_approved(self, proposal_id: str) -> None: ...
    def cancel_job(self, job_id: str) -> None: ...
    def query_status(self, job_id: str) -> str:
        return "pending"

    def get_workflow_history(self, job_id: str) -> list[dict[str, Any]]:
        return []

    def time_skip(self, seconds: float) -> None: ...


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScraperPort:
    def test_runtime_checkable(self) -> None:
        fake = FakeScraper()
        assert isinstance(fake, ScraperPort)

    def test_execute_map_returns_scrape_result(self) -> None:
        fake = FakeScraper()
        bm = BankMap(bank_id="bg", version="1.0", steps=[], schema_version="1")
        cred = _make_credential()
        result = fake.execute_map(bm, cred)
        assert isinstance(result, ScrapeResult)
        assert result.raw_data == b""


class TestOrchestratorPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeOrchestrator(), OrchestratorPort)

    def test_start_job_returns_id(self) -> None:
        result = FakeOrchestrator().start_job("bg", "ref", "full")
        assert isinstance(result, str)


class TestJobStorePort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeJobStore(), JobStorePort)

    def test_load_job_returns_none_when_empty(self) -> None:
        assert FakeJobStore().load_job("x") is None

    def test_list_jobs_returns_list(self) -> None:
        assert FakeJobStore().list_jobs() == []


class TestSecretStorePort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeSecretStore(), SecretStorePort)

    def test_store_returns_credential(self) -> None:
        cred = FakeSecretStore().store_credential("bg", "pass", "label")
        assert isinstance(cred, Credential)

    def test_fetch_returns_string(self) -> None:
        assert isinstance(FakeSecretStore().fetch_credential("ref"), str)


class TestLLMPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeLLM(), LLMPort)

    def test_invoke_returns_response(self) -> None:
        resp = FakeLLM().invoke([{"role": "user", "content": "hi"}], "gpt-4", 100)
        assert isinstance(resp, LLMResponse)
        assert resp.content == "ok"

    def test_llm_response_fields(self) -> None:
        r = LLMResponse(content="x", input_tokens=10, output_tokens=5, model="m")
        assert r.input_tokens == 10
        assert r.output_tokens == 5


class TestBrowserDriverPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeBrowserDriver(), BrowserDriverPort)

    def test_launch_session_returns_id(self) -> None:
        assert FakeBrowserDriver().launch_session("job-1") == "session-1"

    def test_screenshot_returns_bytes(self) -> None:
        assert isinstance(FakeBrowserDriver().screenshot("session-1"), bytes)


class TestSandboxPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeSandbox(), SandboxPort)

    def test_spawn_returns_container_id(self) -> None:
        assert FakeSandbox().spawn("job-1") == "container-1"


class TestExcelParserPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeExcelParser(), ExcelParserPort)

    def test_parse_returns_list(self) -> None:
        spec = ParserConfig()
        result = FakeExcelParser().parse(b"", spec)
        assert result == []


class TestEventBusPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeEventBus(), EventBusPort)

    def test_publish_accepts_webhook_event(self) -> None:
        ev = WebhookEvent(
            id=str(uuid4()),
            event_type=WebhookEventType.JOB_CREATED,
            payload={},
            signature="",
            dispatched_at=_now(),
        )
        FakeEventBus().publish(ev)  # no exception


class TestClockPort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeClock(), ClockPort)

    def test_now_returns_datetime(self) -> None:
        dt = FakeClock().now()
        assert isinstance(dt, datetime)
        assert dt.tzinfo is not None


class TestWorkflowEnginePort:
    def test_runtime_checkable(self) -> None:
        assert isinstance(FakeWorkflowEngine(), WorkflowEnginePort)

    def test_get_workflow_history_returns_list(self) -> None:
        assert FakeWorkflowEngine().get_workflow_history("job-1") == []

    def test_time_skip_callable(self) -> None:
        FakeWorkflowEngine().time_skip(10.0)  # no exception

    def test_extends_orchestrator(self) -> None:
        fe = FakeWorkflowEngine()
        assert isinstance(fe, OrchestratorPort)

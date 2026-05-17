"""Tests for PollingHumanInputWaiter."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from open_banca_browser.errors import HumanInputRequired
from open_banca_browser.human_input_waiter import HumanInputTimeoutError, PollingHumanInputWaiter


def _required(*, timeout_s: int = 60) -> HumanInputRequired:
    return HumanInputRequired(
        question_text="Question?",
        field_key="security_q_1",
        question_hash="abc",
        selector="#answer",
        timeout_s=timeout_s,
    )


def test_wait_for_answer_returns_on_first_poll_hit() -> None:
    poll_port = MagicMock()
    poll_port.take_human_input_answer.return_value = ("secret", True)
    sleeps: list[float] = []

    waiter = PollingHumanInputWaiter(
        job_id="job-1",
        poll_port=poll_port,
        sleep_fn=lambda s: sleeps.append(s),
    )
    required = _required()
    answer, persist = waiter.wait_for_answer(required)

    assert answer == "secret"
    assert persist is True
    poll_port.take_human_input_answer.assert_called_once_with("job-1", "security_q_1")
    assert sleeps == []


def test_wait_for_answer_polls_until_answer() -> None:
    poll_port = MagicMock()
    poll_port.take_human_input_answer.side_effect = [None, None, ("ok", False)]
    sleeps: list[float] = []

    waiter = PollingHumanInputWaiter(
        job_id="job-1",
        poll_port=poll_port,
        poll_interval_s=0.01,
        sleep_fn=lambda s: sleeps.append(s),
    )
    answer, persist = waiter.wait_for_answer(_required(timeout_s=300))

    assert answer == "ok"
    assert persist is False
    assert poll_port.take_human_input_answer.call_count == 3
    assert len(sleeps) == 2


def test_wait_for_answer_timeout() -> None:
    poll_port = MagicMock()
    poll_port.take_human_input_answer.return_value = None

    waiter = PollingHumanInputWaiter(
        job_id="job-1",
        poll_port=poll_port,
        poll_interval_s=0.0,
        sleep_fn=lambda _s: None,
    )

    with pytest.raises(HumanInputTimeoutError):
        waiter.wait_for_answer(_required(timeout_s=0))


def test_on_required_callback_invoked() -> None:
    poll_port = MagicMock()
    poll_port.take_human_input_answer.return_value = ("x", True)
    callback = MagicMock()
    required = _required()

    waiter = PollingHumanInputWaiter(
        job_id="job-1",
        poll_port=poll_port,
        on_required=callback,
    )
    waiter.wait_for_answer(required)

    callback.assert_called_once_with(required)

"""Tests verifying that sensitive fill values are never logged or stored in plain text."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from open_banca_browser.errors import SelectorNotFound, ValueNotResolved
from open_banca_browser.step_executors.fill import execute_fill
from open_banca_domain.entities.bank_map import StepSpec


@pytest.fixture()
def mock_page() -> MagicMock:
    page = MagicMock()
    locator = MagicMock()
    locator.count.return_value = 1
    locator.fill.return_value = None
    locator.evaluate.return_value = None
    page.locator.return_value = locator
    return page


def _make_fill_step(
    selector: str = "#password",
    value_ref: str = "cred:bank_password",
    sensitive: bool = True,
) -> StepSpec:
    return StepSpec(
        step_id="step-fill-1",
        action="fill",
        target=selector,
        selector=selector,
        value_ref=value_ref,
        sensitive=sensitive,
    )


def test_fill_calls_resolver_with_value_ref(mock_page: MagicMock) -> None:
    """secret_resolver is called with the value_ref key."""
    resolver = MagicMock(return_value="supersecret123")
    step = _make_fill_step()
    execute_fill(mock_page, step, secret_resolver=resolver)
    resolver.assert_called_once_with("cred:bank_password")


def test_fill_writes_plaintext_to_input(mock_page: MagicMock) -> None:
    """Plaintext is passed to locator.fill()."""
    resolver = MagicMock(return_value="mypassword")
    step = _make_fill_step()
    execute_fill(mock_page, step, secret_resolver=resolver)
    mock_page.locator.return_value.fill.assert_called_once_with("mypassword", timeout=10_000)


def test_fill_sensitive_marks_field(mock_page: MagicMock) -> None:
    """sensitive=True causes data-sensitive attribute to be set on the element."""
    resolver = MagicMock(return_value="s3cr3t")
    step = _make_fill_step(sensitive=True)
    execute_fill(mock_page, step, secret_resolver=resolver)
    # evaluate() was called to mark as data-sensitive
    mock_page.locator.return_value.evaluate.assert_called()


def test_fill_non_sensitive_skips_marking(mock_page: MagicMock) -> None:
    """sensitive=False does not mark the field."""
    resolver = MagicMock(return_value="plain_value")
    step = _make_fill_step(sensitive=False)
    execute_fill(mock_page, step, secret_resolver=resolver)
    mock_page.locator.return_value.evaluate.assert_not_called()


def test_fill_raises_value_not_resolved_on_resolver_error(mock_page: MagicMock) -> None:
    """If resolver raises, ValueNotResolved is raised — not the original exception."""

    def bad_resolver(ref: str) -> str:
        raise KeyError(f"no secret for {ref}")

    step = _make_fill_step()
    with pytest.raises(ValueNotResolved):
        execute_fill(mock_page, step, secret_resolver=bad_resolver)


def test_fill_raises_value_not_resolved_on_empty_string(mock_page: MagicMock) -> None:
    """Resolver returning empty string raises ValueNotResolved."""
    step = _make_fill_step()
    with pytest.raises(ValueNotResolved):
        execute_fill(mock_page, step, secret_resolver=lambda _: "")


def test_fill_raises_selector_not_found(mock_page: MagicMock) -> None:
    """SelectorNotFound is raised when element is absent."""
    mock_page.locator.return_value.count.return_value = 0
    resolver = MagicMock(return_value="secret")
    step = _make_fill_step(selector="#nonexistent")
    with pytest.raises(SelectorNotFound):
        execute_fill(mock_page, step, secret_resolver=resolver)


def test_fill_secret_not_in_log_output(
    mock_page: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The secret plaintext must never appear in any log message."""
    secret = "SUPER_SECRET_CANARY_12345"
    resolver = MagicMock(return_value=secret)
    step = _make_fill_step()

    with caplog.at_level(logging.DEBUG, logger="open_banca_browser"):
        execute_fill(mock_page, step, secret_resolver=resolver)

    for record in caplog.records:
        assert secret not in record.getMessage(), f"Secret leaked in log: {record.getMessage()!r}"

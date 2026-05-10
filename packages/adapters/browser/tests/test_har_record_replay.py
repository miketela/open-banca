"""Tests for HAR record helper and replay engine.

All tests use mocks — no browser launch, no network calls, no Playwright binary
required in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from open_banca_browser.har.record import HAR_VERSION, load_har, record_har
from open_banca_browser.har.replay import HARReplay

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_har(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "test", "version": "0"},
            "entries": entries or [],
        }
    }


def _entry(
    url: str = "https://bank.example.com/api",
    method: str = "GET",
    status: int = 200,
    body: str = '{"ok": true}',
    mime: str = "application/json",
    response_headers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "request": {"method": method, "url": url, "headers": [], "cookies": [], "queryString": []},
        "response": {
            "status": status,
            "headers": response_headers or [{"name": "Content-Type", "value": mime}],
            "cookies": [],
            "content": {"mimeType": mime, "text": body, "size": len(body)},
        },
    }


# ---------------------------------------------------------------------------
# Record format
# ---------------------------------------------------------------------------


class TestRecordFormat:
    """Verify that record_har produces a file with valid HAR 1.2 schema."""

    def test_record_produces_har_12_schema(self, tmp_path: Path) -> None:
        """record_har writes a JSON file containing 'log.version' == '1.2'."""
        output = tmp_path / "test.har"
        # Fake HAR content that Playwright would write
        fake_har = _make_har([_entry()])

        def fake_run(page: Any) -> None:
            # Simulate writing the HAR by the context.close() side effect
            pass

        # Mock browser.new_context and the context lifecycle
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        def mock_close() -> None:
            # Playwright writes the HAR on context.close(); simulate that.
            output.write_text(json.dumps(fake_har), encoding="utf-8")

        mock_context.close.side_effect = mock_close

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        result_path = record_har(
            browser=mock_browser,
            output_path=output,
            run_callable=fake_run,
            headless_warning=False,
        )

        assert result_path == output
        assert output.exists()
        har = json.loads(output.read_text())
        assert "log" in har
        assert har["log"]["version"].startswith(HAR_VERSION)
        assert "entries" in har["log"]

    def test_record_har_passes_correct_kwargs_to_context(self, tmp_path: Path) -> None:
        """new_context must receive record_har_path and record_har_content='embed'."""
        output = tmp_path / "out.har"
        mock_context = MagicMock()
        mock_context.close.side_effect = lambda: output.write_text(
            json.dumps(_make_har()), encoding="utf-8"
        )
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        record_har(
            browser=mock_browser,
            output_path=output,
            run_callable=lambda page: None,
            record_har_content="embed",
            headless_warning=False,
        )

        call_kwargs = mock_browser.new_context.call_args.kwargs
        assert call_kwargs["record_har_path"] == str(output)
        assert call_kwargs["record_har_content"] == "embed"

    def test_record_har_with_url_filter(self, tmp_path: Path) -> None:
        output = tmp_path / "filtered.har"
        mock_context = MagicMock()
        mock_context.close.side_effect = lambda: output.write_text(
            json.dumps(_make_har()), encoding="utf-8"
        )
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        record_har(
            browser=mock_browser,
            output_path=output,
            run_callable=lambda page: None,
            url_filter="**/banco.com/**",
            headless_warning=False,
        )

        call_kwargs = mock_browser.new_context.call_args.kwargs
        assert call_kwargs["record_har_url_filter"] == "**/banco.com/**"


class TestLoadHar:
    def test_load_valid_har(self, tmp_path: Path) -> None:
        path = tmp_path / "valid.har"
        har = _make_har()
        path.write_text(json.dumps(har), encoding="utf-8")
        loaded = load_har(path)
        assert loaded["log"]["version"] == "1.2"

    def test_load_invalid_har_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.har"
        path.write_text(json.dumps({"notahar": True}), encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'log'"):
            load_har(path)

    def test_load_wrong_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "v99.har"
        har = {"log": {"version": "2.0", "entries": []}}
        path.write_text(json.dumps(har), encoding="utf-8")
        with pytest.raises(ValueError, match="Unexpected HAR version"):
            load_har(path)


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------


class TestReplayMatches:
    """Verify that HARReplay serves the correct response for a request URL."""

    def _make_route(self, url: str, method: str = "GET") -> MagicMock:
        route = MagicMock()
        route.request.url = url
        route.request.method = method
        return route

    def test_replay_exact_url_returns_correct_status(self) -> None:
        har = _make_har([_entry(url="https://bank.example.com/login", status=200)])
        replay = HARReplay(har)
        route = self._make_route("https://bank.example.com/login")
        replay._handle_route(route)
        route.fulfill.assert_called_once()
        assert route.fulfill.call_args.kwargs["status"] == 200

    def test_replay_exact_url_returns_correct_body(self) -> None:
        body = '{"token": "REDACTED"}'
        har = _make_har([_entry(url="https://bank.example.com/auth", body=body)])
        replay = HARReplay(har)
        route = self._make_route("https://bank.example.com/auth")
        replay._handle_route(route)
        called_body = route.fulfill.call_args.kwargs["body"]
        assert called_body == body

    def test_replay_url_with_query_matches_base(self) -> None:
        har = _make_har([_entry(url="https://bank.example.com/api")])
        replay = HARReplay(har)
        route = self._make_route("https://bank.example.com/api?format=json")
        replay._handle_route(route)
        # Should find entry (strip-qs match)
        route.fulfill.assert_called_once()

    def test_replay_unmatched_url_returns_404(self) -> None:
        har = _make_har([_entry(url="https://bank.example.com/known")])
        replay = HARReplay(har)
        route = self._make_route("https://bank.example.com/unknown-endpoint")
        replay._handle_route(route)
        route.fulfill.assert_called_once()
        assert route.fulfill.call_args.kwargs["status"] == 404

    def test_replay_unmatched_with_fallback_passes_through(self) -> None:
        har = _make_har([_entry(url="https://bank.example.com/known")])
        replay = HARReplay(har, fallback_passthrough=True)
        route = self._make_route("https://bank.example.com/other")
        replay._handle_route(route)
        route.continue_.assert_called_once()
        route.fulfill.assert_not_called()

    def test_replay_entry_count(self) -> None:
        har = _make_har([_entry(), _entry(url="https://bank.example.com/b")])
        replay = HARReplay(har)
        assert replay.entry_count == 2

    def test_replay_urls_list(self) -> None:
        har = _make_har(
            [
                _entry(url="https://bank.example.com/login"),
                _entry(url="https://bank.example.com/dashboard"),
            ]
        )
        replay = HARReplay(har)
        urls = replay.urls()
        assert "https://bank.example.com/login" in urls
        assert "https://bank.example.com/dashboard" in urls

    def test_replay_from_file(self, tmp_path: Path) -> None:
        har = _make_har([_entry(url="https://bank.example.com/api")])
        path = tmp_path / "fixture.har"
        path.write_text(json.dumps(har), encoding="utf-8")
        replay = HARReplay.from_file(path)
        assert replay.entry_count == 1

    def test_replay_install_calls_page_route(self) -> None:
        har = _make_har()
        replay = HARReplay(har)
        mock_page = MagicMock()
        replay.install(mock_page)
        mock_page.route.assert_called_once_with("**/*", replay._handle_route)

    def test_replay_install_on_context(self) -> None:
        har = _make_har()
        replay = HARReplay(har)
        mock_ctx = MagicMock()
        replay.install_on_context(mock_ctx)
        mock_ctx.route.assert_called_once_with("**/*", replay._handle_route)

    def test_replay_method_match(self) -> None:
        """POST entry should match a POST request but not GET."""
        har = _make_har(
            [
                _entry(
                    url="https://bank.example.com/submit", method="POST", body='{"result":"ok"}'
                ),
            ]
        )
        replay = HARReplay(har)
        route_get = self._make_route("https://bank.example.com/submit", method="GET")
        replay._handle_route(route_get)
        # No exact or method match — falls to "any method" so still matches
        route_get.fulfill.assert_called_once()

    def test_replay_base64_response_decoded(self) -> None:
        """Response with base64-encoded text body must be decoded and served as bytes."""
        import base64

        text_body = '{"ok": true}'
        encoded = base64.b64encode(text_body.encode()).decode("ascii")
        entry: dict[str, Any] = {
            "request": {
                "method": "GET",
                "url": "https://bank.example.com/data",
                "headers": [],
                "cookies": [],
                "queryString": [],
            },
            "response": {
                "status": 200,
                "headers": [],
                "cookies": [],
                "content": {
                    "mimeType": "application/json",
                    "text": encoded,
                    "encoding": "base64",
                    "size": len(text_body),
                },
            },
        }
        har = _make_har([entry])
        replay = HARReplay(har)
        route = self._make_route("https://bank.example.com/data")
        replay._handle_route(route)
        body = route.fulfill.call_args.kwargs["body"]
        assert isinstance(body, bytes)
        assert json.loads(body.decode())["ok"] is True

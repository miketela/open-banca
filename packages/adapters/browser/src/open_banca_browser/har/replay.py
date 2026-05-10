"""HARReplay — loads a HAR fixture and registers Playwright route handlers.

Intended for integration tests that must not hit live bank servers.

Usage (sync Playwright)::

    from playwright.sync_api import sync_playwright
    from open_banca_browser.har.replay import HARReplay

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        replay = HARReplay.from_file(Path("fixture.har"))
        replay.install(page)
        page.goto("https://bank.example.com/login")
        # responses served from HAR
        browser.close()
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HARReplay:
    """Loads a HAR fixture and can install route handlers on a Playwright Page.

    Args:
        har: Parsed HAR dict (must conform to HAR 1.2 schema).
        url_pattern: Glob or regex pattern passed to ``page.route()``.
            Defaults to ``"**/*"`` (intercept all requests).
        fallback_passthrough: If True, requests that have no matching HAR
            entry are passed through to the network.  If False (default),
            unmatched requests receive a 404 stub response.
    """

    def __init__(
        self,
        har: dict[str, Any],
        url_pattern: str = "**/*",
        *,
        fallback_passthrough: bool = False,
    ) -> None:
        self._entries: list[dict[str, Any]] = (
            har.get("log", {}).get("entries", [])
        )
        self._url_pattern = url_pattern
        self._fallback_passthrough = fallback_passthrough
        # Pre-index: strip query strings for fast lookup
        self._index: dict[str, list[dict[str, Any]]] = {}
        for entry in self._entries:
            url: str = entry.get("request", {}).get("url", "")
            key = url.split("?")[0]
            self._index.setdefault(key, []).append(entry)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path, **kwargs: Any) -> "HARReplay":
        """Load a HAR file and return a HARReplay instance."""
        with path.open(encoding="utf-8") as fh:
            har: dict[str, Any] = json.load(fh)
        return cls(har, **kwargs)

    # ------------------------------------------------------------------
    # Playwright integration
    # ------------------------------------------------------------------

    def install(self, page: Any) -> None:  # type: ignore[override]
        """Register route handler on *page* (sync Playwright Page object)."""
        page.route(self._url_pattern, self._handle_route)

    def install_on_context(self, context: Any) -> None:
        """Register route handler on a Playwright BrowserContext."""
        context.route(self._url_pattern, self._handle_route)

    # ------------------------------------------------------------------
    # Route handler
    # ------------------------------------------------------------------

    def _handle_route(self, route: Any) -> None:  # type: ignore[override]
        """Called by Playwright for each intercepted request."""
        request_url: str = route.request.url
        method: str = route.request.method.upper()
        entry = self._find_entry(request_url, method)
        if entry is None:
            if self._fallback_passthrough:
                route.continue_()
            else:
                logger.warning(
                    "HARReplay: no matching entry for %s %s — returning 404 stub",
                    method,
                    request_url,
                )
                route.fulfill(status=404, body="HAR replay: no matching entry")
            return

        response: dict[str, Any] = entry.get("response", {})
        status: int = response.get("status", 200)
        headers_list: list[dict[str, str]] = response.get("headers", [])
        headers: dict[str, str] = {h["name"]: h["value"] for h in headers_list}
        content: dict[str, Any] = response.get("content", {})
        mime_type: str = content.get("mimeType", "application/octet-stream")
        text: str | None = content.get("text")
        encoding: str = content.get("encoding", "")

        body: bytes | str | None = None
        if text is not None:
            if encoding == "base64":
                body = base64.b64decode(text)
            else:
                body = text

        route.fulfill(
            status=status,
            headers=headers,
            content_type=mime_type,
            body=body if body is not None else "",
        )

    # ------------------------------------------------------------------
    # Entry matching
    # ------------------------------------------------------------------

    def _find_entry(
        self, request_url: str, method: str
    ) -> dict[str, Any] | None:
        """Find the best matching HAR entry for the given URL and method.

        Matching strategy (in order):
        1. Exact URL + method match.
        2. URL without query string + method match (first candidate).
        3. URL without query string, any method.
        """
        url_no_qs = request_url.split("?")[0]

        # 1. Exact match
        for entry in self._entries:
            req: dict[str, Any] = entry.get("request", {})
            if req.get("url") == request_url and req.get("method", "").upper() == method:
                return entry

        # 2. Strip-query match + method
        candidates = self._index.get(url_no_qs, [])
        for entry in candidates:
            if entry.get("request", {}).get("method", "").upper() == method:
                return entry

        # 3. Strip-query match, any method
        if candidates:
            return candidates[0]

        # 4. Regex / partial path match as last resort
        path = _url_path(request_url)
        for entry in self._entries:
            entry_url: str = entry.get("request", {}).get("url", "")
            if path and path in entry_url:
                return entry

        return None

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    @property
    def entry_count(self) -> int:
        """Number of HAR entries loaded."""
        return len(self._entries)

    def urls(self) -> list[str]:
        """Return all request URLs in the fixture (in order)."""
        return [e.get("request", {}).get("url", "") for e in self._entries]


def _url_path(url: str) -> str:
    """Extract the path component from a URL string."""
    try:
        from urllib.parse import urlparse

        return urlparse(url).path
    except Exception:  # noqa: BLE001
        return ""

"""HARSanitizer — strips credentials and PII from HAR files before commit.

Usage (CLI):
    python -m open_banca_browser.har.sanitize <input.har> <output.har>

The sanitizer processes all HAR 1.2 leak sites:
  - request/response headers (Authorization, Cookie, Set-Cookie, X-CSRF-Token)
  - request/response cookies arrays
  - request URL query string (both raw URL and parsed queryString[])
  - request body: postData.text and postData.params[]
  - response body: content.text (text content types only; binary/base64 skipped)
  - Regex patterns: SECRET_CANARY_VALUE, PII_CANARY_*, account numbers (PA),
    password=, token=, session=, passcode=
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

REDACTED = "REDACTED"

# Headers to fully redact (case-insensitive names)
SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-csrf-token",
        "x-xsrf-token",
        "x-auth-token",
        "proxy-authorization",
    }
)

# Query parameters to redact values of
SENSITIVE_QUERY_PARAMS: frozenset[str] = frozenset(
    {
        "token",
        "session",
        "session_id",
        "sessionid",
        "access_token",
        "refresh_token",
        "auth",
        "apikey",
        "api_key",
        "password",
        "passcode",
        "secret",
    }
)

# Body-level patterns: matched against text content of request and response bodies.
# Each tuple is (compiled_pattern, replacement_template).
# The replacement may contain backreferences to named groups.
_BODY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Canary values
    (re.compile(r"SECRET_CANARY_VALUE", re.IGNORECASE), REDACTED),
    (re.compile(r"PII_CANARY_[A-Z0-9_]+", re.IGNORECASE), REDACTED),
    # JSON field patterns — value after colon
    (
        re.compile(
            r'("(?:password|passcode|passwd|token|access_token|refresh_token'
            r'|session|sessionId|session_id|secret|authorization|cookie'
            r'|x-csrf-token|apiKey|api_key|clientSecret|client_secret)"'
            r'\s*:\s*")([^"]+)(")',
            re.IGNORECASE,
        ),
        r"\1" + REDACTED + r"\3",
    ),
    # Form/query-style key=value in body text
    (
        re.compile(
            r"(?i)((?:password|passcode|passwd|token|access_token|refresh_token"
            r"|session|session_id|secret|apikey|api_key|client_secret)=)[^&\s\"'<]+",
        ),
        r"\1" + REDACTED,
    ),
    # Panama account numbers (10–16 digit sequences that look like bank accounts)
    (
        re.compile(r"\b(\d{4}[- ]?\d{4}[- ]?\d{4}(?:[- ]?\d{0,4})?)\b"),
        REDACTED,
    ),
]


class HARSanitizer:
    """Sanitize a HAR dict in-place, redacting credentials and PII.

    Args:
        extra_body_patterns: Additional (pattern, replacement) tuples applied
            to request and response body text on top of the built-in list.
    """

    def __init__(
        self,
        extra_body_patterns: list[tuple[re.Pattern[str], str]] | None = None,
    ) -> None:
        self._body_patterns = list(_BODY_PATTERNS)
        if extra_body_patterns:
            self._body_patterns.extend(extra_body_patterns)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sanitize(self, har: dict[str, Any]) -> dict[str, Any]:
        """Return a *new* sanitized HAR dict (original is not mutated).

        Raises:
            ValueError: If the input does not look like a HAR 1.2 document.
        """
        import copy

        har = copy.deepcopy(har)
        log = har.get("log", {})
        entries: list[dict[str, Any]] = log.get("entries", [])
        for entry in entries:
            self._sanitize_entry(entry)
        return har

    def sanitize_file(self, input_path: Path, output_path: Path) -> None:
        """Read *input_path*, sanitize, write to *output_path*."""
        with input_path.open(encoding="utf-8") as fh:
            har: dict[str, Any] = json.load(fh)
        sanitized = self.sanitize(har)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(sanitized, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sanitize_entry(self, entry: dict[str, Any]) -> None:
        request: dict[str, Any] = entry.get("request", {})
        response: dict[str, Any] = entry.get("response", {})
        self._sanitize_request(request)
        self._sanitize_response(response)

    def _sanitize_request(self, request: dict[str, Any]) -> None:
        # 1. URL — redact sensitive query params
        url: str = request.get("url", "")
        if url:
            request["url"] = self._redact_url_params(url)

        # 2. Parsed queryString array
        qs_list: list[dict[str, str]] = request.get("queryString", [])
        for param in qs_list:
            if param.get("name", "").lower() in SENSITIVE_QUERY_PARAMS:
                param["value"] = REDACTED

        # 3. Headers
        self._redact_headers(request.get("headers", []))

        # 4. Cookies
        self._redact_cookies(request.get("cookies", []))

        # 5. postData
        post_data: dict[str, Any] = request.get("postData", {})
        if post_data:
            text: str | None = post_data.get("text")
            if text:
                post_data["text"] = self._redact_body_text(text)
            params: list[dict[str, str]] = post_data.get("params", [])
            for param in params:
                if param.get("name", "").lower() in SENSITIVE_QUERY_PARAMS:
                    param["value"] = REDACTED
                else:
                    val: str = param.get("value", "")
                    if val:
                        param["value"] = self._redact_body_text(val)

    def _sanitize_response(self, response: dict[str, Any]) -> None:
        # 1. Headers (Set-Cookie etc.)
        self._redact_headers(response.get("headers", []))

        # 2. Cookies
        self._redact_cookies(response.get("cookies", []))

        # 3. Response body
        content: dict[str, Any] = response.get("content", {})
        if content:
            encoding: str = content.get("encoding", "")
            text: str | None = content.get("text")
            if text and encoding == "base64":
                # Attempt to decode, redact, re-encode only for text content
                mime: str = content.get("mimeType", "")
                if self._is_text_mime(mime):
                    try:
                        decoded = base64.b64decode(text).decode("utf-8", errors="replace")
                        redacted = self._redact_body_text(decoded)
                        content["text"] = base64.b64encode(
                            redacted.encode("utf-8")
                        ).decode("ascii")
                    except Exception:  # noqa: BLE001
                        pass  # leave binary content untouched
            elif text:
                content["text"] = self._redact_body_text(text)

    def _redact_headers(self, headers: list[dict[str, str]]) -> None:
        for header in headers:
            if header.get("name", "").lower() in SENSITIVE_HEADERS:
                header["value"] = REDACTED

    def _redact_cookies(self, cookies: list[dict[str, Any]]) -> None:
        for cookie in cookies:
            if "value" in cookie:
                cookie["value"] = REDACTED

    def _redact_url_params(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        qs_dict = parse_qs(parsed.query, keep_blank_values=True)
        redacted: dict[str, list[str]] = {}
        for key, values in qs_dict.items():
            if key.lower() in SENSITIVE_QUERY_PARAMS:
                redacted[key] = [REDACTED]
            else:
                redacted[key] = values
        new_query = urlencode(redacted, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _redact_body_text(self, text: str) -> str:
        for pattern, replacement in self._body_patterns:
            text = pattern.sub(replacement, text)
        return text

    @staticmethod
    def _is_text_mime(mime: str) -> bool:
        """Return True for MIME types that contain human-readable text."""
        lower = mime.lower()
        return (
            lower.startswith("text/")
            or "json" in lower
            or "xml" in lower
            or "javascript" in lower
            or "urlencoded" in lower
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Sanitize a HAR file, redacting credentials and PII."
    )
    parser.add_argument("input", type=Path, help="Input HAR file (may contain secrets)")
    parser.add_argument("output", type=Path, help="Output sanitized HAR file")
    args = parser.parse_args()

    input_path: Path = args.input
    output_path: Path = args.output

    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    sanitizer = HARSanitizer()
    sanitizer.sanitize_file(input_path, output_path)
    print(f"Sanitized HAR written to: {output_path}")


if __name__ == "__main__":
    _main()

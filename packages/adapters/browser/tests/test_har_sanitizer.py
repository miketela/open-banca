"""Tests for HARSanitizer — credentials and PII redaction.

All tests are pure data tests: no browser, no network, no Playwright required.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from open_banca_browser.har.sanitize import REDACTED, HARSanitizer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_har(entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Minimal HAR 1.2 document."""
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
    request_headers: list[dict[str, str]] | None = None,
    request_cookies: list[dict[str, Any]] | None = None,
    request_qs: list[dict[str, str]] | None = None,
    post_data: dict[str, Any] | None = None,
    response_headers: list[dict[str, str]] | None = None,
    response_cookies: list[dict[str, Any]] | None = None,
    response_body: str | None = None,
    response_mime: str = "application/json",
    response_encoding: str = "",
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": request_headers or [],
        "cookies": request_cookies or [],
        "queryString": request_qs or [],
    }
    if post_data:
        request["postData"] = post_data

    response: dict[str, Any] = {
        "status": 200,
        "headers": response_headers or [],
        "cookies": response_cookies or [],
        "content": {
            "mimeType": response_mime,
            "size": len(response_body or ""),
        },
    }
    if response_body is not None:
        response["content"]["text"] = response_body
    if response_encoding:
        response["content"]["encoding"] = response_encoding

    return {"request": request, "response": response}


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


class TestSanitizerStripsAuthHeaders:
    def test_authorization_bearer_redacted(self) -> None:
        entry = _entry(
            request_headers=[{"name": "Authorization", "value": "Bearer xyz-secret-token"}]
        )
        har = _make_har([entry])
        sanitizer = HARSanitizer()
        result = sanitizer.sanitize(har)
        auth_header = result["log"]["entries"][0]["request"]["headers"][0]
        assert auth_header["value"] == REDACTED

    def test_cookie_header_redacted(self) -> None:
        entry = _entry(request_headers=[{"name": "Cookie", "value": "session=abc123; user=mike"}])
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        assert result["log"]["entries"][0]["request"]["headers"][0]["value"] == REDACTED

    def test_set_cookie_response_header_redacted(self) -> None:
        entry = _entry(
            response_headers=[{"name": "Set-Cookie", "value": "sid=supersecret; HttpOnly"}]
        )
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        assert result["log"]["entries"][0]["response"]["headers"][0]["value"] == REDACTED

    def test_x_csrf_token_redacted(self) -> None:
        entry = _entry(request_headers=[{"name": "X-CSRF-Token", "value": "csrf-val-999"}])
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        assert result["log"]["entries"][0]["request"]["headers"][0]["value"] == REDACTED

    def test_safe_header_preserved(self) -> None:
        entry = _entry(request_headers=[{"name": "Content-Type", "value": "application/json"}])
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        assert result["log"]["entries"][0]["request"]["headers"][0]["value"] == "application/json"


# ---------------------------------------------------------------------------
# Body: password field
# ---------------------------------------------------------------------------


class TestSanitizerStripsBodyPasswords:
    def test_json_password_field_request(self) -> None:
        body = json.dumps({"username": "user@example.com", "password": "hunter2"})
        entry = _entry(post_data={"mimeType": "application/json", "text": body})
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        post_text = result["log"]["entries"][0]["request"]["postData"]["text"]
        assert "hunter2" not in post_text
        assert REDACTED in post_text

    def test_json_token_field_redacted(self) -> None:
        body = json.dumps({"token": "my-api-token-123"})
        entry = _entry(post_data={"mimeType": "application/json", "text": body})
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        post_text = result["log"]["entries"][0]["request"]["postData"]["text"]
        assert "my-api-token-123" not in post_text

    def test_form_encoded_password_redacted(self) -> None:
        body = "username=mike&password=s3cr3t&remember=true"
        entry = _entry(post_data={"mimeType": "application/x-www-form-urlencoded", "text": body})
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        post_text = result["log"]["entries"][0]["request"]["postData"]["text"]
        assert "s3cr3t" not in post_text
        assert REDACTED in post_text

    def test_postdata_params_password_redacted(self) -> None:
        entry = _entry(
            post_data={
                "mimeType": "application/x-www-form-urlencoded",
                "params": [{"name": "password", "value": "my-pass"}],
            }
        )
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        param = result["log"]["entries"][0]["request"]["postData"]["params"][0]
        assert param["value"] == REDACTED


# ---------------------------------------------------------------------------
# Response body
# ---------------------------------------------------------------------------


class TestSanitizerResponseBody:
    def test_secret_canary_in_response_body_redacted(self) -> None:
        body = json.dumps({"message": "ok", "debug_token": "SECRET_CANARY_VALUE"})
        entry = _entry(response_body=body)
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        text = result["log"]["entries"][0]["response"]["content"]["text"]
        assert "SECRET_CANARY_VALUE" not in text
        assert REDACTED in text

    def test_json_token_in_response_body_redacted(self) -> None:
        body = json.dumps({"access_token": "eyJhbGciOiJSUzI1"})
        entry = _entry(response_body=body)
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        text = result["log"]["entries"][0]["response"]["content"]["text"]
        assert "eyJhbGciOiJSUzI1" not in text


# ---------------------------------------------------------------------------
# Secret canary
# ---------------------------------------------------------------------------


class TestSanitizerStripsCredentials:
    def test_secret_canary_in_request_body_stripped(self) -> None:
        body = "credential=SECRET_CANARY_VALUE&other=value"
        entry = _entry(post_data={"mimeType": "text/plain", "text": body})
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        post_text = result["log"]["entries"][0]["request"]["postData"]["text"]
        assert "SECRET_CANARY_VALUE" not in post_text

    def test_secret_canary_not_in_serialized_output(self) -> None:
        """Serialize to JSON and grep for canary — must return zero hits."""
        body = json.dumps({"password": "SECRET_CANARY_VALUE"})
        entry = _entry(post_data={"mimeType": "application/json", "text": body})
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        serialized = json.dumps(result)
        assert "SECRET_CANARY_VALUE" not in serialized


# ---------------------------------------------------------------------------
# PII canary
# ---------------------------------------------------------------------------


class TestSanitizerStripsPii:
    def test_pii_canary_name_in_response_body_stripped(self) -> None:
        body = json.dumps({"holder": "PII_CANARY_NAME", "balance": "1000.00"})
        entry = _entry(response_body=body)
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        text = result["log"]["entries"][0]["response"]["content"]["text"]
        assert "PII_CANARY_NAME" not in text
        assert REDACTED in text

    def test_pii_canary_account_in_request_body_stripped(self) -> None:
        body = json.dumps({"account": "PII_CANARY_ACCOUNT_NUMBER"})
        entry = _entry(post_data={"mimeType": "application/json", "text": body})
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        post_text = result["log"]["entries"][0]["request"]["postData"]["text"]
        assert "PII_CANARY_ACCOUNT_NUMBER" not in post_text

    def test_pii_canary_not_in_serialized_output(self) -> None:
        body = json.dumps({"name": "PII_CANARY_FULLNAME", "id": "PII_CANARY_ID"})
        entry = _entry(response_body=body)
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        serialized = json.dumps(result)
        assert "PII_CANARY_" not in serialized


# ---------------------------------------------------------------------------
# URL query params
# ---------------------------------------------------------------------------


class TestSanitizerUrlQuery:
    def test_token_param_in_url_redacted(self) -> None:
        entry = _entry(url="https://bank.example.com/api?token=abc123&page=1")
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        url = result["log"]["entries"][0]["request"]["url"]
        assert "abc123" not in url
        assert "token=REDACTED" in url

    def test_session_param_in_url_redacted(self) -> None:
        entry = _entry(url="https://bank.example.com/app?session=sess-xyz&user=mike")
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        url = result["log"]["entries"][0]["request"]["url"]
        assert "sess-xyz" not in url

    def test_safe_param_preserved(self) -> None:
        entry = _entry(url="https://bank.example.com/api?page=2&limit=20")
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        url = result["log"]["entries"][0]["request"]["url"]
        assert "page=2" in url
        assert "limit=20" in url

    def test_querystring_array_token_redacted(self) -> None:
        entry = _entry(
            url="https://bank.example.com/api?token=abc",
            request_qs=[{"name": "token", "value": "abc"}],
        )
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        qs = result["log"]["entries"][0]["request"]["queryString"]
        assert qs[0]["value"] == REDACTED

    def test_querystring_safe_param_preserved(self) -> None:
        entry = _entry(
            url="https://bank.example.com/api?format=json",
            request_qs=[{"name": "format", "value": "json"}],
        )
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        qs = result["log"]["entries"][0]["request"]["queryString"]
        assert qs[0]["value"] == "json"


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------


class TestSanitizerCookies:
    def test_request_cookies_redacted(self) -> None:
        entry = _entry(request_cookies=[{"name": "sid", "value": "session-secret"}])
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        cookie = result["log"]["entries"][0]["request"]["cookies"][0]
        assert cookie["value"] == REDACTED

    def test_response_cookies_redacted(self) -> None:
        entry = _entry(response_cookies=[{"name": "auth", "value": "auth-cookie-val"}])
        har = _make_har([entry])
        result = HARSanitizer().sanitize(har)
        cookie = result["log"]["entries"][0]["response"]["cookies"][0]
        assert cookie["value"] == REDACTED


# ---------------------------------------------------------------------------
# Original not mutated
# ---------------------------------------------------------------------------


class TestSanitizerImmutability:
    def test_original_har_not_mutated(self) -> None:
        entry = _entry(request_headers=[{"name": "Authorization", "value": "Bearer secret"}])
        har = _make_har([entry])
        original = deepcopy(har)
        HARSanitizer().sanitize(har)
        assert har == original

"""Tests for open_banca_observability.langfuse_client — optional Langfuse factory.

test_langfuse_optional: if LANGFUSE_HOST unset → no errors, no export.
"""

from __future__ import annotations

import pytest

from open_banca_observability.langfuse_client import flush_langfuse, get_langfuse_client


class TestLangfuseOptional:
    """test_langfuse_optional: if LANGFUSE_HOST unset → no errors, no export."""

    def test_returns_none_when_host_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_langfuse_client() returns None when LANGFUSE_HOST is unset."""
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        client = get_langfuse_client()
        assert client is None

    def test_returns_none_when_host_is_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_langfuse_client() returns None when LANGFUSE_HOST is an empty string."""
        monkeypatch.setenv("LANGFUSE_HOST", "")
        client = get_langfuse_client()
        assert client is None

    def test_no_import_error_when_langfuse_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even if langfuse package is not installed, get_langfuse_client is importable
        and returns None when host is not set."""
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        # This should not raise ImportError or any other exception.
        client = get_langfuse_client()
        assert client is None

    def test_flush_none_is_safe(self) -> None:
        """flush_langfuse(None) does not raise."""
        flush_langfuse(None)  # must not raise

    def test_flush_none_client_silently_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Full round-trip with disabled Langfuse: get → flush is safe."""
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        client = get_langfuse_client()
        flush_langfuse(client)  # client is None, must not raise

    def test_langfuse_package_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When LANGFUSE_HOST is set but package unavailable, returns None gracefully."""
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        # Simulate langfuse not installed by patching the import inside the module.
        import builtins

        real_import = builtins.__import__

        def _mock_import(name: str, *args: object, **kwargs: object) -> object:  # type: ignore[misc]
            if name == "langfuse":
                raise ImportError("langfuse not installed (mocked)")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", _mock_import)
        client = get_langfuse_client()
        assert client is None

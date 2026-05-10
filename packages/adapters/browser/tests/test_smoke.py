"""Smoke test: verifies the open_banca_browser package is importable."""


def test_open_banca_browser_imports() -> None:
    import open_banca_browser  # noqa: F401

    assert open_banca_browser.__name__ == "open_banca_browser"

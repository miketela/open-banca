"""Smoke test: verifies the open_banca_api package is importable."""


def test_open_banca_api_imports() -> None:
    import open_banca_api  # noqa: F401

    assert open_banca_api.__name__ == "open_banca_api"

"""Smoke test: verifies the open_banca_application package is importable."""


def test_open_banca_application_imports() -> None:
    import open_banca_application  # noqa: F401

    assert open_banca_application.__name__ == "open_banca_application"

"""Smoke test: verifies the open_banca_parsing package is importable."""


def test_open_banca_parsing_imports() -> None:
    import open_banca_parsing  # noqa: F401

    assert open_banca_parsing.__name__ == "open_banca_parsing"

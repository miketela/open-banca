"""Smoke test: verifies the open_banca_llm package is importable."""


def test_open_banca_llm_imports() -> None:
    import open_banca_llm  # noqa: F401, PLC0415

    assert open_banca_llm.__name__ == "open_banca_llm"

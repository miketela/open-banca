"""Smoke test: verifies the open_banca_banco_general package is importable."""


def test_open_banca_banco_general_imports() -> None:
    import open_banca_banco_general  # noqa: F401

    assert open_banca_banco_general.__name__ == "open_banca_banco_general"

"""Smoke test: verifies the open_banca_schemas package is importable."""


def test_open_banca_schemas_imports() -> None:
    import open_banca_schemas  # noqa: F401

    assert open_banca_schemas.__name__ == "open_banca_schemas"

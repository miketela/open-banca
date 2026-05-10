"""Smoke test: verifies the open_banca_storage package is importable."""


def test_open_banca_storage_imports() -> None:
    import open_banca_storage  # noqa: F401

    assert open_banca_storage.__name__ == "open_banca_storage"

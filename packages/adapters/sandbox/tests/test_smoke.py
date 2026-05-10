"""Smoke test: verifies the open_banca_sandbox package is importable."""


def test_open_banca_sandbox_imports() -> None:
    import open_banca_sandbox  # noqa: F401

    assert open_banca_sandbox.__name__ == "open_banca_sandbox"

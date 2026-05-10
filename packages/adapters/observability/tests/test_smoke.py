"""Smoke test: verifies the open_banca_observability package is importable."""


def test_open_banca_observability_imports() -> None:
    import open_banca_observability  # noqa: F401

    assert open_banca_observability.__name__ == "open_banca_observability"

"""Smoke test: verifies the domain package is importable."""


def test_domain_imports() -> None:
    import open_banca_domain  # noqa: F401

    assert open_banca_domain.__name__ == "open_banca_domain"

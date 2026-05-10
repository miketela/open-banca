"""Smoke test: verifies the open_banca_orchestrator package is importable."""


def test_open_banca_orchestrator_imports() -> None:
    import open_banca_orchestrator  # noqa: F401, PLC0415

    assert open_banca_orchestrator.__name__ == "open_banca_orchestrator"

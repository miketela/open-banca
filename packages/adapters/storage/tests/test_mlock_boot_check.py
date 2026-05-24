"""Tests for boot-time memory-protection checks.

TDD coverage:
- Core dump check raises BootSecurityError when RLIMIT_CORE != 0.
- Linux swap check raises BootSecurityError when swappiness != 0.
- Linux swap check raises BootSecurityError when active swap partitions exist.
- macOS: swap checks are skipped (warning only); no exception raised.
- run_boot_check passes when all conditions are met (mock).
"""

from __future__ import annotations

import sys
from unittest.mock import mock_open, patch

import pytest

from open_banca_storage.mlock_boot_check import (
    BootSecurityError,
    _check_core_dumps_disabled,
    _check_linux_swap,
    run_boot_check,
)

# ── Core dump check ───────────────────────────────────────────────────────────


def test_core_dumps_check_passes_when_zero() -> None:
    """_check_core_dumps_disabled should not raise when RLIMIT_CORE == 0."""
    import resource

    with patch.object(resource, "getrlimit", return_value=(0, 0)):
        _check_core_dumps_disabled()  # must not raise


def test_core_dumps_check_fails_when_nonzero() -> None:
    """_check_core_dumps_disabled should raise BootSecurityError when soft limit > 0."""
    import resource

    with patch.object(resource, "getrlimit", return_value=(1024, 1024)):
        with pytest.raises(BootSecurityError, match="RLIMIT_CORE"):
            _check_core_dumps_disabled()


# ── Linux swap checks ─────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only test")
def test_linux_swap_check_fails_when_swappiness_nonzero() -> None:
    """On Linux: raises BootSecurityError when swappiness != 0."""
    with patch("builtins.open", mock_open(read_data="60\n")):
        with pytest.raises(BootSecurityError, match="swappiness"):
            _check_linux_swap()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only test")
def test_linux_swap_check_fails_when_active_swaps() -> None:
    """On Linux: raises BootSecurityError when active swap partitions exist."""
    proc_swaps_content = (
        "Filename\t\t\tType\t\tSize\tUsed\tPriority\n/dev/sda2\t\t\tpartition\t2097148\t0\t-2\n"
    )

    def mock_open_router(file: str, *a: object, **kw: object) -> object:
        if "swappiness" in file:
            return mock_open(read_data="0\n")()
        if "swaps" in file:
            return mock_open(read_data=proc_swaps_content)()
        raise FileNotFoundError(file)

    with patch("builtins.open", side_effect=mock_open_router):
        with pytest.raises(BootSecurityError, match="swap"):
            _check_linux_swap()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only test")
def test_linux_swap_check_passes_when_no_swap() -> None:
    """On Linux: no exception when swappiness=0 and no active swap."""
    proc_swaps_content = "Filename\t\t\tType\t\tSize\tUsed\tPriority\n"

    def mock_open_router(file: str, *a: object, **kw: object) -> object:
        if "swappiness" in file:
            return mock_open(read_data="0\n")()
        if "swaps" in file:
            return mock_open(read_data=proc_swaps_content)()
        raise FileNotFoundError(file)

    with patch("builtins.open", side_effect=mock_open_router):
        _check_linux_swap()  # must not raise


# ── run_boot_check integration ────────────────────────────────────────────────


def test_run_boot_check_passes_all_mocked() -> None:
    """run_boot_check should not raise when all sub-checks are mocked to pass."""
    import resource

    with patch.object(resource, "getrlimit", return_value=(0, 0)):
        with patch("open_banca_storage.mlock_boot_check._check_linux_swap"):
            # On Linux this will call the patched swap check; on macOS it's a warning.
            run_boot_check()  # must not raise


def test_run_boot_check_propagates_core_dump_error() -> None:
    """run_boot_check must propagate BootSecurityError from core dump check."""
    import resource

    with patch.object(resource, "getrlimit", return_value=(4096, 4096)):
        with pytest.raises(BootSecurityError, match="RLIMIT_CORE"):
            run_boot_check(enforce_core_dumps=True)


@pytest.mark.skipif(sys.platform == "linux", reason="macOS-specific behaviour")
def test_run_boot_check_skips_swap_on_macos() -> None:
    """On macOS, run_boot_check should not raise for swap (warning only)."""
    import resource

    with patch.object(resource, "getrlimit", return_value=(0, 0)):
        run_boot_check()  # must not raise on macOS regardless of swap state

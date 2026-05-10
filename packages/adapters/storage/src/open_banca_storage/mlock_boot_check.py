"""Boot-time memory-protection checks.

Per ADR-0008 amendment and ``docs/04-security/secrets-at-rest.md``:

- On **Linux**: the API refuses to start if swap is active
  (``/proc/sys/vm/swappiness`` != 0 OR ``/proc/swaps`` shows active swap
  partitions).  Raises ``BootSecurityError`` which must propagate to the
  process supervisor so the operator knows to fix the host.

- On **macOS** (dev only): swap checks are skipped; a warning is logged.
  macOS compressed memory is not equivalent to disk swap but the OS manages
  it transparently, so enforcement is not feasible without root privs.

- On **all platforms**: core dump limit (RLIMIT_CORE) must be 0.  A non-zero
  limit means plaintext key material could end up in a core file.  This is
  enforced on both Linux and macOS.

Call ``run_boot_check()`` once at application startup before instantiating
``SecretVault`` or ``ConnectionPool``.
"""
from __future__ import annotations

import logging
import resource
import sys

logger = logging.getLogger(__name__)


class BootSecurityError(RuntimeError):
    """Raised when a hard memory-security requirement is not satisfied at boot."""


def _check_core_dumps_disabled() -> None:
    """Verify RLIMIT_CORE is 0 (core dumps disabled)."""
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_CORE)
    except (OSError, ValueError) as exc:
        raise BootSecurityError(f"Cannot read RLIMIT_CORE: {exc}") from exc
    if soft != 0:
        raise BootSecurityError(
            f"RLIMIT_CORE is {soft} (non-zero). Core dumps must be disabled to prevent "
            "key material leaking to disk. Run: ulimit -c 0"
        )


def _check_linux_swap() -> None:
    """On Linux: verify swap is fully disabled."""
    # /proc/sys/vm/swappiness == 0 is necessary but not sufficient;
    # also check /proc/swaps for active swap devices.
    try:
        swappiness_path = "/proc/sys/vm/swappiness"
        with open(swappiness_path) as f:
            swappiness = int(f.read().strip())
        if swappiness != 0:
            raise BootSecurityError(
                f"/proc/sys/vm/swappiness={swappiness} (must be 0). "
                "Swap must be disabled to prevent master passphrase from being written to disk. "
                "Run: sysctl -w vm.swappiness=0 && swapoff -a"
            )
    except FileNotFoundError:
        # /proc not available — unexpected on Linux, skip
        logger.warning("mlock_boot_check: /proc/sys/vm/swappiness not found; skipping check.")
        return

    # Check for active swap partitions in /proc/swaps
    try:
        with open("/proc/swaps") as f:
            lines = f.readlines()
        # Header line is always present; active swaps appear as data rows
        active_swaps = [ln for ln in lines[1:] if ln.strip()]
        if active_swaps:
            swap_summary = ", ".join(ln.split()[0] for ln in active_swaps)
            raise BootSecurityError(
                f"Active swap partitions detected: {swap_summary}. "
                "Swap must be fully disabled to protect key material. Run: swapoff -a"
            )
    except FileNotFoundError:
        logger.warning("mlock_boot_check: /proc/swaps not found; skipping active-swap check.")


def run_boot_check(*, enforce_core_dumps: bool = True) -> None:
    """Run all boot-time memory-security checks.

    Args:
        enforce_core_dumps: If True (default), raise ``BootSecurityError``
            when RLIMIT_CORE != 0.  Set to False only in test environments
            where core-dump control is unavailable (e.g. containers with no
            capabilities).

    Raises:
        BootSecurityError: If a hard requirement fails on a platform where it
            is enforced.  On macOS only a warning is logged for swap checks.
    """
    if enforce_core_dumps:
        try:
            _check_core_dumps_disabled()
        except BootSecurityError:
            raise
        except Exception as exc:
            # Unexpected error reading RLIMIT — warn but don't crash on macOS
            if sys.platform == "linux":
                raise BootSecurityError(f"Core dump check failed unexpectedly: {exc}") from exc
            logger.warning("mlock_boot_check: core dump check raised %s (non-Linux, ignoring)", exc)

    if sys.platform == "linux":
        _check_linux_swap()
    else:
        logger.warning(
            "mlock_boot_check: running on %s (not Linux) — swap checks skipped. "
            "In production, run on Linux with swap disabled.",
            sys.platform,
        )

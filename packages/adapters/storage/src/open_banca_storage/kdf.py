"""Argon2id key-derivation functions for the SQLCipher vault.

Two distinct KDF callsites per ADR-0008:

1. **DB-level** (``Argon2idKeyDerivation``): derives the SQLCipher PRAGMA key
   from the master passphrase.  Parameters: time_cost=3, memory_cost=131072
   (128 MiB), parallelism=4, hash_len=32.  Produces a hex-encoded key for
   ``PRAGMA key = "x'<hex>'"`` mode.

2. **Per-row** (``derive_row_key``): derives the AES-GCM key for each
   credential row.  Parameters: time_cost=3, memory_cost=262144 (256 MiB),
   parallelism=4, hash_len=32.  Uses a per-row random 16-byte salt.

ADR-0008 constrains:
    DB-level:  mem=128 MiB, iters=3
    Per-row:   mem=256 MiB, iters=3, parallelism=4

OWASP cheatsheet 2024 minimum: time_cost>=3, memory>=64 MiB.  The project
spec exceeds the OWASP floor deliberately.
"""
from __future__ import annotations

import ctypes
import sys

from argon2.low_level import Type, hash_secret_raw

# ── DB-level KDF params (ADR-0008 §Decisión, DB row) ────────────────────────
_DB_TIME_COST = 3
_DB_MEMORY_COST = 131_072  # 128 MiB
_DB_PARALLELISM = 4
_DB_HASH_LEN = 32
_DB_SALT = b"open_banca_db_v1"  # constant salt for DB-level KDF; secret is the passphrase

# ── Per-row KDF params (ADR-0008 §Cadena de cifrado) ─────────────────────────
_ROW_TIME_COST = 3
_ROW_MEMORY_COST = 262_144  # 256 MiB
_ROW_PARALLELISM = 4
_ROW_HASH_LEN = 32


def _wipe(buf: bytearray) -> None:
    """Overwrite *buf* with zeros in-place (best-effort zeroize).

    Python's memory model does not guarantee that the underlying allocation is
    immediately freed, but an explicit zero-fill before GC is the best we can
    do without C extensions.  For mlock'd buffers this is combined with
    ctypes mlock below.
    """
    for i in range(len(buf)):
        buf[i] = 0


def _try_mlock(buf: bytearray) -> bool:
    """Attempt to call mlock(2) on the buffer's backing memory.

    Returns True on success, False if the syscall fails (EPERM / ENOMEM) or
    the platform does not support it.  On macOS this is a best-effort dev aid;
    the boot-check enforcer only runs on Linux.
    """
    try:
        libc = ctypes.cdll.LoadLibrary("")  # "" loads the current process (libc) on POSIX
        ptr = (ctypes.c_uint8 * len(buf)).from_buffer(buf)
        result: int = libc.mlock(ptr, ctypes.c_size_t(len(buf)))  # type: ignore[attr-defined]
        return result == 0
    except Exception:
        return False


def _try_madvise_dontdump(buf: bytearray) -> bool:
    """Call madvise(MADV_DONTDUMP) to exclude the buffer from core dumps.

    Linux-only (MADV_DONTDUMP = 16).  No-op on macOS.
    """
    if sys.platform != "linux":
        return False
    try:
        libc = ctypes.cdll.LoadLibrary("")  # "" loads the current process (libc) on POSIX
        ptr = (ctypes.c_uint8 * len(buf)).from_buffer(buf)
        MADV_DONTDUMP = 16
        result: int = libc.madvise(ptr, ctypes.c_size_t(len(buf)), ctypes.c_int(MADV_DONTDUMP))  # type: ignore[attr-defined]
        return result == 0
    except Exception:
        return False


class Argon2idKeyDerivation:
    """SQLCipher PRAGMA key derivation using Argon2id.

    Implements the ``KeyDerivation`` protocol from ``connection.py``.

    Replaces ``PassthroughKeyDerivation`` for production use.  Uses a
    constant per-deployment salt bound to the passphrase (the passphrase IS
    the secret).  Produces a 64-character hex string used as the SQLCipher
    PRAGMA key passphrase.

    ``ConnectionPool._open()`` wraps the returned value in single quotes::

        PRAGMA key = '<returned_value>';

    The hex string is used directly as the passphrase argument — SQLCipher
    applies its own key derivation (PBKDF2) on top, making the 256-bit
    Argon2id output the effective entropy source.

    Params (DB-level per ADR-0008):
        time_cost   = 3
        memory_cost = 131072 (128 MiB)
        parallelism = 4
        hash_len    = 32
    """

    def derive_db_key(self, passphrase: str) -> str:
        """Derive and return the SQLCipher PRAGMA key from *passphrase*.

        Returns a 64-character lowercase hex string for use as a high-entropy
        SQLCipher passphrase::

            PRAGMA key = '<64-char-hex>';

        The Argon2id output provides 256 bits of entropy as the passphrase
        so that the master passphrase never directly touches SQLCipher's
        PBKDF2 derivation.
        """
        secret = passphrase.encode("utf-8")
        raw: bytes = hash_secret_raw(
            secret=secret,
            salt=_DB_SALT,
            time_cost=_DB_TIME_COST,
            memory_cost=_DB_MEMORY_COST,
            parallelism=_DB_PARALLELISM,
            hash_len=_DB_HASH_LEN,
            type=Type.ID,
        )
        return raw.hex()


def derive_row_key(passphrase_bytes: bytes, salt: bytes) -> bytearray:
    """Derive a 32-byte AES-GCM row key from *passphrase_bytes* and *salt*.

    Uses per-row KDF params per ADR-0008 (mem=256 MiB, iters=3, p=4).

    The returned ``bytearray`` is mlock'd and madvise'd if the OS allows it.
    Callers MUST call ``wipe_row_key`` when done.

    Args:
        passphrase_bytes: UTF-8-encoded master passphrase as bytes.
        salt: 16-byte random salt for this specific row.

    Returns:
        32-byte ``bytearray`` suitable for ``AESGCM(key=bytes(row_key))``.
    """
    raw: bytes = hash_secret_raw(
        secret=passphrase_bytes,
        salt=salt,
        time_cost=_ROW_TIME_COST,
        memory_cost=_ROW_MEMORY_COST,
        parallelism=_ROW_PARALLELISM,
        hash_len=_ROW_HASH_LEN,
        type=Type.ID,
    )
    buf = bytearray(raw)
    _try_mlock(buf)
    _try_madvise_dontdump(buf)
    return buf


def wipe_row_key(key: bytearray) -> None:
    """Zeroize a derived row key buffer in-place.

    Should be called in a ``finally`` block immediately after use.
    """
    _wipe(key)

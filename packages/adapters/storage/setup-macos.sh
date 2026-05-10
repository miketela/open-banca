#!/usr/bin/env bash
# setup-macos.sh — one-time setup for open-banca-storage on macOS (arm64/x86).
#
# pysqlcipher3 requires libsqlcipher headers to compile.  This script installs
# the dependency and sets the build flags needed for `uv sync`.
#
# Usage (from the monorepo root or this directory):
#   bash packages/adapters/storage/setup-macos.sh
#
# On Linux (CI), sqlcipher3-binary ships a static manylinux wheel and this
# script is not needed — bare `uv sync --all-packages` works.

set -euo pipefail

echo "[setup-macos] Checking for sqlcipher..."
if ! brew list sqlcipher &>/dev/null; then
    echo "[setup-macos] Installing sqlcipher via Homebrew..."
    brew install sqlcipher
else
    echo "[setup-macos] sqlcipher already installed ($(brew list --versions sqlcipher))"
fi

SQLCIPHER_PATH="$(brew --prefix sqlcipher)"
echo "[setup-macos] SQLCipher prefix: $SQLCIPHER_PATH"

echo "[setup-macos] Running uv sync with build flags for pysqlcipher3..."
CFLAGS="-I${SQLCIPHER_PATH}/include" \
LDFLAGS="-L${SQLCIPHER_PATH}/lib -lsqlcipher" \
CPPFLAGS="-I${SQLCIPHER_PATH}/include" \
    uv sync --all-packages

echo "[setup-macos] Done. Verify with:"
echo "  uv run python -c \"from pysqlcipher3 import dbapi2 as s; print(s.connect(':memory:').execute('PRAGMA cipher_version').fetchone())\""

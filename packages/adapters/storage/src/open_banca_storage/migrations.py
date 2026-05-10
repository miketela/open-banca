"""Simple sequential SQL migration runner.

Migrations are plain ``.sql`` files in the ``migrations/`` package directory,
named ``NNN_description.sql`` (e.g. ``001_initial.sql``).  They are applied in
lexicographic order; each migration is idempotent because every ``CREATE``
statement uses ``IF NOT EXISTS``.

A ``schema_migrations`` table tracks which files have been applied so that
re-running ``migrate()`` is a no-op for already-applied migrations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from open_banca_storage.connection import Connection

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT    PRIMARY KEY,
    applied_at  TEXT    NOT NULL
);
"""


def migrate(conn: Connection) -> list[str]:
    """Apply any pending migrations and return list of applied filenames.

    Args:
        conn: An open SQLCipher connection with ``PRAGMA key`` already applied.

    Returns:
        List of migration filenames that were applied in this call.
    """
    conn.execute(_CREATE_MIGRATIONS_TABLE)
    conn.commit()

    applied_rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    applied: set[str] = {row[0] for row in applied_rows}

    sql_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    newly_applied: list[str] = []

    for sql_file in sql_files:
        filename = sql_file.name
        if filename in applied:
            logger.debug("Migration already applied: %s", filename)
            continue

        logger.info("Applying migration: %s", filename)
        sql_text = sql_file.read_text(encoding="utf-8")
        conn.executescript(sql_text)

        # Record the migration within its own statement (executescript commits).
        conn.execute(
            "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (filename,),
        )
        conn.commit()
        newly_applied.append(filename)
        logger.info("Migration applied: %s", filename)

    return newly_applied

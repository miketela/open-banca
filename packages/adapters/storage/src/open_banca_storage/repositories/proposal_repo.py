"""ProposalRepository — SQL operations for remap_proposals not in SqliteJobStore.

SqliteJobStore already provides ``save_proposal`` and ``load_proposal``.
This class adds the mutation helpers (mark_applied, mark_rejected),
list helpers, and the TTL expire sweep — all operating on the same
``remap_proposals`` table created by migration 002.

Architectural note (task-21):
  The docs say "apply corre dentro de RemapBankWorkflow (no en API)".
  v1 implements HITL-only: the approve endpoint applies the patch to disk
  and commits it, then signals the workflow.  The workflow's apply logic
  (which mirrors this) is deferred to v1.x along with auto-apply.
  This deviation is intentional and tracked as a v1.x follow-up.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus
from open_banca_storage.connection import Connection

logger = logging.getLogger(__name__)

_PROPOSAL_SELECT = """
    SELECT id, bank, breakage_id, judge_decision, confidence, risk,
           patch_diff, status, expires_at
    FROM remap_proposals
"""


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _row_to_proposal(row: tuple[Any, ...]) -> RemapProposal:
    (id_, bank, breakage_id, judge_decision, confidence, risk, patch_diff, status, expires_at) = row
    return RemapProposal(
        id=id_,
        bank=bank,
        breakage_id=breakage_id,
        judge_decision=judge_decision,
        confidence=confidence,
        risk=risk,
        patch_diff=patch_diff,
        status=RemapStatus(status),
        expires_at=datetime.fromisoformat(expires_at),
    )


class ProposalRepository:
    """SQL helpers for remap proposal lifecycle management.

    Takes an open SQLCipher connection with schema already migrated.
    All methods are synchronous (matching the rest of the storage layer).

    Args:
        conn: Open SQLCipher connection.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_proposal(self, proposal_id: str) -> RemapProposal | None:
        """Return a proposal by ID, or None if not found."""
        row = self._conn.execute(
            _PROPOSAL_SELECT + "WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        return _row_to_proposal(row) if row is not None else None

    def list_pending(self) -> list[RemapProposal]:
        """Return all proposals with status=pending."""
        rows = self._conn.execute(
            _PROPOSAL_SELECT + "WHERE status = ?",
            (RemapStatus.PENDING.value,),
        ).fetchall()
        return [_row_to_proposal(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────────

    def save_proposal(self, proposal: RemapProposal) -> None:
        """Insert or update a remap proposal (upsert on id)."""
        self._conn.execute(
            """
            INSERT INTO remap_proposals (
                id, bank, breakage_id, judge_decision, confidence, risk,
                patch_diff, status, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status     = excluded.status,
                expires_at = excluded.expires_at
            """,
            (
                proposal.id,
                proposal.bank,
                proposal.breakage_id,
                proposal.judge_decision,
                proposal.confidence,
                proposal.risk,
                proposal.patch_diff,
                str(proposal.status),
                proposal.expires_at.isoformat(),
                _utcnow_iso(),
            ),
        )
        self._conn.commit()

    def mark_applied(self, proposal_id: str) -> None:
        """Update status to APPLIED.  No-op if proposal not found."""
        self._conn.execute(
            "UPDATE remap_proposals SET status = ? WHERE id = ?",
            (RemapStatus.APPLIED.value, proposal_id),
        )
        self._conn.commit()
        logger.debug("proposal %s marked applied", proposal_id)

    def mark_rejected(self, proposal_id: str, reason: str | None = None) -> None:
        """Update status to REJECTED.  No-op if proposal not found.

        Args:
            proposal_id: UUID of the proposal.
            reason: Optional human-readable rejection reason (stored in judge_decision).
        """
        if reason is not None:
            self._conn.execute(
                "UPDATE remap_proposals SET status = ?, judge_decision = ? WHERE id = ?",
                (RemapStatus.REJECTED.value, reason, proposal_id),
            )
        else:
            self._conn.execute(
                "UPDATE remap_proposals SET status = ? WHERE id = ?",
                (RemapStatus.REJECTED.value, proposal_id),
            )
        self._conn.commit()
        logger.debug("proposal %s marked rejected (reason=%s)", proposal_id, reason)

    # ── TTL sweep ─────────────────────────────────────────────────────────────

    def expire_old(self) -> list[str]:
        """Mark all PENDING proposals past their expires_at as EXPIRED.

        Returns a list of proposal IDs that were transitioned to expired.
        Called by the hourly background task.
        """
        now = _utcnow_iso()
        rows = self._conn.execute(
            """
            SELECT id FROM remap_proposals
            WHERE status = ? AND expires_at < ?
            """,
            (RemapStatus.PENDING.value, now),
        ).fetchall()
        expired_ids: list[str] = [row[0] for row in rows]

        if expired_ids:
            placeholders = ",".join("?" * len(expired_ids))
            self._conn.execute(
                f"UPDATE remap_proposals SET status = ? WHERE id IN ({placeholders})",
                [RemapStatus.EXPIRED.value, *expired_ids],
            )
            self._conn.commit()
            logger.info("expire_old: marked %d proposals as expired", len(expired_ids))

        return expired_ids

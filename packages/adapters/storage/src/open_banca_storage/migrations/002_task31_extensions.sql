-- Migration 002: task-31 extensions
-- Adds idempotency_keys and remap_proposals tables needed for API wiring.

-- ── idempotency_keys ─────────────────────────────────────────────────────────
-- Stores Idempotency-Key → job_id mapping for POST /scrape.
-- Retained for 24 h (TTL enforced at application layer, not DB).
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT    PRIMARY KEY,         -- Idempotency-Key header value
    request_hash TEXT   NOT NULL,            -- SHA-256 of canonicalised request body
    job_id      TEXT    NOT NULL,            -- Existing job_id to return
    created_at  TEXT    NOT NULL             -- UTC ISO-8601; used for TTL pruning
);

CREATE INDEX IF NOT EXISTS idx_idem_created_at ON idempotency_keys (created_at);

-- ── remap_proposals ──────────────────────────────────────────────────────────
-- Persists remap proposals so approve/reject can query and update them.
CREATE TABLE IF NOT EXISTS remap_proposals (
    id              TEXT    PRIMARY KEY,     -- UUID
    bank            TEXT    NOT NULL,
    breakage_id     TEXT    NOT NULL,
    judge_decision  TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    risk            TEXT    NOT NULL,
    patch_diff      TEXT    NOT NULL,
    status          TEXT    NOT NULL,        -- RemapStatus enum value
    expires_at      TEXT    NOT NULL,        -- UTC ISO-8601
    created_at      TEXT    NOT NULL         -- UTC ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_proposals_bank ON remap_proposals (bank);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON remap_proposals (status);

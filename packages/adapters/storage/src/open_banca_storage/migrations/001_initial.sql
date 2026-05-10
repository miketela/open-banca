-- Migration 001: initial schema
-- All UUIDs stored as TEXT.
-- All timestamps stored as TEXT in UTC ISO-8601 (e.g. 2024-01-15T12:00:00Z).
-- All monetary amounts stored as TEXT (Decimal serialised via str()).
-- Do NOT store plaintext credentials here; the secret vault (Task #5)
-- handles AES-GCM encryption before INSERT into `credentials`.

-- ── credentials ──────────────────────────────────────────────────────────────
-- Bank credential vault.  ciphertext is AES-GCM encrypted by the secret vault
-- (Task #5).  This table holds the opaque envelope; decryption happens only
-- just-in-time in the orchestrator.
CREATE TABLE IF NOT EXISTS credentials (
    id          TEXT    PRIMARY KEY,     -- UUID
    bank        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    credential_ref TEXT NOT NULL UNIQUE, -- opaque reference returned to callers
    ciphertext  BLOB,                    -- AES-GCM ciphertext (Task #5 writes this)
    nonce       BLOB,                    -- 12-byte random nonce (Task #5)
    kdf_meta    TEXT,                    -- JSON: {salt, argon2id params} (Task #5)
    created_at  TEXT    NOT NULL         -- UTC ISO-8601
);

-- ── jobs ─────────────────────────────────────────────────────────────────────
-- One row per scrape execution lifecycle.
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT    PRIMARY KEY,  -- UUID
    status          TEXT    NOT NULL,     -- JobStatus enum value
    bank            TEXT    NOT NULL,
    credential_ref  TEXT    NOT NULL,
    mode            TEXT    NOT NULL,     -- JobMode enum value
    since_cursor    TEXT,
    error           TEXT,
    temporal_workflow_id TEXT,
    cost_usd        TEXT,                 -- Decimal as TEXT
    failure_reason  TEXT,
    created_at      TEXT    NOT NULL,     -- UTC ISO-8601
    updated_at      TEXT    NOT NULL      -- UTC ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_jobs_bank_status ON jobs (bank, status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at  ON jobs (created_at);

-- ── accounts ─────────────────────────────────────────────────────────────────
-- Discovered bank accounts.  Materialised last-known state.
CREATE TABLE IF NOT EXISTS accounts (
    id                  TEXT    PRIMARY KEY,  -- UUID
    bank                TEXT    NOT NULL,
    bank_account_id     TEXT    NOT NULL,
    account_type        TEXT    NOT NULL,     -- 'savings'|'checking'|'credit_card'
    currency            TEXT    NOT NULL,
    balance             TEXT    NOT NULL,     -- Decimal as TEXT
    -- credit_card only fields (NULL for other types)
    credit_limit        TEXT,
    available_credit    TEXT,
    cut_date            TEXT,
    min_payment         TEXT,
    payment_due_date    TEXT,
    statement_balance   TEXT,
    opened_at           TEXT    NOT NULL,     -- date ISO-8601 YYYY-MM-DD
    last_synced_at      TEXT,                 -- UTC ISO-8601
    metadata_json       TEXT,
    UNIQUE (bank, bank_account_id)
);

CREATE INDEX IF NOT EXISTS idx_accounts_bank ON accounts (bank);

-- ── transactions ─────────────────────────────────────────────────────────────
-- Bank movements persisted post-validation.  amount stored as TEXT (Decimal).
-- NOTE: job_id is nullable because JobStorePort.save_transaction() does not
-- carry a job context.  Use save_transaction_with_job() from the use-case
-- layer when the job_id is known.  FK on job_id is therefore not enforced
-- at the schema level; application layer enforces via save_transaction_with_job().
CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT    PRIMARY KEY,  -- UUID
    account_id          TEXT    NOT NULL      REFERENCES accounts (id),
    job_id              TEXT,                 -- nullable: NULL when called via port without job context
    posted_at           TEXT    NOT NULL,     -- UTC ISO-8601
    value_at            TEXT    NOT NULL,     -- UTC ISO-8601
    amount              TEXT    NOT NULL,     -- Decimal as TEXT
    currency            TEXT    NOT NULL,
    description         TEXT    NOT NULL,
    fingerprint_hash    TEXT    NOT NULL,
    embedded_id         TEXT,               -- bank-supplied stable ID when available
    transfer_match_id   TEXT,               -- UUID of matched counterpart tx
    created_at          TEXT    NOT NULL     -- UTC ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_txn_account_posted ON transactions (account_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_txn_job            ON transactions (job_id);
CREATE INDEX IF NOT EXISTS idx_txn_fingerprint    ON transactions (fingerprint_hash);

-- ── dedup_index ───────────────────────────────────────────────────────────────
-- O(1) lookup for 3-level deduplication:
--   level 0 = 'embedded'    (bank-supplied stable ID)
--   level 1 = 'fingerprint' (deterministic hash of canonical fields)
--   level 2 = 'fuzzy'       (transfer cross-account match)
CREATE TABLE IF NOT EXISTS dedup_index (
    fingerprint     TEXT    NOT NULL,
    account_id      TEXT    NOT NULL    REFERENCES accounts (id),
    transaction_id  TEXT    NOT NULL    REFERENCES transactions (id),
    level           TEXT    NOT NULL    CHECK (level IN ('embedded', 'fingerprint', 'fuzzy')),
    PRIMARY KEY (fingerprint, account_id)
);

CREATE INDEX IF NOT EXISTS idx_dedup_txn ON dedup_index (transaction_id);

-- ── audit_log ─────────────────────────────────────────────────────────────────
-- Append-only event log for critical actions.
-- Triggers enforce immutability: UPDATE and DELETE are ABORT-ed.
CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT    PRIMARY KEY,  -- UUID
    actor           TEXT    NOT NULL,
    action          TEXT    NOT NULL,     -- e.g. 'job.created', 'cred.read', 'otp.confirmed'
    entity_type     TEXT    NOT NULL,
    entity_id       TEXT    NOT NULL,
    at              TEXT    NOT NULL,     -- UTC ISO-8601
    metadata_json   TEXT
);

-- Enforce append-only semantics at the DB level.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE is not allowed');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE is not allowed');
END;

-- ── webhook_outbox ────────────────────────────────────────────────────────────
-- Transactional outbox for webhooks.  Delivery worker reads pending rows,
-- POSTs with HMAC-SHA256 signature, and marks delivered_at.
CREATE TABLE IF NOT EXISTS webhook_outbox (
    id              TEXT    PRIMARY KEY,  -- UUID
    job_id          TEXT    NOT NULL      REFERENCES jobs (id),
    event_type      TEXT    NOT NULL,
    payload_json    TEXT    NOT NULL,
    signature       TEXT    NOT NULL,     -- HMAC-SHA256 hex
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,                 -- UTC ISO-8601; NULL = immediate
    delivered_at    TEXT,                 -- UTC ISO-8601; NULL = pending
    created_at      TEXT    NOT NULL      -- UTC ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending ON webhook_outbox (delivered_at, next_retry_at)
    WHERE delivered_at IS NULL;

-- ── cursors ───────────────────────────────────────────────────────────────────
-- Per-bank incremental-sync cursor.
-- NOTE: The task specification names 7 tables; this is an 8th added to satisfy
-- JobStorePort.get_cursor(bank) / save_cursor(bank, cursor) from the domain
-- port.  The 7 named tables do not include a cursor store.
CREATE TABLE IF NOT EXISTS cursors (
    bank        TEXT    PRIMARY KEY,
    cursor      TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL   -- UTC ISO-8601
);

-- Migration 003: security_answers table for prompt_user cache (ADR-0021)
-- TTL hard 90 days per entry (configurable via BANCA_HUMAN_INPUT_TTL_DAYS).
-- cache_key = sha256(bank_id || ":" || credential_id || ":" || field_key || ":" || normalize(question_text))
-- encrypted_answer = AES-GCM ciphertext (same scheme as credentials table).

CREATE TABLE IF NOT EXISTS security_answers (
    credential_id   TEXT    NOT NULL,                           -- credential UUID (FK to credentials.id)
    question_hash   TEXT    NOT NULL,                           -- sha256 hex of cache_key
    field_key       TEXT    NOT NULL,                           -- stable human-readable key (e.g. security_q_mother_color)
    ciphertext      BLOB    NOT NULL,                           -- AES-GCM encrypted answer
    nonce           BLOB    NOT NULL,                           -- 12-byte random nonce
    kdf_meta        TEXT    NOT NULL,                           -- JSON: {salt_hex, time_cost, memory_cost, parallelism}
    created_at      TEXT    NOT NULL,                           -- UTC ISO-8601
    expires_at      TEXT    NOT NULL,                           -- UTC ISO-8601 (created_at + TTL)
    last_used_at    TEXT,                                       -- UTC ISO-8601, NULL until first lookup hit

    PRIMARY KEY (credential_id, question_hash)
);

-- Index for TTL-based purge (daily cron scans expires_at < now())
CREATE INDEX IF NOT EXISTS idx_security_answers_expires
    ON security_answers (expires_at);

-- Index for per-credential listing (list_security_questions operation)
CREATE INDEX IF NOT EXISTS idx_security_answers_credential
    ON security_answers (credential_id);

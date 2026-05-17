-- Pending human-input answers delivered via POST /jobs/{id}/human-input.
-- Consumed by ExecuteScrapeMapActivity polling (ADR-0021 bridge).

CREATE TABLE IF NOT EXISTS human_input_answers (
    job_id         TEXT NOT NULL,
    field_key      TEXT NOT NULL,
    answer         TEXT,
    persist        INTEGER NOT NULL DEFAULT 1,
    question_hash  TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (job_id, field_key)
);

-- ============================================================
-- Text2SQL Studio — PostgreSQL Schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Tables
CREATE TABLE IF NOT EXISTS tables (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    name        TEXT NOT NULL,
    schema_name TEXT NOT NULL DEFAULT 'public',
    status      TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','sandbox','verified','production','degraded')),
    owner_id    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tables_status   ON tables(status);
CREATE INDEX IF NOT EXISTS idx_tables_owner_id ON tables(owner_id);
CREATE INDEX IF NOT EXISTS idx_tables_name     ON tables USING gin(to_tsvector('english', name));

-- Enrichment Versions
CREATE TABLE IF NOT EXISTS enrichment_versions (
    id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    table_id   TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL DEFAULT 1,
    data       JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (table_id, version)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_table_id ON enrichment_versions(table_id);

-- Golden Questions
CREATE TABLE IF NOT EXISTS golden_questions (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    table_id     TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    question     TEXT NOT NULL,
    expected_sql TEXT NOT NULL,
    difficulty   TEXT NOT NULL DEFAULT 'simple'
                   CHECK (difficulty IN ('simple','medium','complex')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_questions_table_id ON golden_questions(table_id);

-- Eval Runs
CREATE TABLE IF NOT EXISTS eval_runs (
    id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    table_id   TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    score      FLOAT NOT NULL DEFAULT 0.0,
    status     TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running','completed','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_table_id ON eval_runs(table_id);

-- Eval Results
CREATE TABLE IF NOT EXISTS eval_results (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    run_id      TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL REFERENCES golden_questions(id) ON DELETE CASCADE,
    score       FLOAT NOT NULL DEFAULT 0.0,
    status      TEXT NOT NULL CHECK (status IN ('pass','fail')),
    error_type  TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id);

-- User Scopes
CREATE TABLE IF NOT EXISTS user_scopes (
    id        TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    user_id   TEXT NOT NULL,
    name      TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_scopes_user_id ON user_scopes(user_id);

-- Audit Queries
CREATE TABLE IF NOT EXISTS audit_queries (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    table_id    TEXT REFERENCES tables(id) ON DELETE SET NULL,
    user_id     TEXT NOT NULL,
    query       TEXT NOT NULL,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    latency_ms  INTEGER,
    success     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_audit_table_id   ON audit_queries(table_id);
CREATE INDEX IF NOT EXISTS idx_audit_executed_at ON audit_queries(executed_at DESC);

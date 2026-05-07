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

-- ============================================================
-- Profiling System
-- ============================================================

-- Table Profiles
CREATE TABLE IF NOT EXISTS table_profiles (
    id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    table_id     TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','running','completed','failed')),
    row_count    BIGINT,
    sample_size  BIGINT,
    column_count INTEGER,
    size_bytes   BIGINT,
    null_rate_avg FLOAT,
    duplicate_rate FLOAT,
    sample_data  JSONB,
    auto_insights JSONB,
    profile_json JSONB,
    cached_until TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_table_profiles_table_id  ON table_profiles(table_id);
CREATE INDEX IF NOT EXISTS idx_table_profiles_version   ON table_profiles(table_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_table_profiles_status    ON table_profiles(status);

-- Column Profiles
CREATE TABLE IF NOT EXISTS column_profiles (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    table_id       TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    profile_id     TEXT NOT NULL REFERENCES table_profiles(id) ON DELETE CASCADE,
    column_name    TEXT NOT NULL,
    data_type      TEXT,
    null_count     BIGINT,
    null_rate      FLOAT,
    distinct_count BIGINT,
    min_value      TEXT,
    max_value      TEXT,
    avg_value      FLOAT,
    median_value   FLOAT,
    top_values     JSONB,
    is_categorical BOOLEAN NOT NULL DEFAULT FALSE,
    is_geo         BOOLEAN NOT NULL DEFAULT FALSE,
    is_time        BOOLEAN NOT NULL DEFAULT FALSE,
    semantic_type  TEXT CHECK (semantic_type IN ('categorical','continuous','time','geo')),
    stats_json     JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_col_profiles_profile_id   ON column_profiles(profile_id);
CREATE INDEX IF NOT EXISTS idx_col_profiles_table_id     ON column_profiles(table_id);
CREATE INDEX IF NOT EXISTS idx_col_profiles_col_name     ON column_profiles(column_name);
CREATE INDEX IF NOT EXISTS idx_col_profiles_semantic     ON column_profiles(semantic_type);
CREATE INDEX IF NOT EXISTS idx_col_profiles_categorical  ON column_profiles(is_categorical) WHERE is_categorical = TRUE;

-- Cross-Table Profiles (join suggestions)
CREATE TABLE IF NOT EXISTS cross_table_profiles (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    source_table_id  TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    target_table_id  TEXT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    join_suggestion  TEXT,
    match_strength   TEXT NOT NULL DEFAULT 'weak' CHECK (match_strength IN ('strong','weak')),
    common_columns   JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cross_profiles_source ON cross_table_profiles(source_table_id);

-- ============================================================
-- Migration: Add new columns to existing deployments
-- (idempotent — safe to run multiple times)
-- ============================================================
ALTER TABLE table_profiles
    ADD COLUMN IF NOT EXISTS version      INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS sample_size  BIGINT,
    ADD COLUMN IF NOT EXISTS profile_json JSONB;

ALTER TABLE column_profiles
    ADD COLUMN IF NOT EXISTS median_value   FLOAT,
    ADD COLUMN IF NOT EXISTS is_categorical BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS semantic_type  TEXT,
    ADD COLUMN IF NOT EXISTS stats_json     JSONB;

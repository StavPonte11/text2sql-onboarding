"""Add config schema: feature_flags, feature_flag_audit_log, execution_modes

Revision ID: f9a3d1c8e205
Revises: 4f7c2b9a8e1d
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f9a3d1c8e205"
down_revision: Union[str, None] = "4f7c2b9a8e1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create config schema
    op.execute("CREATE SCHEMA IF NOT EXISTS config")

    # 2. feature_flags table
    op.create_table(
        "feature_flags",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column("value", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        sa.Column("owner", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_modified_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        sa.Column("last_modified_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("name"),
        schema="config",
    )

    # 3. feature_flag_audit_log table
    op.create_table(
        "feature_flag_audit_log",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column("flag_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False, index=True),
        sa.Column("actor", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("old_value", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        schema="config",
    )
    op.create_index(
        "ix_feature_flag_audit_log_flag_name",
        "feature_flag_audit_log",
        ["flag_name"],
        schema="config",
    )

    # 4. execution_modes table
    op.create_table(
        "execution_modes",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), primary_key=True, nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
        sa.Column("flag_overrides", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("name"),
        schema="config",
    )

    # 5. Seed initial feature flags (42 flags from TTS-G4-02)
    flags = [
        # Extraction
        ("EXTRACTOR_MODEL",             "gpt-4o",    "string", "LLM model for extractor node",                  "DS team"),
        ("EXTRACTOR_TEMPERATURE",       0.0,         "float",  "Sampling temperature for extractor",             "DS team"),
        ("EXTRACTOR_TOP_K_TABLES",      10,          "int",    "Max candidate tables from extractor",            "DS team"),
        ("DEFAULT_TABLE_SCOPING_MODE",  "hybrid",    "string", "Table scoping mode: strict | hybrid",            "DS team"),
        # Schema Explorer
        ("MAX_PROFILES_TO_FETCH",       8,           "int",    "Max table profiles fetched per run",             "DS team"),
        ("PROFILE_FETCH_CONCURRENCY",   4,           "int",    "asyncio.Semaphore limit for profile fetch",      "Eng"),
        ("SCHEMA_CACHE_TTL",            600,         "int",    "DDL cache TTL in seconds",                       "Eng"),
        ("PROFILE_CACHE_TTL",           1800,        "int",    "Profile cache TTL in seconds",                   "Eng"),
        ("SCHEMA_SEMANTIC_TYPING",      False,       "bool",   "Enable column semantic type classification",     "DS team"),
        ("SCHEMA_JOIN_GRAPH",           False,       "bool",   "Enable join graph injection (Phase 2)",          "DS team"),
        ("SCHEMA_SUMMARIZATION",        False,       "bool",   "Enable table summarization for large schemas",   "DS team"),
        ("SCHEMA_AMBIGUITY_DETECT",     True,        "bool",   "Enable ambiguous column detection",              "DS team"),
        ("SCHEMA_EXPLORER_MODEL",        "gpt-4o-mini","string","Model for schema explorer",   "DS team"),
        ("SCHEMA_TOP_K_JOINS",          5,           "int",    "Max join suggestions to inject",                 "DS team"),
        # Query Builder
        ("QUERY_BUILDER_MODEL",         "gpt-4o",    "string", "LLM model for SQL generation",                  "DS team"),
        ("QUERY_BUILDER_TEMPERATURE",   0.0,         "float",  "Temperature for query builder",                  "DS team"),
        # Refiner
        ("MAX_REFINER_ITERATIONS",      4,           "int",    "Max refiner retry attempts before fallback",     "DS team"),
        ("REFINER_SCHEMA_CONTEXT_TABLES", 4,         "int",    "Max tables in schema context",                   "DS team"),
        ("MAX_SCHEMA_REPLAN_ITERATIONS",2,           "int",    "Max schema_explorer re-entries before HITL",     "DS team"),
        ("REFINER_MODEL",               "gpt-4o",    "string", "LLM model for refiner",                         "DS team"),
        ("ESCA_WRITE_ENABLED",          True,        "bool",   "Master switch to enable writing/reading from Esca", "Eng"),
        # Satisfaction Check
        ("SATISFACTION_CHECK_ENABLED",  False,       "bool",   "Master switch for satisfaction check module",    "DS team"),
        ("SATISFACTION_CHECK_EXECUTION",True,        "bool",   "Check: SQL executed without error",              "DS team"),
        ("SATISFACTION_CHECK_PLAUSIBILITY",True,     "bool",   "Check: result row count plausible",              "DS team"),
        ("SATISFACTION_CHECK_COLUMNS",  False,       "bool",   "Check: result columns match question intent",    "DS team"),
        ("SATISFACTION_CHECK_SEMANTIC", False,       "bool",   "Check: LLM semantic alignment score",            "DS team"),
        ("SATISFACTION_MIN_ROWS",       0,           "int",    "Min acceptable result rows (0 = allow empty)",  "DS team"),
        ("SATISFACTION_MAX_ROWS",       1000000,     "int",    "Max acceptable result rows",                     "DS team"),
        ("SATISFACTION_SEMANTIC_THRESHOLD", 0.75,   "float",  "Min semantic alignment score (0-1)",             "DS team"),
        ("SATISFACTION_JUDGE_MODEL",    "gpt-4o-mini","string","Model for satisfaction semantic check",          "DS team"),
        # Skills
        ("SKILLS_ENABLED",              True,        "bool",   "Enable Jeen Skills API integration",            "DS team"),
        ("SKILLS_HOT_RELOAD",           False,       "bool",   "Re-fetch skills on every agent invocation",     "DS team"),
        ("SKILLS_CACHE_TTL",            900,         "int",    "Skills cache TTL in seconds",                    "Eng"),
        # Evaluation
        ("LLM_JUDGE_ENABLED",           True,        "bool",   "Enable LLM judge in evaluations",               "DS team"),
        ("EVAL_PARALLEL_WORKERS",       4,           "int",    "Parallel eval task workers",                     "Eng"),
        ("EVAL_JUDGE_MODEL",            "gpt-4-turbo","string","Model used by LLM judge",                       "DS team"),
        # Catalog Validation
        ("CATALOG_VALIDATION_ENABLED",  True,        "bool",   "Validate extracted tables against Trino catalog","DS team"),
        ("CATALOG_CACHE_TTL",           300,         "int",    "Catalog validation cache TTL in seconds",        "Eng"),
    ]

    import json as _json
    from datetime import datetime as _dt

    now = _dt.utcnow()
    flag_rows = [
        {
            "name": name,
            "value": _json.dumps(value),  # store as JSON-encoded value
            "type": flag_type,
            "description": description,
            "owner": owner,
            "last_modified_by": "seed",
            "last_modified_at": now,
        }
        for name, value, flag_type, description, owner in flags
    ]
    op.bulk_insert(
        sa.table(
            "feature_flags",
            sa.column("name"),
            sa.column("value"),
            sa.column("type"),
            sa.column("description"),
            sa.column("owner"),
            sa.column("last_modified_by"),
            sa.column("last_modified_at"),
            schema="config",
        ),
        flag_rows,
    )

    # 6. Seed built-in execution modes
    modes = [
        {
            "name": "default",
            "description": "Standard production configuration. No flag overrides.",
            "flag_overrides": _json.dumps({}),
            "is_active": True,
            "created_by": "system",
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "cost_saving",
            "description": "Use cheaper models and disable expensive LLM checks. Suitable for high-volume batch runs.",
            "flag_overrides": _json.dumps({
                "QUERY_BUILDER_MODEL": "gpt-4o-mini",
                "REFINER_MODEL": "gpt-4o-mini",
                "SATISFACTION_CHECK_SEMANTIC": False,
                "SCHEMA_SUMMARIZATION": False,
                "LLM_JUDGE_ENABLED": False,
            }),
            "is_active": True,
            "created_by": "system",
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "high_quality",
            "description": "Use strongest models and enable all quality checks. Best accuracy, higher cost.",
            "flag_overrides": _json.dumps({
                "QUERY_BUILDER_MODEL": "gpt-4o",
                "REFINER_MODEL": "gpt-4o",
                "SATISFACTION_CHECK_ENABLED": True,
                "SATISFACTION_CHECK_SEMANTIC": True,
                "SCHEMA_SUMMARIZATION": True,
                "SCHEMA_SEMANTIC_TYPING": True,
                "MAX_REFINER_ITERATIONS": 6,
            }),
            "is_active": True,
            "created_by": "system",
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "benchmark",
            "description": "Disable HITL and satisfaction checks for uninterrupted eval runs.",
            "flag_overrides": _json.dumps({
                "SATISFACTION_CHECK_ENABLED": False,
                "MAX_REFINER_ITERATIONS": 2,
                "SCHEMA_SUMMARIZATION": False,
            }),
            "is_active": True,
            "created_by": "system",
            "created_at": now,
            "updated_at": now,
        },
    ]
    op.bulk_insert(
        sa.table(
            "execution_modes",
            sa.column("name"),
            sa.column("description"),
            sa.column("flag_overrides"),
            sa.column("is_active"),
            sa.column("created_by"),
            sa.column("created_at"),
            sa.column("updated_at"),
            schema="config",
        ),
        modes,
    )


def downgrade() -> None:
    op.drop_index("ix_feature_flag_audit_log_flag_name", table_name="feature_flag_audit_log", schema="config")
    op.drop_table("feature_flag_audit_log", schema="config")
    op.drop_table("feature_flags", schema="config")
    op.drop_table("execution_modes", schema="config")
    op.execute("DROP SCHEMA IF EXISTS config")

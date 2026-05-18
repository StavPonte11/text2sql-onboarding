"""
warehouse.py — Manages physical table presence in the PostgreSQL data warehouse.

These functions add or remove a table definition from the warehouse database that
the Text2SQL agent queries. This is separate from the onboarding app's own DB
(which tracks metadata like status, enrichment, golden questions).

Merge note:
  The "warehouse" here is the PostgreSQL database the agent runs queries against.
  When merging with the main app, replace the raw psycopg2 calls with the
  appropriate Trino/MCP DDL commands if the warehouse is Trino-managed.
"""
from __future__ import annotations

import logging
from typing import Optional

import psycopg2
from psycopg2 import sql as pg_sql

from app.config import settings
from app.models.models import Table

logger = logging.getLogger(__name__)


def _get_warehouse_connection():
    """
    Returns a psycopg2 connection to the data warehouse (same PostgreSQL instance).
    Uses the DATABASE_URL from settings, stripping the SQLAlchemy driver prefix.
    """
    db_url = settings.WAREHOUSE_DB_URL
    # Strip SQLAlchemy prefixes like postgresql+psycopg2:// → postgresql://
    if "+" in db_url.split("://")[0]:
        db_url = "postgresql://" + db_url.split("://", 1)[1]
    return psycopg2.connect(db_url)


def add_table_to_warehouse(table: Table, schema_definition: Optional[str] = None) -> bool:
    """
    Registers a table in the data warehouse so the Text2SQL agent can query it.

    Creates the table's schema in the warehouse PostgreSQL database.
    If schema_definition (a CREATE TABLE SQL string) is provided, it is executed.
    Otherwise, a minimal placeholder table is created so queries can be routed to it.

    Args:
        table: The Table model instance to add.
        schema_definition: Optional DDL SQL to create the real schema. If None,
                           creates a placeholder with an 'id' column.

    Returns:
        True if successfully added (or already exists), False on error.
    """
    try:
        conn = _get_warehouse_connection()
        conn.autocommit = True
        with conn.cursor() as cur:
            schema = table.schema_name or "public"

            # Ensure schema exists
            cur.execute(
                pg_sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    pg_sql.Identifier(schema)
                )
            )

            if schema_definition:
                # Execute the provided DDL directly
                cur.execute(schema_definition)
            else:
                # Create a minimal placeholder table so the agent can be routed to it
                cur.execute(
                    pg_sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {}.{} (id SERIAL PRIMARY KEY)"
                    ).format(
                        pg_sql.Identifier(schema),
                        pg_sql.Identifier(table.name),
                    )
                )

        conn.close()
        logger.info(
            f"[Warehouse] Table '{table.schema_name}.{table.name}' added to warehouse."
        )
        return True

    except Exception as e:
        logger.error(
            f"[Warehouse] Failed to add table '{table.schema_name}.{table.name}': {e}",
            exc_info=True,
        )
        return False


def remove_table_from_warehouse(table: Table) -> bool:
    """
    Removes a table from the data warehouse so the Text2SQL agent can no longer query it.

    Drops the physical table from PostgreSQL. Uses IF EXISTS so it is safe to call
    even if the table was never added (e.g. promotion was rejected before approval).

    Args:
        table: The Table model instance to remove.

    Returns:
        True if successfully removed (or did not exist), False on error.
    """
    try:
        conn = _get_warehouse_connection()
        conn.autocommit = True
        with conn.cursor() as cur:
            schema = table.schema_name or "public"
            cur.execute(
                pg_sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                    pg_sql.Identifier(schema),
                    pg_sql.Identifier(table.name),
                )
            )
        conn.close()
        logger.info(
            f"[Warehouse] Table '{table.schema_name}.{table.name}' removed from warehouse."
        )
        return True

    except Exception as e:
        logger.error(
            f"[Warehouse] Failed to remove table '{table.schema_name}.{table.name}': {e}",
            exc_info=True,
        )
        return False


def table_exists_in_warehouse(table: Table) -> bool:
    """
    Returns True if the table already exists in the warehouse PostgreSQL database.
    """
    try:
        conn = _get_warehouse_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                )
                """,
                (table.schema_name or "public", table.name),
            )
            exists = cur.fetchone()[0]
        conn.close()
        return exists
    except Exception as e:
        logger.error(f"[Warehouse] Existence check failed for '{table.name}': {e}")
        return False

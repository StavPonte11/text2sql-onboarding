#!/usr/bin/env python3
"""
generate_trino_catalogs.py
--------------------------
Auto-generates Trino Snowflake catalog .properties files from the live
Snowflake database list.  One file per database, written to
infra/trino/etc/catalog/.

Idempotent: skips databases whose catalog file already exists.

Usage:
    uv run --with python-dotenv --with snowflake-connector-python scripts/generate_trino_catalogs.py

Optional env vars:
    CATALOG_DENY_LIST   comma-separated extra DB names to skip in addition to system defaults
"""

import os
import re
import logging
from pathlib import Path
from dotenv import load_dotenv
import snowflake.connector

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("generate_trino_catalogs")

load_dotenv(".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CATALOG_DIR = Path("infra/trino/etc/catalog")

# System databases we never want as catalogs
SYSTEM_DENY = {
    "SNOWFLAKE",
    "SNOWFLAKE_SAMPLE_DATA",
    "SNOWFLAKE_LEARNING_DB",
    "LOG",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All connector values come from env vars — only snowflake.database is per-catalog
TEMPLATE = """\
connector.name=snowflake
connection-url=${{ENV:SNOWFLAKE_JDBC_URL}}
connection-user=${{ENV:SNOWFLAKE_USER}}
connection-password=${{ENV:SNOWFLAKE_PASSWORD}}
snowflake.account=${{ENV:SNOWFLAKE_ACCOUNT}}
snowflake.role=${{ENV:SNOWFLAKE_ROLE}}
snowflake.warehouse=${{ENV:SNOWFLAKE_WAREHOUSE}}
snowflake.database={database}
"""



def sanitize_catalog_name(db_name: str) -> str:
    """Convert a Snowflake DB name to a valid Trino catalog name (lowercase, no special chars)."""
    name = db_name.lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    if name and not name[0].isalpha():
        name = "sf_" + name
    return name


def get_snowflake_databases(account: str, user: str, password: str, role: str, warehouse: str) -> list[str]:
    logger.info("Connecting to Snowflake...")
    conn = snowflake.connector.connect(
        user=user,
        password=password,
        account=account,
        role=role,
        warehouse=warehouse,
    )
    cur = conn.cursor()
    try:
        cur.execute("SHOW DATABASES")
        rows = cur.fetchall()
        return [r[1] for r in rows]
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Read all credentials from env vars
    sf_account  = os.environ.get("SNOWFLAKE_ACCOUNT", "RSRSBDK-YDB67606")
    sf_user     = os.environ.get("SNOWFLAKE_USER", "BARVAZ_HAMOOD")
    sf_password = os.environ.get("SNOWFLAKE_PASSWORD")
    sf_role     = os.environ.get("SNOWFLAKE_ROLE", "PARTICIPANT")
    sf_warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH_PARTICIPANT")

    if not sf_password:
        raise ValueError("SNOWFLAKE_PASSWORD not set in .env")

    CATALOG_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve extra deny list (on top of system defaults)
    deny_env = os.environ.get("CATALOG_DENY_LIST", "")
    extra_deny = {db.strip().upper() for db in deny_env.split(",") if db.strip()}
    deny_list = SYSTEM_DENY | extra_deny

    # Fetch ALL live Snowflake databases
    all_dbs = get_snowflake_databases(sf_account, sf_user, sf_password, sf_role, sf_warehouse)
    logger.info(f"Snowflake reports {len(all_dbs)} total databases.")

    # Filter out system/denied databases — everything else gets a catalog file
    target_dbs = [db for db in all_dbs if db.upper() not in deny_list]
    logger.info(f"Will ensure {len(target_dbs)} catalog file(s) exist (excluding {len(deny_list)} denied databases).")

    created, skipped = 0, 0
    for db in sorted(target_dbs):
        catalog_name = sanitize_catalog_name(db)
        props_file = CATALOG_DIR / f"{catalog_name}.properties"

        if props_file.exists():
            logger.info(f"  [SKIP]    {props_file.name}  (already exists)")
            skipped += 1
            continue

        content = TEMPLATE.format(database=db)
        props_file.write_text(content)
        logger.info(f"  [CREATED] {props_file.name}  → Snowflake DB '{db}'")
        created += 1

    logger.info(
        f"\nDone. Created: {created}  Skipped (already existed): {skipped}  "
        f"Total catalog files: {len(list(CATALOG_DIR.glob('*.properties')))}"
    )


if __name__ == "__main__":
    main()

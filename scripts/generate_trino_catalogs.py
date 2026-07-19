#!/usr/bin/env python3
"""
generate_trino_catalogs.py
--------------------------
Auto-generates Trino Snowflake catalog .properties files from the live
Snowflake database list.  One file per database, written to
infra/trino/etc/catalog/.

Idempotent: skips databases whose catalog file already exists.

IMPORTANT: Trino (file-based catalog config) only loads .properties files
from infra/trino/etc/catalog/ at container STARTUP. Running this script
while Trino is already running will create new files that Trino will NOT
see until it is restarted. After running this script, always:
    docker compose restart trino
(or `docker compose up -d --force-recreate trino`) before running
sync_om_metadata.py, or newly-added catalogs will silently be invisible
to OpenMetadata and the app DB, even though the .properties files exist
on disk.

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
import json

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


def get_question_referenced_db_ids() -> set[str]:
    """
    Fetch the Spider2-Snow sf_ question set and return the distinct db_id
    values it references (uppercased, matching Snowflake's SHOW DATABASES
    casing). Used to skip generating Trino catalogs for databases that will
    never have a single golden question -- no point ingesting/syncing them
    at all.
    """
    import requests as _requests
    url = "https://raw.githubusercontent.com/xlang-ai/Spider2/main/spider2-snow/spider2-snow.jsonl"
    resp = _requests.get(url, timeout=15)
    resp.raise_for_status()
    db_ids = set()
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            q = json.loads(line)
        except json.JSONDecodeError:
            continue
        if q.get("instance_id", "").startswith("sf_") and q.get("db_id"):
            db_ids.add(q["db_id"].upper())
    return db_ids

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

    # Fetch ALL live Snowflake databases visible to this role/account. If this
    # number looks low (e.g. you expected ~129 spider2-snow databases but only
    # see a handful), that's a Snowflake grants problem -- this script can
    # only ever federate what SHOW DATABASES actually returns for sf_role.
    all_dbs = get_snowflake_databases(sf_account, sf_user, sf_password, sf_role, sf_warehouse)
    logger.info(f"Snowflake reports {len(all_dbs)} total databases visible to role '{sf_role}':")
    for db in sorted(all_dbs):
        logger.info(f"    {db}")

    referenced_db_ids = get_question_referenced_db_ids()
    logger.info(f"Golden questions reference {len(referenced_db_ids)} distinct database(s).")

    target_dbs = [
        db for db in all_dbs
        if db.upper() not in deny_list and db.upper() in referenced_db_ids
    ]
    logger.info(
        f"Will ensure {len(target_dbs)} catalog file(s) exist "
        f"(restricted to golden-question databases; excluding {len(deny_list)} denied "
        f"database(s), and {len([d for d in all_dbs if d.upper() not in deny_list]) - len(target_dbs)} "
        f"database(s) with zero golden questions)."
    )

    created, skipped = 0, 0
    created_names, skipped_names = [], []
    for db in sorted(target_dbs):
        catalog_name = sanitize_catalog_name(db)
        props_file = CATALOG_DIR / f"{catalog_name}.properties"

        if props_file.exists():
            logger.info(f"  [SKIP]    {props_file.name}  (already exists)")
            skipped += 1
            skipped_names.append(catalog_name)
            continue

        content = TEMPLATE.format(database=db)
        props_file.write_text(content)
        logger.info(f"  [CREATED] {props_file.name}  → Snowflake DB '{db}'")
        created += 1
        created_names.append(catalog_name)

    total_files = len(list(CATALOG_DIR.glob('*.properties')))
    logger.info(
        f"\nDone. Created: {created}  Skipped (already existed): {skipped}  "
        f"Total catalog files on disk: {total_files}"
    )

    if created:
        logger.warning(
            "\n"
            "==================================================================\n"
            f"{created} NEW catalog file(s) were just written to {CATALOG_DIR}/.\n"
            "Trino only loads catalog .properties files at container STARTUP.\n"
            "It will NOT see these new catalogs until you restart it:\n"
            "\n"
            "    docker compose restart trino\n"
            "\n"
            "Do this BEFORE re-running the OpenMetadata ingestion pipeline or\n"
            "scripts/sync_om_metadata.py, or the new catalogs will silently be\n"
            "invisible to both and you'll see the same tables/catalogs as before.\n"
            "=================================================================="
        )


if __name__ == "__main__":
    main()
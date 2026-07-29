#!/usr/bin/env python3
"""
Sync OpenMetadata metadata into the app's `tables` rows.

Direction: OpenMetadata -> App DB. OM is the source of truth for which
tables exist, including their canonical name/catalog/schema casing.

Uses OpenMetadata's REST API directly (via `requests`) instead of the
`openmetadata-ingestion` SDK, because that SDK pins sqlalchemy<2 while
sqlmodel (used by this app's ORM) requires sqlalchemy>=2 -- the two cannot
coexist in one environment. This script only needs read-only GET calls, so
the SDK isn't necessary here.

For every table found in OpenMetadata under the `local_trino` service
(the sole service used -- it already federates every Snowflake database
as its own Trino catalog via generate_trino_catalogs.py, so a separate
native Snowflake_Prod service would just duplicate the same data):
    - If a matching row already exists in the app DB `tables` table it is
      updated. Matching is done first by exact (service, catalog,
      schema_name, name), then -- if that misses -- CASE-INSENSITIVELY
      against every existing app row, since a row that differs from OM
      only by case is the same table, not a new one. `openmetadata_json`
      is refreshed (and `embedding`, if --recompute-embeddings is
      passed), and the row's service/catalog/schema_name/name are
      realigned to OM's current casing.
    - If no matching row exists (not even case-insensitively), a NEW row
      is inserted with:
        id           = freshly generated UUID
        service/catalog/schema_name/name = from OpenMetadata
        status       = "sandbox" if catalog == "minio" (the genuine local
                       demo data), else "production" (everything else is a
                       Trino-federated Snowflake catalog, i.e. spider2-snow
                       data)
        owner_id     = "system"
        oasis_source_id = the OpenMetadata table's own entity id (om_table["id"])
        openmetadata_json = synced payload
        embedding    = always computed for new rows (there's no prior
                       embedding to preserve), regardless of whether
                       --recompute-embeddings was passed

If more than one existing app row matches an OM table case-insensitively
(a duplicate), the row whose oasis_source_id exactly matches the OM
table's own entity id is preferred as canonical; otherwise the lowest-id
row is picked deterministically. The other row(s) are left in place and
reported as duplicates -- pass --merge-case-duplicates to delete them.

Rows whose oasis_source_id starts with a protected prefix (currently just
"airlines." -- these are seeded directly into Postgres by infra_init.py's
_ensure_airlines_registered(), bypassing OM entirely) are never considered
for ghost/duplicate reporting, even though they'll never have an OM match.

Rows that exist in the app DB but have NO OpenMetadata match under their
own exact (service, catalog, schema, name) OR case-insensitively against
every table OM actually has (across all TARGET_SERVICES) are "ghost" rows.
This is also how any old rows tagged with a now-retired service (e.g. a
former Snowflake_Prod) get surfaced: since it's no longer in
TARGET_SERVICES, those rows will never match anything. Ghost rows are
reported in the logs; this script does not delete them.

Commits happen in batches (BATCH_SIZE rows) rather than one commit for the
whole run. If a batch fails (e.g. a constraint violation on one row), that
batch is rolled back and retried row-by-row so only the actual bad row(s)
are skipped -- everything already committed in prior batches stays in the
DB, and everything else in the failed batch still gets saved. The same
batching applies to duplicate-row merging.

By default this does NOT recompute embeddings for EXISTING rows (only
openmetadata_json is touched for them). Pass --recompute-embeddings to also
regenerate the embedding column for existing rows from the freshly-synced
description + columns, using the same embedding pipeline as the seed
script. New rows always get an embedding computed, since they don't have
one yet.

CATALOG COVERAGE: this script can only sync what OpenMetadata already
knows about, which in turn can only be what Trino currently has catalogs
loaded for. If you've recently run scripts/generate_trino_catalogs.py to
add more Snowflake databases, make sure you (1) restarted Trino so it
picks up the new .properties files, and (2) re-triggered (or waited for)
the OpenMetadata ingestion pipeline, BEFORE running this script -- otherwise
this script will happily run to completion while only seeing a fraction of
your intended catalogs. This script now logs the distinct catalogs it sees
from OM on every run (see "OM catalog coverage" below) specifically so that
gap is visible immediately instead of requiring separate debugging.

Requires in .env:
    OPENMETADATA_URL, OM_JWT_TOKEN
    Whatever core.db.engine / app.config.settings already need
      (DB connection, EMBEDDER_URL, EMBEDDER_MODEL, EMBEDDER_KEY)

Run with:
    uv run scripts/sync_om_metadata.py [--recompute-embeddings] [--merge-case-duplicates]
"""

import argparse
import logging
import os
import re
import uuid

import requests
import sqlglot
from core.db.engine import engine
from core.embeddings import EXPECTED_EMBEDDING_DIM, get_embedding as core_get_embedding
from core.models.models import EnrichmentVersion, Table as AppTable
from core.spider2 import fetch_spider2_snow_sf_questions
from dotenv import load_dotenv
from sqlmodel import Session, col, select

from app.config import settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("OM_Metadata_Sync")

load_dotenv(".env")

OM_URL = os.environ.get("OPENMETADATA_URL", "http://localhost:8585/api").rstrip("/")
OM_JWT_TOKEN = os.environ.get("OPENMETADATA_TOKEN")
QUERY_LIMIT_PER_TABLE = int(os.environ.get("OM_SAMPLE_QUERIES_PER_TABLE", "5"))
PAGE_SIZE = 100

# Pull everything under this OM service -- no filtering. local_trino is the
# sole source now: it already federates every Snowflake database as its own
# Trino catalog (see generate_trino_catalogs.py), so there's no need for a
# separate native Snowflake_Prod service -- that would just duplicate data
# already visible here.
TARGET_SERVICES = ["local_trino"]

# The one catalog under local_trino that is genuinely local demo data (per
# infra_init.py's own DDL). Every other catalog under local_trino is a
# Trino-federated mirror of a Snowflake database (see
# generate_trino_catalogs.py) -- i.e. spider2-snow data. Used to set
# status: anything NOT in this set is treated as "from spider" -> production.
LOCAL_ONLY_CATALOGS = {"minio"}

# Rows whose oasis_source_id starts with one of these prefixes were seeded
# directly into Postgres by infra_init.py (bypassing OM entirely -- see
# _ensure_airlines_registered()) and must never be treated as ghosts/dupes,
# even though they'll never have an OM match.
PROTECTED_OASIS_SOURCE_ID_PREFIXES = ("airlines.",)

SYSTEM_OWNER_ID = "system"

# Commit in batches so a single problem row only costs that batch, not the
# entire run (a single failed INSERT rolls back everything since the last
# commit -- keeping this small limits the blast radius).
BATCH_SIZE = 200

# ── Spider2-Snow golden questions: fetched live from GitHub ────────────────
#
# Previously golden questions were read from a local spider2_questions.json
# file. That file had gone stale (it actually contained Spider2-Lite
# questions, not Spider2-Snow), and being a local file it's also fragile --
# if it moves or gets renamed, load_golden_questions() silently does nothing.
# Fetching directly from the same GitHub source the evaluation service uses
# removes that whole class of problem: there's no local file to drift out of
# sync or go missing. See the evaluation service's Spider2SnowDownloader for
# the canonical version of this logic (this is a lightweight standalone copy
# since this script lives in a different project/repo).
SPIDER2_SNOW_JSONL_URL = (
    "https://raw.githubusercontent.com/xlang-ai/Spider2/main/"
    "spider2-snow/spider2-snow.jsonl"
)
SPIDER2_SNOW_GOLD_SQL_BASE_URL = (
    "https://raw.githubusercontent.com/xlang-ai/Spider2/main/"
    "spider2-snow/evaluation_suite/gold/sql/"
)
GITHUB_TIMEOUT = 15  # seconds per request


def normalize_key(
    service: str | None, catalog: str | None, schema: str | None, name: str | None
) -> tuple:
    """Case-insensitive matching key. OM is the source of truth for casing,
    but its reported casing for a table can drift between syncs, so exact
    string matching alone isn't reliable for deciding "is this the same
    table" -- only for deciding what to store/display."""
    return (
        (service or "").lower(),
        (catalog or "").lower(),
        (schema or "").lower(),
        (name or "").lower(),
    )


def om_get(path: str, params: dict | None = None) -> dict:
    if not OM_JWT_TOKEN:
        raise ValueError("OM_JWT_TOKEN not found in env. Set it in your .env file.")
    resp = requests.get(
        f"{OM_URL}/v1{path}",
        headers={"Authorization": f"Bearer {OM_JWT_TOKEN}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_om_tables_by_service(service_name: str) -> dict:
    """
    Fetch OM tables for a given service, keyed by (catalog, schema, name).

    Defensive: OM's `service=` filter on /v1/tables is not reliably strict --
    it can return tables belonging to a different actual service. So we also
    request the `service` field on each table and verify it actually matches
    `service_name` before indexing it. Mismatches are dropped and logged
    rather than silently mis-tagged with the wrong service in our DB.
    """
    logger.info(f"Fetching OpenMetadata tables for service '{service_name}'...")
    index = {}
    after = None
    fields = "columns,description,tags,owners,databaseSchema,database,service"

    while True:
        params = {"service": service_name, "fields": fields, "limit": PAGE_SIZE}
        if after:
            params["after"] = after

        page = om_get("/tables", params=params)
        for t in page.get("data", []):
            actual_service = (t.get("service") or {}).get("name")
            if actual_service != service_name:
                logger.warning(
                    f"OM returned a table under service filter '{service_name}' but its actual "
                    f"service is '{actual_service}' (fqn={t.get('fullyQualifiedName')}) -- skipping. "
                    f"Add '{actual_service}' to TARGET_SERVICES if it should be synced."
                )
                continue

            catalog = (t.get("database") or {}).get("name")
            schema = (t.get("databaseSchema") or {}).get("name")
            name = t.get("name")
            index[(catalog, schema, name)] = t

        after = page.get("paging", {}).get("after")
        if not after:
            break

    logger.info(
        f"Found {len(index)} tables in OpenMetadata for service '{service_name}' (after verification)."
    )
    return index


def fetch_sample_queries(table_fqn: str, limit: int) -> list[str]:
    try:
        resp = om_get(
            "/queries",
            params={"entityFQN": table_fqn, "limit": limit, "fields": "query"},
        )
        return [item["query"] for item in resp.get("data", []) if item.get("query")]
    except Exception as e:
        logger.warning(f"Could not fetch sample queries for '{table_fqn}': {e}")
        return []


def build_om_payload(om_table: dict) -> dict:
    columns = [
        {
            "name": col.get("name"),
            "dataType": col.get("dataTypeDisplay") or col.get("dataType"),
            "description": col.get("description"),
            "tags": col.get("tags"),
        }
        for col in om_table.get("columns", [])
    ]

    table_fqn = om_table.get("fullyQualifiedName")
    sample_queries = (
        fetch_sample_queries(table_fqn, QUERY_LIMIT_PER_TABLE) if table_fqn else []
    )

    return {
        "fqn": table_fqn,
        "description": om_table.get("description"),
        "owners": om_table.get("owners"),
        "tags": om_table.get("tags"),
        "columns": columns,
        "sample_queries": sample_queries,
    }


def get_embedding_text(description: str, columns: list[dict]) -> str:
    col_names = ", ".join(c["name"] for c in columns if c.get("name"))
    return f"Description: {description or ''}. Columns: {col_names}"


def get_embedding(text: str) -> list[float]:
    emb = core_get_embedding(
        text=text,
        embedder_url=settings.EMBEDDER_URL,
        embedder_model=settings.EMBEDDER_MODEL,
        embedder_key=settings.EMBEDDER_KEY,
    )
    if emb is None:
        logger.warning("Error getting embedding for text, falling back to zero-vector")
        return [0.0] * EXPECTED_EMBEDDING_DIM
    return emb


def status_for_catalog(catalog: str | None) -> str:
    # Anything other than the genuine local demo catalog (minio) is a
    # Trino-federated Snowflake catalog, i.e. spider2-snow data -> production.
    return "sandbox" if (catalog or "").lower() in LOCAL_ONLY_CATALOGS else "production"


def flush_delete_batch(session: Session, batch: list[tuple]) -> tuple[int, int]:
    """
    Commit a batch of (obj, key_str) pairs that were session.delete()'d.
    Same pattern as flush_batch: if the bulk commit fails, roll back and
    retry each deletion individually so one bad row doesn't block the rest.

    Returns (succeeded, failed) counts.
    """
    if not batch:
        return 0, 0

    try:
        session.commit()
        return len(batch), 0
    except Exception as e:
        session.rollback()
        logger.error(
            f"Batch delete failed ({len(batch)} rows) -- retrying individually. Batch error: {e}"
        )
        succeeded = 0
        failed = 0
        for obj, key_str in batch:
            try:
                session.delete(obj)
                session.commit()
                succeeded += 1
            except Exception as row_e:
                session.rollback()
                logger.error(f"Failed to delete row {key_str}: {row_e}")
                failed += 1
        return succeeded, failed


def flush_batch(session: Session, batch: list[tuple]) -> tuple[int, int]:
    """
    Commit a batch of (obj, key_str) pairs. If the bulk commit fails, roll
    back and retry each row individually so we only lose the actual bad
    row(s) instead of the whole batch.

    Returns (succeeded, failed) counts.
    """
    if not batch:
        return 0, 0

    try:
        session.commit()
        return len(batch), 0
    except Exception as e:
        session.rollback()
        logger.error(
            f"Batch commit failed ({len(batch)} rows) -- retrying rows individually "
            f"to isolate the bad one(s). Batch error: {e}"
        )
        succeeded = 0
        failed = 0
        for obj, key_str in batch:
            try:
                session.add(obj)
                session.commit()
                succeeded += 1
            except Exception as row_e:
                session.rollback()
                logger.error(f"Skipping row {key_str} -- failed to commit: {row_e}")
                failed += 1
        return succeeded, failed


def extract_tables_from_sql(sql: str) -> list[str]:
    # Regex to find table names in FROM or JOIN clauses
    matches = re.findall(
        r'\b(?:from|join|update|into)\s+([a-zA-Z0-9_"\.]+)', sql, re.IGNORECASE
    )
    tables = []
    for match in matches:
        parts = [p.replace('"', "").lower().strip() for p in match.split(".")]
        if parts:
            tables.append(parts[-1])
    return tables


def sync_spider_schemas(session: Session, force: bool = False):
    spider_tables = session.exec(
        select(AppTable).where(AppTable.owner_id == "spider2")
    ).all()
    logger.info(
        f"Syncing schemas (EnrichmentVersion) for {len(spider_tables)} Spider2-Snow tables..."
    )
    synced_schemas = 0
    failed_schemas = 0

    for table in spider_tables:
        try:
            data = table.openmetadata_json or {}
            description = data.get("description") or ""
            om_columns = data.get("columns") or []

            if not om_columns and not description:
                continue

            def parse_columns(cols):
                parsed = []
                for c in cols:
                    col_def = {
                        "name": c.get("name"),
                        "description": c.get("description") or "",
                        "dataType": c.get("dataType"),
                        "is_geo": False,
                        "is_time": False,
                    }
                    if "children" in c:
                        col_def["children"] = parse_columns(c["children"])
                    parsed.append(col_def)
                return parsed

            columns = parse_columns(om_columns)

            existing_enrichment = session.exec(
                select(EnrichmentVersion)
                .where(EnrichmentVersion.table_id == table.id)
                .order_by(col(EnrichmentVersion.version).desc())
            ).first()

            if existing_enrichment and existing_enrichment.data and not force:
                existing_data = existing_enrichment.data
                if (
                    existing_data.get("table_description") == description
                    and existing_data.get("columns") == columns
                ):
                    continue

            next_version = (
                (existing_enrichment.version + 1) if existing_enrichment else 1
            )
            new_enrichment = EnrichmentVersion(
                table_id=table.id,
                version=next_version,
                data={"table_description": description, "columns": columns},
            )
            session.add(new_enrichment)
            synced_schemas += 1
        except Exception as e:
            logger.error(
                f"Failed to sync schema for table {table.name} ({table.id}): {e}"
            )
            failed_schemas += 1

    session.commit()
    logger.info(f"Schema sync completed: {synced_schemas} ok, {failed_schemas} failed.")


def _fetch_spider2_snow_gold_sql(instance_id: str) -> str | None:
    """Download the gold SQL file for a single Spider2-Snow instance."""
    url = f"{SPIDER2_SNOW_GOLD_SQL_BASE_URL}{instance_id}.sql"
    try:
        resp = requests.get(url, timeout=GITHUB_TIMEOUT)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as exc:
        logger.debug(f"No gold SQL available for {instance_id}: {exc}")
        return None


def _translate_snowflake_to_trino(snowflake_sql: str, instance_id: str) -> str:
    """Translate gold SQL from Snowflake to Trino dialect via sqlglot."""
    try:
        results = sqlglot.transpile(snowflake_sql, read="snowflake", write="trino")
        if results:
            trino_sql = results[0]
            # Clean up sqlglot's comma + CROSS JOIN UNNEST formatting bug.
            trino_sql = trino_sql.replace(",  CROSS JOIN UNNEST", " CROSS JOIN UNNEST")
            trino_sql = trino_sql.replace(", CROSS JOIN UNNEST", " CROSS JOIN UNNEST")
            trino_sql = trino_sql.replace(",  cross join unnest", " cross join unnest")
            trino_sql = trino_sql.replace(", cross join unnest", " cross join unnest")
            return trino_sql
    except Exception as exc:
        logger.warning(f"SQL translation failed for {instance_id}: {exc}")
    # Fall back to the original SQL -- Trino may still accept it as-is.
    return snowflake_sql


def fetch_spider2_snow_questions() -> list[dict]:
    """
    Fetch Spider2-Snow benchmark questions straight from the xlang-ai/Spider2
    GitHub repo, with gold SQL translated from Snowflake to Trino dialect.

    Returns a list of dicts shaped like the old spider2_questions.json
    entries did, so load_golden_questions() below needs no other changes:
        {"input": {"query": ...}, "expected_output": {"sql": ...},
         "metadata": {"db": ..., "difficulty": ..., "question_type": ...}}
    """

    logger.info(f"Fetching Spider2-Snow questions from {SPIDER2_SNOW_JSONL_URL} ...")
    sf_questions = fetch_spider2_snow_sf_questions(
        url=SPIDER2_SNOW_JSONL_URL, timeout=GITHUB_TIMEOUT
    )
    logger.info(
        f"spider2-snow.jsonl: fetched {len(sf_questions)} sf_ (Snowflake) questions."
    )

    # Diagnostic: how many distinct Snowflake databases (db_id) does the
    # full sf_ question set reference? Compared against the "OM catalog
    # coverage" log emitted in main() below, this tells you at a glance
    # what fraction of the benchmark's databases you've actually federated.
    distinct_db_ids = sorted(
        {q.get("db_id", "") for q in sf_questions if q.get("db_id")}
    )
    logger.info(
        f"spider2-snow.jsonl sf_ questions reference {len(distinct_db_ids)} distinct db_id(s): "
        f"{distinct_db_ids}"
    )

    questions: list[dict] = []
    skipped_no_gold = 0
    for q in sf_questions:
        instance_id = q["instance_id"]
        db = q.get("db_id", "")

        gold_sql = _fetch_spider2_snow_gold_sql(instance_id)
        if not gold_sql:
            skipped_no_gold += 1
            continue

        trino_sql = _translate_snowflake_to_trino(gold_sql, instance_id)

        questions.append(
            {
                "id": instance_id,
                "input": {"query": q["instruction"]},
                "expected_output": {"sql": trino_sql},
                "metadata": {
                    "db": db,
                    "difficulty": "complex",
                    "question_type": "join",
                },
            }
        )

    logger.info(
        f"Fetched {len(questions)} Spider2-Snow questions with gold SQL from GitHub "
        f"({skipped_no_gold} skipped -- no released gold SQL on GitHub for that instance; "
        f"this is expected, the maintainers only release gold SQL for a subset of instances)."
    )
    return questions


def load_golden_questions(session: Session):
    try:
        questions = fetch_spider2_snow_questions()
    except Exception as e:
        logger.error(f"Failed to fetch Spider2-Snow questions from GitHub: {e}")
        return

    if not questions:
        logger.warning(
            "No Spider2-Snow questions were fetched from GitHub -- nothing to load."
        )
        return

    tables = session.exec(select(AppTable).where(AppTable.owner_id == "spider2")).all()
    tables_by_catalog = {}
    for t in tables:
        tables_by_catalog.setdefault(t.catalog.lower(), []).append(t)

    logger.info(
        f"App DB currently has {len(tables_by_catalog)} distinct spider2 catalog(s) "
        f"available to match golden questions against: {sorted(tables_by_catalog.keys())}"
    )

    from core.models.models import DifficultyLevel, GoldenQuestion, QuestionType

    def get_difficulty(diff_str):
        diff_str = str(diff_str).lower().strip()
        if diff_str == "medium":
            return DifficultyLevel.medium
        elif diff_str == "complex":
            return DifficultyLevel.complex
        return DifficultyLevel.simple

    def get_question_type(q_type_str):
        q_type_str = str(q_type_str).lower().strip()
        if q_type_str == "join":
            return QuestionType.join
        elif q_type_str == "geo":
            return QuestionType.geo
        elif q_type_str == "aggregate":
            return QuestionType.aggregate
        elif q_type_str == "time_series":
            return QuestionType.time_series
        return QuestionType.simple

    inserted = 0
    skipped = 0
    failed = 0
    failed_dbs: dict[str, int] = {}

    for item in questions:
        question_text = item["input"]["query"]
        expected_sql = item["expected_output"]["sql"]
        # Same db -> catalog normalization as the evaluation service's
        # Spider2SnowDownloader (db.lower(), spaces -> underscores), so
        # this lines up with how catalogs are actually named in Trino.
        db = item["metadata"]["db"].lower().strip().replace(" ", "_")
        difficulty = get_difficulty(item["metadata"].get("difficulty", "simple"))
        q_type = get_question_type(item["metadata"].get("question_type", "simple"))

        catalog_tables = tables_by_catalog.get(db, [])
        if not catalog_tables:
            failed += 1
            failed_dbs[db] = failed_dbs.get(db, 0) + 1
            continue

        ref_tables = extract_tables_from_sql(expected_sql)
        target_table = None

        for ref_t in ref_tables:
            for t in catalog_tables:
                if t.name.lower() == ref_t:
                    target_table = t
                    break
            if target_table:
                break

        if not target_table:
            catalog_tables_sorted = sorted(catalog_tables, key=lambda t: t.id)
            target_table = catalog_tables_sorted[0]

        existing_q = session.exec(
            select(GoldenQuestion)
            .where(GoldenQuestion.table_id == target_table.id)
            .where(GoldenQuestion.question == question_text)
        ).first()

        if existing_q:
            skipped += 1
        else:
            new_q = GoldenQuestion(
                id=str(uuid.uuid4()),
                table_id=target_table.id,
                question=question_text,
                expected_sql=expected_sql,
                difficulty=difficulty,
                question_type=q_type,
            )
            session.add(new_q)
            inserted += 1

    session.commit()
    logger.info(
        f"Golden questions load completed: {inserted} inserted, {skipped} skipped, {failed} failed (no matching catalog)."
    )

    if failed_dbs:
        logger.warning(
            f"{len(failed_dbs)} distinct db_id(s) had NO matching app-DB catalog at all "
            f"(these catalogs were never synced -- see 'OM catalog coverage' earlier in the "
            f"log, or check generate_trino_catalogs.py / Trino restart status): "
            f"{sorted(failed_dbs.keys())}"
        )

    tables_with_zero = []
    for t in tables:
        q_count = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == t.id)
        ).first()
        if not q_count:
            tables_with_zero.append(f"{t.catalog}.{t.schema_name}.{t.name}")

    if tables_with_zero:
        logger.warning(
            f"Found {len(tables_with_zero)} Spider2-Snow tables with 0 golden questions: {tables_with_zero}"
        )


def main(
    recompute_embeddings: bool,
    merge_case_duplicates: bool,
    sync_schema: bool,
    load_questions: bool,
):
    with Session(engine) as session:
        app_tables = session.exec(select(AppTable)).all()
        logger.info(f"Loaded {len(app_tables)} existing rows from app 'tables' table.")

        # Exact-case index (fast path) plus a case-insensitive index (fallback
        # path -- and the thing that stops this script from re-inserting the
        # same table under a different case every time OM's casing drifts).
        app_index = {
            (t.service, t.catalog, t.schema_name, t.name): t for t in app_tables
        }
        app_index_by_norm: dict[tuple, list[AppTable]] = {}
        for t in app_tables:
            app_index_by_norm.setdefault(
                normalize_key(t.service, t.catalog, t.schema_name, t.name), []
            ).append(t)

        matched_row_ids: set[str] = set()

        om_by_service = {
            svc: fetch_om_tables_by_service(svc) for svc in TARGET_SERVICES
        }

        # ── Catalog coverage diagnostic ─────────────────────────────────────
        # Log exactly which catalogs OpenMetadata is reporting *before* we do
        # any matching. This is the fastest way to notice "I generated 129
        # Trino catalogs but OM (and this script) only sees 11 of them" --
        # usually because Trino wasn't restarted after new .properties files
        # were written, or the OM ingestion pipeline hasn't run since.
        om_catalogs_seen = sorted(
            {
                (catalog or "").lower()
                for om_index in om_by_service.values()
                for (catalog, _schema, _name) in om_index.keys()
            }
        )
        logger.info(
            f"OM catalog coverage: OpenMetadata currently exposes {len(om_catalogs_seen)} "
            f"distinct catalog(s) under {TARGET_SERVICES}: {om_catalogs_seen}"
        )

        updated, inserted, failed = 0, 0, 0
        batch: list[tuple] = []

        for service, om_index in om_by_service.items():
            for (catalog, schema, name), om_table in om_index.items():
                key = (service, catalog, schema, name)
                norm_key = normalize_key(service, catalog, schema, name)
                key_str = f"{service}.{catalog}.{schema}.{name}"
                payload = build_om_payload(om_table)

                existing = app_index.get(key)

                if existing is None:
                    # No exact-case match. Fall back to a case-insensitive
                    # lookup -- OM's casing for this table may simply have
                    # drifted since the row was first synced, and that's
                    # still the SAME table, not a new one.
                    candidates = [
                        c
                        for c in app_index_by_norm.get(norm_key, [])
                        if c.id not in matched_row_ids
                    ]
                    if candidates:
                        om_id = om_table.get("id")
                        exact_source_matches = [
                            c for c in candidates if c.oasis_source_id == om_id
                        ]
                        if exact_source_matches:
                            existing = exact_source_matches[0]
                        else:
                            candidates_sorted = sorted(candidates, key=lambda c: c.id)
                            existing = candidates_sorted[0]
                        if len(candidates) > 1:
                            others = ", ".join(
                                f"{c.service}.{c.catalog}.{c.schema_name}.{c.name}"
                                for c in candidates
                                if c.id != existing.id
                            )
                            logger.warning(
                                f"{len(candidates)} existing app rows match {key_str} "
                                f"case-insensitively -- updating "
                                f"{existing.service}.{existing.catalog}.{existing.schema_name}.{existing.name} "
                                f"as canonical and leaving pre-existing duplicate(s) [{others}] in place "
                                f"(pass --merge-case-duplicates to remove them)."
                            )

                is_spider = (
                    (catalog or "").lower() not in {"minio", "airlines", "admin_db"}
                    and (schema or "").lower() != "information_schema"
                    and not (om_table.get("id") or "").startswith(
                        PROTECTED_OASIS_SOURCE_ID_PREFIXES
                    )
                )

                # Only sync tables whose owner would be "spider2".
                # Skip system tables (minio, airlines, admin_db catalogs,
                # information_schema schema) entirely -- no update, no insert.
                if not is_spider:
                    logger.debug(f"Skipping system table {key_str}")
                    continue

                if existing is not None:
                    if (existing.oasis_source_id or "").startswith(
                        PROTECTED_OASIS_SOURCE_ID_PREFIXES
                    ):
                        logger.info(f"Skipping protected table {key_str}")
                        continue

                    matched_row_ids.add(existing.id)
                    # Realign casing to OM's current, canonical values. This
                    # is what prevents the next run from treating this same
                    # row as a miss and inserting yet another duplicate.
                    if (
                        existing.service,
                        existing.catalog,
                        existing.schema_name,
                        existing.name,
                    ) != key:
                        logger.info(
                            f"Realigning casing: "
                            f"{existing.service}.{existing.catalog}.{existing.schema_name}.{existing.name} "
                            f"-> {key_str}"
                        )
                    existing.service = service
                    existing.catalog = catalog
                    existing.schema_name = schema
                    existing.name = name
                    existing.openmetadata_json = payload
                    existing.owner_id = "spider2"
                    if recompute_embeddings:
                        embed_text = get_embedding_text(
                            payload["description"], payload["columns"]
                        )
                        embedding = get_embedding(embed_text)
                        if embedding is None or all(v == 0.0 for v in embedding):
                            logger.warning(
                                f"Embedding fell back to zero-vector for {key_str}"
                            )
                        existing.embedding = embedding
                    session.add(existing)
                    batch.append((existing, key_str))
                    logger.info(f"Queued update for {key_str}")
                else:
                    embed_text = get_embedding_text(
                        payload["description"], payload["columns"]
                    )
                    embedding = get_embedding(embed_text)
                    if embedding is None or all(v == 0.0 for v in embedding):
                        logger.warning(
                            f"Embedding fell back to zero-vector for {key_str}"
                        )
                    new_row = AppTable(
                        id=str(uuid.uuid4()),
                        name=name,
                        schema_name=schema,
                        catalog=catalog,
                        service=service,
                        status=status_for_catalog(catalog),
                        owner_id="spider2",
                        oasis_source_id=om_table.get("id"),
                        openmetadata_json=payload,
                        embedding=embedding,
                    )
                    session.add(new_row)
                    matched_row_ids.add(new_row.id)
                    batch.append((new_row, key_str))
                    logger.info(
                        f"Queued insert for {key_str} "
                        f"(status={new_row.status}, owner_id={new_row.owner_id}, "
                        f"oasis_source_id={new_row.oasis_source_id})"
                    )

                if len(batch) >= BATCH_SIZE:
                    succeeded, batch_failed = flush_batch(session, batch)
                    failed += batch_failed
                    logger.info(
                        f"Committed batch: {succeeded} ok, {batch_failed} failed"
                    )
                    batch = []

        # Flush any remaining rows in the final partial batch.
        succeeded, batch_failed = flush_batch(session, batch)
        failed += batch_failed
        if succeeded or batch_failed:
            logger.info(f"Committed final batch: {succeeded} ok, {batch_failed} failed")

        # Rows in the app DB that no OM table matched this run (not even
        # case-insensitively) -- candidates for ghost cleanup. Duplicates
        # (a second+ row that matched an OM table case-insensitively but
        # wasn't picked as canonical) are excluded here -- they're handled
        # separately below, since "no OM match at all" and "OM has this
        # table but we already have two rows for it" are different problems.
        unmatched = [
            t
            for t in app_tables
            if t.id not in matched_row_ids
            and not (t.oasis_source_id or "").startswith(
                PROTECTED_OASIS_SOURCE_ID_PREFIXES
            )
        ]

        normalized_om_keys = set()
        for om_index in om_by_service.values():
            for (catalog, schema, name), om_table in om_index.items():
                actual_service = (om_table.get("service") or {}).get("name") or ""
                normalized_om_keys.add(
                    normalize_key(actual_service, catalog, schema, name)
                )

        ghost_rows = [
            t
            for t in unmatched
            if normalize_key(t.service, t.catalog, t.schema_name, t.name)
            not in normalized_om_keys
        ]
        # Rows that DID match an OM table's normalized key but were excluded
        # from matched_row_ids because another row was already picked as
        # canonical for that same table this run -- i.e. genuine pre-existing
        # duplicates, not ghosts.
        duplicate_rows = [t for t in unmatched if t not in ghost_rows]

        # matched_row_ids includes both updated existing rows and newly
        # inserted rows; split them back out by checking which ids existed
        # before this run started.
        pre_run_ids = {t.id for t in app_tables}
        updated = len([rid for rid in matched_row_ids if rid in pre_run_ids])
        inserted = len([rid for rid in matched_row_ids if rid not in pre_run_ids])

        if not ghost_rows:
            logger.info("No ghost rows found.")
        else:
            logger.warning(
                f"Found {len(ghost_rows)} ghost row(s) with no OM match under any casing/service:"
            )
            for t in ghost_rows[:20]:
                logger.warning(f"  {t.service}.{t.catalog}.{t.schema_name}.{t.name}")
            if len(ghost_rows) > 20:
                logger.warning(f"  ...and {len(ghost_rows) - 20} more")

        merged, merge_failed = 0, 0

        if not duplicate_rows:
            logger.info("No pre-existing case-duplicate rows found.")
        elif not merge_case_duplicates:
            logger.warning(
                f"Found {len(duplicate_rows)} pre-existing duplicate row(s) that match an OM table "
                f"case-insensitively but weren't kept as the canonical row this run. NOT deleting -- "
                f"pass --merge-case-duplicates to remove them. Sample:"
            )
            for t in duplicate_rows[:20]:
                logger.warning(
                    f"  would delete: {t.service}.{t.catalog}.{t.schema_name}.{t.name}"
                )
            if len(duplicate_rows) > 20:
                logger.warning(f"  ...and {len(duplicate_rows) - 20} more")
        else:
            merge_batch: list[tuple] = []
            for t in duplicate_rows:
                key_str = f"{t.service}.{t.catalog}.{t.schema_name}.{t.name}"
                session.delete(t)
                merge_batch.append((t, key_str))
                logger.info(f"Merging (deleting) duplicate row: {key_str}")
                if len(merge_batch) >= BATCH_SIZE:
                    succeeded, batch_failed = flush_delete_batch(session, merge_batch)
                    merged += succeeded
                    merge_failed += batch_failed
                    merge_batch = []
            succeeded, batch_failed = flush_delete_batch(session, merge_batch)
            merged += succeeded
            merge_failed += batch_failed

        logger.info(
            f"\nDone. Updated {updated} existing rows, inserted {inserted} new rows, "
            f"{failed} rows failed to commit (see errors above). "
            f"Ghost rows (no OM match, reported only): {len(ghost_rows)}. "
            f"Case duplicates: {len(duplicate_rows)} found, {merged} merged/deleted, "
            f"{merge_failed} merge failures (pass --merge-case-duplicates to enable; without it, "
            f"they are reported only)."
        )

        # Task 2: Schema Sync for Spider2-Snow tables
        sync_spider_schemas(session, force=sync_schema)

        # Task 3: Golden Questions loading
        if load_questions:
            load_golden_questions(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--recompute-embeddings",
        action="store_true",
        help="Also regenerate the embedding column for EXISTING rows from the freshly-synced "
        "description/columns. New rows always get an embedding computed on insert.",
    )
    parser.add_argument(
        "--merge-case-duplicates",
        action="store_true",
        help="Delete pre-existing duplicate rows that match an OM table case-insensitively but "
        "were not kept as the canonical row (e.g. leftovers from before case-insensitive "
        "matching was added). Without this flag, duplicates are only reported.",
    )
    parser.add_argument(
        "--sync-schema",
        action="store_true",
        help="Force re-syncing of schemas (EnrichmentVersion) even if openmetadata_json columns are unchanged.",
    )
    parser.add_argument(
        "--load-golden-questions",
        action="store_true",
        help="Fetch Spider2-Snow golden questions + gold SQL live from GitHub and link them to "
        "Spider2-Snow tables in the DB.",
    )
    args = parser.parse_args()
    main(
        recompute_embeddings=args.recompute_embeddings,
        merge_case_duplicates=args.merge_case_duplicates,
        sync_schema=args.sync_schema,
        load_questions=args.load_golden_questions,
    )

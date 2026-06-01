"""
profiling_dag.py — Airflow DAG for scheduled data profiling via Trino.

Schedule: daily at 03:00 UTC for all active tables.
Supports manual trigger for on-demand re-profiling.

Requirements:
  - PROFILING_API_BASE_URL Airflow Variable: e.g. http://backend:8000
  - apache-airflow-providers-http
"""

import logging
from datetime import datetime, timedelta

import requests
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago

logger = logging.getLogger(__name__)

# ── DAG defaults ───────────────────────────────────────────────────────────────
DEFAULT_ARGS = {
    "owner": "text2sql-platform",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(hours=2),
}

API_BASE_URL = Variable.get(
    "PROFILING_API_BASE_URL", default_var="http://localhost:8000"
)
REQUEST_TIMEOUT = 300  # 5 min per table profiling call
FORCE_RERUN = False  # Set True in manual trigger to bypass cache


# ── Task functions ─────────────────────────────────────────────────────────────
def fetch_active_tables(**context) -> list[dict]:
    """Step 1: Fetch all tables from the Text2SQL API."""
    url = f"{API_BASE_URL}/tables"
    logger.info(f"[ProfilingDAG] Fetching tables from {url}")

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    tables = resp.json()

    # Filter to active tables (production or sandbox)
    active_tables = [
        t for t in tables if t.get("status") in ("production", "sandbox", "verified")
    ]
    logger.info(
        f"[ProfilingDAG] Found {len(active_tables)} active tables (of {len(tables)} total)"
    )

    # Push to XCom for downstream tasks
    context["ti"].xcom_push(key="active_tables", value=active_tables)
    return active_tables


def run_profiling_for_table(table: dict, force: bool = FORCE_RERUN) -> dict:
    """Trigger profiling for a single table. Returns the API response."""
    table_id = table["id"]
    table_name = table.get("name", table_id)
    url = f"{API_BASE_URL}/tables/{table_id}/profile/run"
    params = {"force": "true"} if force else {}

    logger.info(f"[ProfilingDAG] Triggering profiling: {table_name} ({table_id})")
    resp = requests.post(url, params=params, timeout=REQUEST_TIMEOUT)

    if resp.status_code in (200, 202):
        logger.info(
            f"[ProfilingDAG] ✓ {table_name} — profile queued (status={resp.status_code})"
        )
        return {"table_id": table_id, "status": "queued", "profile": resp.json()}
    else:
        logger.warning(
            f"[ProfilingDAG] ✗ {table_name} — HTTP {resp.status_code}: {resp.text[:200]}"
        )
        return {"table_id": table_id, "status": "failed", "error": resp.text}


def run_profiling_batch(**context):
    """Step 2: Run profiling for all active tables with partial-success handling."""
    active_tables = context["ti"].xcom_pull(
        key="active_tables", task_ids="fetch_active_tables"
    )
    if not active_tables:
        logger.warning("[ProfilingDAG] No active tables to profile — skipping")
        return

    results = {"queued": [], "failed": []}
    for table in active_tables:
        try:
            outcome = run_profiling_for_table(table)
            if outcome["status"] == "queued":
                results["queued"].append(outcome["table_id"])
            else:
                results["failed"].append(
                    {"table_id": outcome["table_id"], "error": outcome.get("error")}
                )
        except Exception as exc:
            table_id = table.get("id", "unknown")
            logger.error(f"[ProfilingDAG] Unexpected error for table {table_id}: {exc}")
            results["failed"].append({"table_id": table_id, "error": str(exc)})

    logger.info(
        f"[ProfilingDAG] Batch complete — "
        f"queued={len(results['queued'])}, failed={len(results['failed'])}"
    )
    if results["failed"]:
        logger.warning(f"[ProfilingDAG] Failed tables: {results['failed']}")

    context["ti"].xcom_push(key="profiling_results", value=results)

    # Raise if ALL tables failed (partial success is allowed)
    if results["failed"] and not results["queued"]:
        raise RuntimeError(
            f"All {len(results['failed'])} profiling jobs failed — see logs"
        )


def verify_profiling_results(**context):
    """Step 3: Verify at least some profiles completed successfully."""
    results = context["ti"].xcom_pull(
        key="profiling_results", task_ids="run_profiling_batch"
    )
    if not results:
        logger.warning("[ProfilingDAG] No results to verify")
        return

    queued = len(results.get("queued", []))
    failed = len(results.get("failed", []))
    total = queued + failed

    success_rate = queued / total if total > 0 else 0
    logger.info(
        f"[ProfilingDAG] Verification — {queued}/{total} tables profiled "
        f"(success rate: {success_rate:.0%})"
    )

    if success_rate < 0.5:
        raise RuntimeError(
            f"Profiling success rate too low: {success_rate:.0%} "
            f"({queued} ok, {failed} failed)"
        )

    logger.info("[ProfilingDAG] ✓ Profiling run verified successfully")


# ── DAG definition ─────────────────────────────────────────────────────────────
with DAG(
    dag_id="profiling_dag",
    description="Daily Trino-backed data profiling for all active TextToSQL tables",
    default_args=DEFAULT_ARGS,
    schedule_interval="0 3 * * *",  # Daily at 03:00 UTC
    start_date=days_ago(1),
    catchup=False,
    max_active_runs=1,
    tags=["profiling", "text2sql", "trino"],
    doc_md="""
## Profiling DAG

Runs the full data profiling pipeline daily:

1. **fetch_active_tables** — Fetches all production/sandbox/verified tables from the API
2. **run_profiling_batch** — Triggers Trino profiling for each table (partial-success tolerant)
3. **verify_profiling_results** — Validates ≥50% success rate

**Manual trigger**: Set `force=True` in `run_profiling_for_table` to bypass the 24h cache.
    """,
) as dag:

    task_fetch = PythonOperator(
        task_id="fetch_active_tables",
        python_callable=fetch_active_tables,
        doc_md="Fetch all active tables from the Text2SQL REST API.",
    )

    task_profile = PythonOperator(
        task_id="run_profiling_batch",
        python_callable=run_profiling_batch,
        doc_md="Trigger Trino-backed profiling for each active table (partial success OK).",
    )

    task_verify = PythonOperator(
        task_id="verify_profiling_results",
        python_callable=verify_profiling_results,
        doc_md="Assert ≥50% of tables were profiled successfully.",
    )

    task_fetch >> task_profile >> task_verify

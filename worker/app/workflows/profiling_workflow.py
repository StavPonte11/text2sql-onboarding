import asyncio
import logging
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import activity definitions and config bypassing sandbox restrictions
with workflow.unsafe.imports_passed_through():
    from app.config import settings
    from app.workflows.profiling_activities import (
        compute_chunk_metrics_activity,
        fetch_table_metadata_activity,
        generate_ai_summary_activity,
        persist_profiling_results_activity,
        profile_column_activity,
    )

logger = logging.getLogger(__name__)


@workflow.defn
class TableProfilingWorkflow:
    @workflow.run
    async def run(self, table_id: str, resume_from_partial: bool = False) -> None:
        logger.info("Running TableProfilingWorkflow for table_id: %s (resume=%s)", table_id, resume_from_partial)

        # 1. Fetch metadata activity
        metadata = await workflow.execute_activity(
            fetch_table_metadata_activity,
            {"table_id": table_id, "resume_from_partial": resume_from_partial},
            start_to_close_timeout=timedelta(seconds=settings.ACTIVITY_START_TO_CLOSE_TIMEOUT_SECONDS),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        catalog = metadata["catalog"]
        schema_name = metadata["schema_name"]
        table_name = metadata["table_name"]
        fqn = metadata["fqn"]
        row_count = metadata["row_count"]
        columns_meta = metadata["columns_meta"]
        sample_data = metadata["sample_data"]
        sample_size = metadata["sample_size"]
        profile_id = metadata["profile_id"]
        existing_columns = metadata.get("existing_columns", [])

        column_stats = []
        # Immediately append previously completed columns
        if existing_columns:
            column_stats.extend(existing_columns)

        errors = []
        failed_subtasks = []
        is_partial = False

        existing_col_names = {c["column_name"] for c in existing_columns}
        columns_meta_to_run = [c for c in columns_meta if c[0] not in existing_col_names]

        try:
            # If there are no columns or table is empty, we don't profile columns
            if row_count > 0 and columns_meta_to_run:
                # 2. Get precomputed metrics via chunked queries
                precomputed_metrics = {}
                try:
                    chunk_size = settings.PROFILING_CHUNK_SIZE
                    chunks = [columns_meta_to_run[i:i + chunk_size] for i in range(0, len(columns_meta_to_run), chunk_size)]

                    async def run_chunk(chunk):
                        return await workflow.execute_activity(
                            compute_chunk_metrics_activity,
                            {
                                "fqn": fqn,
                                "table_id": table_id,
                                "row_count": row_count,
                                "columns_chunk": chunk
                            },
                            start_to_close_timeout=timedelta(seconds=settings.ACTIVITY_START_TO_CLOSE_TIMEOUT_SECONDS),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )

                    chunk_tasks = [run_chunk(c) for c in chunks]
                    chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

                    for i, res in enumerate(chunk_results):
                        if isinstance(res, BaseException):
                            logger.error("Failed to run profiling chunk %d: %s", i, res)
                            is_partial = True
                            errors.append(f"Chunk profiling failed: {res}")
                            failed_subtasks.append(f"Profiling Chunk {i}")
                        else:
                            precomputed_metrics.update(res)
                except Exception as exc:
                    logger.error("Failed to run chunked profiling queries: %s", exc)
                    is_partial = True
                    errors.append(f"Chunked profiling setup failed: {exc}")
                    failed_subtasks.append("Chunked Profiling Setup")

                # Helper to run column profiling activity with error handling and retry policy
                async def run_col_profile(col):
                    col_name, data_type = col[0], col[1]
                    payload = {
                        "col_name": col_name,
                        "data_type": data_type,
                        "row_count": row_count,
                        "precomputed": precomputed_metrics.get(col_name),
                        "sample_data": sample_data,
                        "catalog": catalog,
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "table_id": table_id,
                    }
                    try:
                        res = await workflow.execute_activity(
                            profile_column_activity,
                            payload,
                            start_to_close_timeout=timedelta(seconds=settings.ACTIVITY_START_TO_CLOSE_TIMEOUT_SECONDS),
                            retry_policy=RetryPolicy(
                                initial_interval=timedelta(seconds=2),
                                backoff_coefficient=2.0,
                                maximum_attempts=3,
                            ),
                        )
                        return res, None
                    except Exception as exc:
                        return None, (col_name, str(exc))

                # 3. Schedule and run all column profiling in parallel
                tasks = [run_col_profile(col) for col in columns_meta_to_run]
                results = await asyncio.gather(*tasks)

                # Process results of concurrent activities
                for res, err in results:
                    if res is not None:
                        column_stats.append(res)
                        if res.get("errors"):
                            is_partial = True
                            failed_subtasks.append(f"Profile column with errors: {res.get('column_name')}")
                            for col_err in res.get("errors"):
                                errors.append(f"Column {res.get('column_name')} error: {col_err}")
                    elif err is not None:
                        col_name, error_msg = err
                        is_partial = True
                        failed_subtasks.append(f"Profile column: {col_name}")
                        errors.append(f"Column {col_name} failed: {error_msg}")
            elif row_count == 0:
                # Table is empty, build default stats for columns that aren't already existing
                for col in columns_meta_to_run:
                    col_name, data_type = col[0], col[1]
                    column_stats.append({
                        "column_name": col_name,
                        "data_type": data_type,
                        "null_count": 0,
                        "null_rate": 0.0,
                        "distinct_count": 0,
                        "semantic_type": "continuous",
                        "stats_json": {"type": "continuous", "distinct_count": 0, "null_rate": 0.0},
                        "errors": ["Table is empty"],
                    })

            # 4. Generate AI summary activity (skip if no columns completed or it's a completely failed run)
            ai_summary = ""
            if column_stats and not (is_partial and len(column_stats) == 0):
                summary_payload = {
                    "table_id": table_id,
                    "table_fqn": fqn,
                    "row_count": row_count,
                    "column_count": len(columns_meta),
                    "column_stats": column_stats,
                }
                try:
                    ai_summary = await workflow.execute_activity(
                        generate_ai_summary_activity,
                        summary_payload,
                        start_to_close_timeout=timedelta(seconds=settings.ACTIVITY_START_TO_CLOSE_TIMEOUT_SECONDS),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                except Exception as exc:
                    logger.warning("generate_ai_summary_activity failed: %s", exc)
                    is_partial = True
                    failed_subtasks.append("Generate AI Summary")
                    errors.append(f"AI Summary generation failed: {exc}")
        except Exception as exc:
            logger.error("Workflow failed with error: %s", exc)
            is_partial = True
            failed_subtasks.append("Workflow Execution Failed")
            errors.append(f"Workflow crashed: {exc!s}")
            raise
        finally:
            # 5. Persist results activity
            persist_payload = {
                "table_id": table_id,
                "profile_id": profile_id,
                "table_fqn": fqn,
                "row_count": row_count,
                "sample_size": sample_size,
                "column_count": len(columns_meta),
                "sample_data": sample_data,
                "column_stats": column_stats,
                "ai_summary": ai_summary,
                "is_partial": is_partial,
                "failed_subtasks": failed_subtasks,
                "errors": errors,
            }
            try:
                await workflow.execute_activity(
                    persist_profiling_results_activity,
                    persist_payload,
                    start_to_close_timeout=timedelta(seconds=settings.ACTIVITY_START_TO_CLOSE_TIMEOUT_SECONDS),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except Exception as p_exc:
                logger.error("Failed to persist results in finally block: %s", p_exc)

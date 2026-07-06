import asyncio
import concurrent.futures
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import settings
from app.workflows.profiling_activities import (
    compute_chunk_metrics_activity,
    fetch_table_metadata_activity,
    generate_ai_summary_activity,
    persist_profiling_results_activity,
    profile_column_activity,
)
from app.workflows.profiling_workflow import TableProfilingWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Connecting to Temporal server at %s", settings.TEMPORAL_HOST)

    client = None
    for attempt in range(1, 16):
        try:
            client = await Client.connect(settings.TEMPORAL_HOST)
            logger.info("Connected to Temporal server successfully!")
            break
        except Exception as e:
            logger.warning(
                "Attempt %d/15: Failed to connect to Temporal server at %s: %s. Retrying in 5 seconds...",
                attempt,
                settings.TEMPORAL_HOST,
                e
            )
            await asyncio.sleep(5)

    if not client:
        logger.error("Could not connect to Temporal server. Exiting.")
        raise ConnectionError("Failed to connect to Temporal server")

    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as activity_executor:
        worker = Worker(
            client,
            task_queue="profiling-tasks",
            workflows=[TableProfilingWorkflow],
            activities=[
                fetch_table_metadata_activity,
                compute_chunk_metrics_activity,
                profile_column_activity,
                generate_ai_summary_activity,
                persist_profiling_results_activity,
            ],
            activity_executor=activity_executor,
        )
        logger.info("Temporal profiling worker is running...")
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

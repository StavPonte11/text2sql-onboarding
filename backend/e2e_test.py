import argparse
import logging
import os
import sys

# Add the backend directory to python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.db.engine import engine
from core.models.models import Table, TableCreate
from sqlmodel import Session, select

from app.routers.tables import create_table
from app.services.profiling_engine import run_table_profiling

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def test_add_and_profile_table(source_id: str):
    logger.info(f"Starting E2E test for source ID / FQN: {source_id}")

    with Session(engine) as session:
        # 1. Clean up existing table with same source ID if any
        existing = session.exec(
            select(Table).where(Table.oasis_source_id == source_id)
        ).first()
        if existing:
            logger.info(
                f"Table with source_id '{source_id}' already exists. Deleting it for a clean test."
            )
            session.delete(existing)
            session.commit()

        # 2. Add table using the create_table router function
        logger.info("Adding table via create_table...")
        payload = TableCreate(oasis_source_id=source_id)

        try:
            new_table = create_table(payload=payload, session=session)
            logger.info(f"Table created successfully! ID: {new_table.id}")
            logger.info(
                f"Parsed FQN details -> Service: {new_table.service}, Catalog: {new_table.catalog}, Schema: {new_table.schema_name}, Name: {new_table.name}"
            )
        except Exception as e:
            logger.error(f"Failed to create table: {e}")
            sys.exit(1)

        # 3. Profile the table using the profiling engine function
        logger.info(
            f"Starting profiling for table {new_table.service}.{new_table.catalog}.{new_table.schema_name}.{new_table.name}..."
        )
        try:
            profile_result = run_table_profiling(
                table_id=new_table.id,
                catalog=new_table.catalog,
                schema=new_table.schema_name,
                table=new_table.name,
                version=1,
            )

            if profile_result.success:
                logger.info("Profiling Completed Successfully!")
                logger.info(f"Row count: {profile_result.row_count}")
                logger.info(f"Column count: {profile_result.column_count}")

                logger.info("--- Column Stats ---")
                for col_stat in profile_result.column_stats:
                    logger.info(
                        f" Column: {col_stat.column_name} | Type: {col_stat.data_type} | Nulls: {col_stat.null_count} | Distinct: {col_stat.distinct_count}"
                    )
                    if col_stat.min_value is not None or col_stat.max_value is not None:
                        logger.info(
                            f"   Min/Max: {col_stat.min_value} / {col_stat.max_value}"
                        )
                    if col_stat.errors:
                        logger.warning(f"   Errors: {col_stat.errors}")
            else:
                logger.error("Profiling Failed!")
                logger.error(f"Errors: {profile_result.errors}")

        except Exception as e:
            logger.error(f"Failed to run profiling: {e}")
            sys.exit(1)

        logger.info("E2E test completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="E2E test for adding and profiling a table."
    )
    parser.add_argument(
        "source_id", help="The oasis_source_id or exact FQN of the table to test."
    )
    args = parser.parse_args()

    test_add_and_profile_table(args.source_id)

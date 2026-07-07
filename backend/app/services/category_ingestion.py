import logging
from sqlmodel import Session, select

from core.models.models import LargeCategoryValue 
from app.config import settings
from core.trino import execute_query_sync
from app.services.profiling_engine import TableProfilingResult
from core.embeddings import get_embedding

logger = logging.getLogger(__name__)


def get_query_embedding(text: str) -> list[float] | None:
    """Generate 768-dimensional embedding from nomic-embed-text."""
    emb = get_embedding(
        text=text,
        embedder_url=settings.EMBEDDER_URL,
        embedder_model=settings.EMBEDDER_MODEL,
        embedder_key=settings.EMBEDDER_KEY,
    )
    if emb is None:
        logger.error(f"Error getting query embedding for text: {text}")
        return None  
    return emb


def ingest_large_category_values(db_session: Session, profile_result: TableProfilingResult, batch_size: int | None = None):
    """
    Finds 'large_categorical' columns from the profiling result, extracts unique values 
    from Trino, generates embeddings using the system embedder, and saves to Postgres.
    
    Args:
        batch_size: If provided, chunks the DB commits to prevent memory/transaction bloat.
                    If None, processes and commits all vectors in a single transaction.
    """
    # 1. Identify which columns the profiler flagged as large categories
    large_cat_cols = [
        c.column_name 
        for c in profile_result.column_stats 
        if c.semantic_type == "large_categorical"
    ]
    
    if not large_cat_cols:
        logger.info("[Ingestion] No large categories found for %s. Skipping.", profile_result.table_fqn)
        return

    for col_name in large_cat_cols:
        logger.info("[Ingestion] Extracting unique values for %s.%s", profile_result.table_fqn, col_name)
        
        # 2. Fetch distinct values directly from Trino
        query = f'SELECT DISTINCT "{col_name}" FROM {profile_result.table_fqn} WHERE "{col_name}" IS NOT NULL'
        trino_res = execute_query_sync(query, profile_result.table_id)
        
        if not trino_res.success or not trino_res.rows:
            logger.warning("[Ingestion] Trino returned no values for %s", col_name)
            continue
            
        trino_values = {str(row[0]) for row in trino_res.rows}
        
        # 3. Diff against PostgreSQL so we don't re-embed things we already have
        existing_stmt = select(LargeCategoryValue.value_text).where(
            LargeCategoryValue.table_id == profile_result.table_id,
            LargeCategoryValue.column_name == col_name
        )
        existing_values = set(db_session.exec(existing_stmt).all())
        
        new_values = list(trino_values - existing_values)
        if not new_values:
            logger.info("[Ingestion] No new values to embed for %s.", col_name)
            continue
                    
        if batch_size:
            logger.info("[Ingestion] Embedding %d new values for %s in batches of %d...", len(new_values), col_name, batch_size)
        else:
            logger.info("[Ingestion] Embedding %d new values for %s in a single transaction...", len(new_values), col_name)
        
        # Determine the loop step size: use batch_size if provided, else process all at once
        effective_batch = batch_size if batch_size and batch_size > 0 else len(new_values)

        # 4. Generate embeddings and build records
        total_saved = 0
        for i in range(0, len(new_values), effective_batch):
            batch = new_values[i : i + effective_batch]
            new_records = []
            
            for val in batch:
                emb = get_query_embedding(text=val)
                
                # skip if embedding failed 
                if emb is None:
                    continue

                record = LargeCategoryValue(
                    table_id=profile_result.table_id,
                    column_name=col_name,
                    value_text=val,
                    embedding=emb,
                    embedder_model=settings.EMBEDDER_MODEL 
                )
                new_records.append(record)
                
            # 5. Save the chunk to PostgreSQL
            if new_records:
                db_session.add_all(new_records)
                db_session.commit()
                total_saved += len(new_records)
                
                if batch_size:
                    logger.info("[Ingestion] Committed chunk of %d vectors for %s.", len(new_records), col_name)
        
        if not batch_size:
            logger.info("[Ingestion] Successfully saved %d vectors for %s.", total_saved, col_name)
            
    logger.info("[Ingestion] Finished embedding pipeline for %s.", profile_result.table_fqn)

    
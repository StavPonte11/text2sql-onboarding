from fastapi import APIRouter, Depends, HTTPException, Query, Header
from typing import Optional, List
from sqlmodel import Session, select, col
from datetime import datetime
from app.db.engine import get_session
from app.models.models import (
    Table, TableCreate, TableRead,
    TableStatus, UserScope, TableProfile, ProfilingStatus,
    EnrichmentVersion
)
import httpx
from app.config import settings
from fastapi import BackgroundTasks

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tables", tags=["tables"])

def get_fqn_from_source_id(oasis_source_id: str, default_fqn: str = None) -> str:
    if settings.TRINO_SERVICE_URL:
        try:
            url = f"{settings.TRINO_SERVICE_URL.rstrip('/')}/source-db/get_trino_full_names_by_ids"
            response = httpx.post(url, json=[oasis_source_id], timeout=10.0)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0]
        except Exception as e:
            logger.warning(f"Failed to fetch FQN from TRINO_SERVICE_URL: {e}")
    return default_fqn if default_fqn else oasis_source_id

def get_table_fqn(table: Table) -> str:
    return f"{table.service}.{table.catalog}.{table.schema_name}.{table.name}"

@router.get("", response_model=List[TableRead])
def list_tables(
    status: Optional[TableStatus] = Query(default=None),
    owner_id: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    x_scope_id: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
):
    q = select(Table)
    if status:
        q = q.where(Table.status == status)
    if owner_id:
        q = q.where(Table.owner_id == owner_id)
    if search:
        q = q.where(Table.name.ilike(f"%{search}%"))

    if x_scope_id:
        scope = session.get(UserScope, x_scope_id)
        if scope:
            if "marketing" in scope.name.lower():
                q = q.where(Table.name.ilike("%campaign%"))
            elif "finance" in scope.name.lower():
                q = q.where(Table.name.ilike("%order%"))

    return session.exec(q).all()


@router.get("/{table_id}", response_model=TableRead)
def get_table(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return table


@router.post("", response_model=TableRead, status_code=201)
def create_table(
    payload: TableCreate,
    session: Session = Depends(get_session)
):
    # Get the FQN using the helper function
    fqn = get_fqn_from_source_id(payload.oasis_source_id)

    try:
        # Instead of sending oasis_source_id into the URL, send the FQN
        url = f"{settings.OPENMETADATA_URL}/api/v1/tables/name/{fqn}?fields=columns"
        token = settings.OPENMETADATA_TOKEN
        response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch table metadata: {str(e)}")

    # Handle both old and new data structures
    metadata = data.get("metadata", data)
    name = metadata.get("name", data.get("name"))
    schema_name = metadata.get("databaseSchema", {}).get("name") if "databaseSchema" in metadata else data.get("databaseSchema", {}).get("name")
    service_name = metadata.get("service", {}).get("name") if "service" in metadata else data.get("service", {}).get("name")
    catalog_name = metadata.get("database", {}).get("name") if "database" in metadata else data.get("database", {}).get("name")

    description = metadata.get("description", data.get("description", ""))
    om_columns = metadata.get("columns", data.get("columns", []))

    # Create the table
    table = Table(
        name=name,
        schema_name=schema_name,
        catalog=catalog_name,
        service=service_name,
        owner_id="system",
        oasis_source_id=payload.oasis_source_id,
        openmetadata_json=data
    )
    session.add(table)
    session.commit()
    
    # Extract columns in the format expected by the frontend
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
    new_enrichment = EnrichmentVersion(
        table_id=table.id,
        version=1,
        data={
            "table_description": description,
            "columns": columns
        }
    )
    session.add(new_enrichment)
    session.commit()
    
    session.refresh(table)

    return table


@router.post("/{table_id}/sync-schema", response_model=TableRead)
def sync_table_schema(
    table_id: str,
    session: Session = Depends(get_session)
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    if not table.oasis_source_id:
        raise HTTPException(status_code=400, detail="Table has no oasis_source_id")

    # Get the FQN using the helper function, get the updated from open and if not exist get the last table fqn in the database
    fqn = get_fqn_from_source_id(table.oasis_source_id, default_fqn=get_table_fqn(table))

    try:
        url = f"{settings.OPENMETADATA_URL}/api/v1/tables/name/{fqn}?fields=columns"
        token = settings.OPENMETADATA_TOKEN
        response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to refetch table metadata: {str(e)}")

    # Handle both old and new data structures
    metadata = data.get("metadata", data)
    name = metadata.get("name", data.get("name"))
    schema_name = metadata.get("databaseSchema", {}).get("name") if "databaseSchema" in metadata else data.get("databaseSchema", {}).get("name")
    service_name = metadata.get("service", {}).get("name") if "service" in metadata else data.get("service", {}).get("name")
    catalog_name = metadata.get("database", {}).get("name") if "database" in metadata else data.get("database", {}).get("name")

    description = metadata.get("description", data.get("description", ""))
    om_columns = metadata.get("columns", data.get("columns", []))

    # Update the table
    table.name = name
    table.schema_name = schema_name
    table.catalog = catalog_name
    table.service = service_name
    table.openmetadata_json = data
    table.updated_at = datetime.utcnow()
    
    session.add(table)

    # Create/Update Enrichment
    # Extract columns in the format expected by the frontend
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

    # Bump version
    existing_enrichment = session.exec(
        select(EnrichmentVersion)
        .where(EnrichmentVersion.table_id == table_id)
        .order_by(col(EnrichmentVersion.version).desc())
    ).first()
    next_version = (existing_enrichment.version + 1) if existing_enrichment else 1

    new_enrichment = EnrichmentVersion(
        table_id=table_id,
        version=next_version,
        data={
            "table_description": description,
            "columns": columns
        }
    )
    session.add(new_enrichment)
    
    session.commit()
    session.refresh(table)

    return table


@router.patch("/{table_id}/status", response_model=TableRead)
def update_table_status(
    table_id: str,
    status: TableStatus,
    session: Session = Depends(get_session)
):
    """
    Update a table's status.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    previous_status = table.status
    table.status = status
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()
    session.refresh(table)

    from app.services.langfuse_client import langfuse_client
    from app.routers.evaluation import _build_questions_payload, PRODUCTION_DATASET_NAME
    from app.models.models import GoldenQuestion

    # Handle transitions
    if status == TableStatus.production and previous_status != TableStatus.production:
        logger.info(f"[Tables] Table '{table.name}' approved for production. Adding to dataset.")
        
        # Upsert golden questions to production dataset
        qs = session.exec(select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)).all()
        if qs:
            payload = _build_questions_payload(qs, table)
            langfuse_client.ensure_dataset_synced(PRODUCTION_DATASET_NAME, payload)
            
    elif status in [TableStatus.sandbox, TableStatus.degraded] and previous_status == TableStatus.production:
        logger.info(f"[Tables] Table '{table.name}' demoted from production -> {status.value}. Removing from dataset.")
        # Remove questions from production dataset
        langfuse_client.remove_table_questions_from_dataset(PRODUCTION_DATASET_NAME, table.id)

    return table

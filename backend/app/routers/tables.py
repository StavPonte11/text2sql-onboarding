import base64
import logging
from datetime import datetime

import httpx
from core.db.engine import get_session
from core.embeddings import get_embedding as core_get_embedding
from core.models.models import (
    EnrichmentVersion,
    ForeignKeyMapping,
    ForeignKeyMappingCreate,
    ForeignKeyMappingRead,
    GoldenQuestion,
    Table,
    TableCreate,
    TableRead,
    TableStatus,
    UserScope,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlmodel import Session, col, select

from app.config import settings
from app.routers.evaluation import PRODUCTION_DATASET_NAME, _build_questions_payload
from app.services.langfuse_client import langfuse_client

logger = logging.getLogger(__name__)


_cached_om_token: str | None = None


def get_om_token(force_refresh: bool = False) -> str:
    global _cached_om_token
    if _cached_om_token and not force_refresh:
        return _cached_om_token

    try:
        b64_password = base64.b64encode(
            settings.OPENMETADATA_ADMIN_PASSWORD.encode()
        ).decode()
        r = httpx.post(
            f"{settings.OPENMETADATA_URL}/api/v1/users/login",
            json={"email": settings.OPENMETADATA_ADMIN_EMAIL, "password": b64_password},
            timeout=10.0,
            verify=settings.OPENMETADATA_VERIFY_SSL,
        )
        r.raise_for_status()
        token = r.json().get("accessToken") or r.json().get("token", "")
        if token:
            _cached_om_token = token
            return token
    except Exception as e:
        logger.warning(f"Failed to dynamically log in to OpenMetadata: {e}")

    return settings.OPENMETADATA_TOKEN


def om_get_request(url: str) -> httpx.Response:
    token = get_om_token()
    headers = {"Authorization": f"Bearer {token}"}
    response = httpx.get(
        url, headers=headers, timeout=10.0, verify=settings.OPENMETADATA_VERIFY_SSL
    )

    if response.status_code == 401:
        logger.info(
            "OpenMetadata request returned 401. Retrying with a refreshed token..."
        )
        token = get_om_token(force_refresh=True)
        headers = {"Authorization": f"Bearer {token}"}
        response = httpx.get(
            url, headers=headers, timeout=10.0, verify=settings.OPENMETADATA_VERIFY_SSL
        )

    return response


router = APIRouter(prefix="/tables", tags=["tables"])


def get_fqn_from_source_id(oasis_source_id: str, default_fqn: str | None = None) -> str:
    if settings.TRINO_SERVICE_URL:
        try:
            url = f"{settings.TRINO_SERVICE_URL.rstrip('/')}/stages/get_stage_trino_full_name_by_ids?stage_name=pipeline&get_valid_table=true"
            response = httpx.post(
                url, json=[oasis_source_id], timeout=10.0, verify=False
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return f"{settings.OPENMETADATA_SERVICE_NAME}.{data[0]}"
        except Exception as e:
            logger.warning(f"Failed to fetch FQN from TRINO_SERVICE_URL: {e}")
    return default_fqn if default_fqn else oasis_source_id


def get_table_fqn(table: Table) -> str:
    return f"{table.service}.{table.catalog}.{table.schema_name}.{table.name}"


def get_embedding(text: str) -> list[float] | None:
    return core_get_embedding(
        text=text,
        embedder_url=settings.EMBEDDER_URL,
        embedder_model=settings.EMBEDDER_MODEL,
        embedder_key=settings.EMBEDDER_KEY,
    )


@router.get("", response_model=list[TableRead])
def list_tables(
    status: TableStatus | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    x_scope_id: str | None = Header(default=None),
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
def create_table(payload: TableCreate, session: Session = Depends(get_session)):
    # Get the FQN using the helper function
    fqn = get_fqn_from_source_id(payload.oasis_source_id)

    try:
        # Instead of sending oasis_source_id into the URL, send the FQN
        url = f"{settings.OPENMETADATA_URL}/api/v1/tables/name/{fqn}?fields=columns"
        response = om_get_request(url)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Table '{fqn}' not found in metadata repository.",
            )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Failed to fetch table metadata: {e!s}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to fetch table metadata: {e!s}"
        )

    # Handle both old and new data structures
    metadata = data.get("metadata", data)
    name = metadata.get("name", data.get("name"))
    schema_name = (
        metadata.get("databaseSchema", {}).get("name")
        if "databaseSchema" in metadata
        else data.get("databaseSchema", {}).get("name")
    )
    service_name = (
        metadata.get("service", {}).get("name")
        if "service" in metadata
        else data.get("service", {}).get("name")
    )
    catalog_name = (
        metadata.get("database", {}).get("name")
        if "database" in metadata
        else data.get("database", {}).get("name")
    )

    description = metadata.get("description", data.get("description", ""))
    om_columns = metadata.get("columns", data.get("columns", []))

    # Generate embedding
    text_to_embed = f"Table name: {name}\nSchema: {schema_name}\nDescription: {description}\nColumns: {', '.join([c.get('name', '') for c in om_columns])}"
    embedding = get_embedding(text_to_embed)

    # Check for duplicate table
    existing = session.exec(
        select(Table).where(
            Table.catalog == catalog_name,
            Table.schema_name == schema_name,
            Table.name == name
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Table '{catalog_name}.{schema_name}.{name}' already exists."
        )

    # Create the table
    table = Table(
        name=name,
        schema_name=schema_name,
        catalog=catalog_name,
        service=service_name,
        owner_id="system",
        oasis_source_id=payload.oasis_source_id,
        openmetadata_json=data,
        embedding=embedding,
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
        data={"table_description": description, "columns": columns},
    )
    session.add(new_enrichment)
    session.commit()

    session.refresh(table)

    return table


@router.post("/{table_id}/sync-schema", response_model=TableRead)
def sync_table_schema(table_id: str, session: Session = Depends(get_session)):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    if not table.oasis_source_id:
        raise HTTPException(status_code=400, detail="Table has no oasis_source_id")

    # Get the FQN using the helper function, get the updated from open and if not exist get the last table fqn in the database
    fqn = get_fqn_from_source_id(
        table.oasis_source_id, default_fqn=get_table_fqn(table)
    )

    try:
        url = f"{settings.OPENMETADATA_URL}/api/v1/tables/name/{fqn}?fields=columns"
        response = om_get_request(url)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Table '{fqn}' not found in metadata repository.",
            )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Failed to refetch table metadata: {e!s}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to refetch table metadata: {e!s}"
        )

    # Handle both old and new data structures
    metadata = data.get("metadata", data)
    name = metadata.get("name", data.get("name"))
    schema_name = (
        metadata.get("databaseSchema", {}).get("name")
        if "databaseSchema" in metadata
        else data.get("databaseSchema", {}).get("name")
    )
    service_name = (
        metadata.get("service", {}).get("name")
        if "service" in metadata
        else data.get("service", {}).get("name")
    )
    catalog_name = (
        metadata.get("database", {}).get("name")
        if "database" in metadata
        else data.get("database", {}).get("name")
    )

    description = metadata.get("description", data.get("description", ""))
    om_columns = metadata.get("columns", data.get("columns", []))

    # Generate embedding
    text_to_embed = f"Table name: {name}\nSchema: {schema_name}\nDescription: {description}\nColumns: {', '.join([c.get('name', '') for c in om_columns])}"
    embedding = get_embedding(text_to_embed)
    if embedding:
        table.embedding = embedding

    # Update the table
    table.name = name
    table.schema_name = schema_name
    table.catalog = catalog_name
    table.service = service_name
    table.openmetadata_json = data
    table.updated_at = datetime.now()

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
        data={"table_description": description, "columns": columns},
    )
    session.add(new_enrichment)

    session.commit()
    session.refresh(table)

    return table


@router.patch("/{table_id}/status", response_model=TableRead)
def update_table_status(
    table_id: str, status: TableStatus, session: Session = Depends(get_session)
):
    """
    Update a table's status.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    previous_status = table.status
    table.status = status
    table.updated_at = datetime.now()
    session.add(table)
    session.commit()
    session.refresh(table)

    # Handle transitions
    if status == TableStatus.production and previous_status != TableStatus.production:
        logger.info(
            f"[Tables] Table '{table.name}' approved for production. Adding to dataset."
        )

        # Upsert golden questions to production dataset
        qs = session.exec(
            select(GoldenQuestion).where(GoldenQuestion.table_id == table.id)
        ).all()
        if qs:
            payload = _build_questions_payload(qs, table)
            langfuse_client.ensure_dataset_synced(PRODUCTION_DATASET_NAME, payload)

    elif (
        status in [TableStatus.sandbox, TableStatus.degraded]
        and previous_status == TableStatus.production
    ):
        logger.info(
            f"[Tables] Table '{table.name}' demoted from production -> {status.value}. Removing from dataset."
        )
        # Remove questions from production dataset
        langfuse_client.remove_table_questions_from_dataset(
            PRODUCTION_DATASET_NAME, table.id
        )

    return table


# ─────────────────────────────────────────────────────────────────────────────
# FOREIGN KEY MAPPINGS
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{table_id}/foreign-keys", response_model=list[ForeignKeyMappingRead])
def get_foreign_keys(table_id: str, session: Session = Depends(get_session)):
    """
    Get all custom foreign key mappings for a table.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")

    return session.exec(
        select(ForeignKeyMapping).where(ForeignKeyMapping.table_id == table_id)
    ).all()


@router.post("/{table_id}/foreign-keys", response_model=ForeignKeyMappingRead)
def create_foreign_key(
    table_id: str,
    payload: ForeignKeyMappingCreate,
    session: Session = Depends(get_session),
):
    """
    Create or update a foreign key mapping for a specific column in the table.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Source table not found")

    target_table = session.get(Table, payload.target_table_id)
    if not target_table:
        raise HTTPException(status_code=404, detail="Target table not found")

    # Default to 1-to-1 mapping for a given source column (Upsert behavior)
    existing = session.exec(
        select(ForeignKeyMapping)
        .where(ForeignKeyMapping.table_id == table_id)
        .where(ForeignKeyMapping.source_column == payload.source_column)
    ).first()

    if existing:
        existing.target_table_id = payload.target_table_id
        existing.target_column = payload.target_column
        existing.updated_at = datetime.now()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    mapping = ForeignKeyMapping(
        table_id=table_id,
        source_column=payload.source_column,
        target_table_id=payload.target_table_id,
        target_column=payload.target_column,
    )
    session.add(mapping)
    session.commit()
    session.refresh(mapping)
    return mapping


@router.delete("/{table_id}/foreign-keys/{fk_id}", status_code=204)
def delete_foreign_key(
    table_id: str, fk_id: str, session: Session = Depends(get_session)
):
    """
    Delete a specific foreign key mapping.
    """
    mapping = session.get(ForeignKeyMapping, fk_id)
    if not mapping or mapping.table_id != table_id:
        raise HTTPException(status_code=404, detail="Foreign key mapping not found")

    session.delete(mapping)
    session.commit()

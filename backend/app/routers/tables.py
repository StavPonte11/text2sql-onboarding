from fastapi import APIRouter, Depends, HTTPException, Query, Header
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

router = APIRouter(prefix="/tables", tags=["tables"])


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
            # Mock filtering based on scope name
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
    # For testing, we use the oasis_source_id directly as the OpenMetadata FQN
    fqn = payload.oasis_source_id

    try:
        # Instead of sending oasis_source_id into the URL, send the FQN
        url = f"{settings.OPENMETADATA_URL}/api/v1/tables/name/{fqn}?fields=columns"
        token = "eyJraWQiOiJHYjM4OWEtOWY3Ni1nZGpzLWE5MmotMDI0MmJrOTQzNTYiLCJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJvcGVuLW1ldGFkYXRhLm9yZyIsInN1YiI6InByb2ZpbGVyLWJvdCIsInJvbGVzIjpbIlByb2ZpbGVyQm90Um9sZSJdLCJlbWFpbCI6InByb2ZpbGVyLWJvdEBvcGVuLW1ldGFkYXRhLm9yZyIsImlzQm90Ijp0cnVlLCJ0b2tlblR5cGUiOiJCT1QiLCJpYXQiOjE3Nzg3NDUyMDEsImV4cCI6bnVsbH0.nZr-FXxHEscRjzz2z-cE2NDTtIuTlAsDdeQ5hu_QVdB7j5bYj7xTmVettbyAT1rP1FHZgrCNb7R_TblLLtL_coyZSJWfKWoJoD82snkn3wc9fIHIYfktHUejU-UHM_DTIzx51qU2O-tbQT8L9FZWbSJQkbTvHDYKVxuERD26xcx-cQ3TSD87RIzw7b7m4ailKp4RUattt__jI0bz02cS4orJgptSpr0WG6ePmTMmlElcoUTZHBWtAwe1bL63lQlloKYJCYkX93Iy-eIEFnHf-YzS4NopwfKDrqbyZNWqR_GHxLDanf3Ylhb_WyB0zCVbtwImBBLdLcB3w9sgZBue-A"
        response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch table metadata: {str(e)}")

    # Handle both old and new data structures
    metadata = data.get("metadata", data)
    name = metadata.get("name", data.get("name"))
    schema_name = metadata.get("databaseSchema", {}).get("name") if "databaseSchema" in metadata else data.get("databaseSchema", {}).get("name")
    description = metadata.get("description", data.get("description", ""))
    om_columns = metadata.get("columns", data.get("columns", []))

    # Create the table
    table = Table(
        name=name,
        schema_name=schema_name,
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

    # Stub for the inner URL service that converts oasis_source_id to FQN
    # We use oasis_source_id directly as the FQN for testing
    fqn = table.oasis_source_id

    try:
        url = f"{settings.OPENMETADATA_URL}/api/v1/tables/name/{fqn}?fields=columns"
        token = "eyJraWQiOiJHYjM4OWEtOWY3Ni1nZGpzLWE5MmotMDI0MmJrOTQzNTYiLCJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJvcGVuLW1ldGFkYXRhLm9yZyIsInN1YiI6InByb2ZpbGVyLWJvdCIsInJvbGVzIjpbIlByb2ZpbGVyQm90Um9sZSJdLCJlbWFpbCI6InByb2ZpbGVyLWJvdEBvcGVuLW1ldGFkYXRhLm9yZyIsImlzQm90Ijp0cnVlLCJ0b2tlblR5cGUiOiJCT1QiLCJpYXQiOjE3Nzg3NDUyMDEsImV4cCI6bnVsbH0.nZr-FXxHEscRjzz2z-cE2NDTtIuTlAsDdeQ5hu_QVdB7j5bYj7xTmVettbyAT1rP1FHZgrCNb7R_TblLLtL_coyZSJWfKWoJoD82snkn3wc9fIHIYfktHUejU-UHM_DTIzx51qU2O-tbQT8L9FZWbSJQkbTvHDYKVxuERD26xcx-cQ3TSD87RIzw7b7m4ailKp4RUattt__jI0bz02cS4orJgptSpr0WG6ePmTMmlElcoUTZHBWtAwe1bL63lQlloKYJCYkX93Iy-eIEFnHf-YzS4NopwfKDrqbyZNWqR_GHxLDanf3Ylhb_WyB0zCVbtwImBBLdLcB3w9sgZBue-A"
        response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to refetch table metadata: {str(e)}")

    # Handle both old and new data structures
    metadata = data.get("metadata", data)
    name = metadata.get("name", data.get("name"))
    schema_name = metadata.get("databaseSchema", {}).get("name") if "databaseSchema" in metadata else data.get("databaseSchema", {}).get("name")
    description = metadata.get("description", data.get("description", ""))
    om_columns = metadata.get("columns", data.get("columns", []))

    # Update the table
    table.name = name
    table.schema_name = schema_name
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

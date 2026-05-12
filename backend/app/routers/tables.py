from fastapi import APIRouter, Depends, HTTPException, Query, Header
from typing import Optional, List

from sqlmodel import Session, select
from datetime import datetime
from app.db.engine import get_session
from app.models.models import (
    Table, TableCreate, TableRead,
    TableStatus, UserScope
)

router = APIRouter(prefix="/tables", tags=["tables"])


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
def create_table(payload: TableCreate, session: Session = Depends(get_session)):
    table = Table(**payload.model_dump())
    session.add(table)
    session.commit()
    session.refresh(table)
    return table
@router.patch("/{table_id}/status", response_model=TableRead)
def update_table_status(
    table_id: str, 
    status: TableStatus, 
    session: Session = Depends(get_session)
):
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
    table.status = status
    table.updated_at = datetime.utcnow()
    session.add(table)
    session.commit()
    session.refresh(table)
    return table

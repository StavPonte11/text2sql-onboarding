from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, desc
from pydantic import BaseModel
from app.db.engine import get_session
from app.models.models import Table, TableStatus, Admin, EvalRun, EvalStatus
from app.services.auth import get_current_admin

router = APIRouter(prefix="/admin/tables", tags=["admin_approval"])

class RejectionNote(BaseModel):
    note: str

@router.get("/pending", response_model=List[dict])
def get_pending_tables(
    current_admin: Admin = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Get all tables that are in verified status (awaiting approval)."""
    tables = session.exec(select(Table).where(Table.status == TableStatus.verified)).all()
    
    result = []
    for table in tables:
        latest_run = session.exec(
            select(EvalRun)
            .where(EvalRun.table_id == table.id, EvalRun.status == EvalStatus.completed)
            .order_by(desc(EvalRun.created_at))
        ).first()
        
        result.append({
            "id": table.id,
            "name": table.name,
            "schema_name": table.schema_name,
            "status": table.status,
            "latest_run": {
                "score": latest_run.score if latest_run else None,
                "pass_rate": latest_run.pass_rate if latest_run else None,
                "created_at": latest_run.created_at if latest_run else None,
            } if latest_run else None
        })
    return result

@router.post("/{table_id}/approve")
def approve_table(
    table_id: str,
    current_admin: Admin = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Approve a verified table for production."""
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
    if table.status != TableStatus.verified:
        raise HTTPException(
            status_code=400, 
            detail=f"Table must be in 'verified' status to be approved. Current status: {table.status}"
        )
    
    table.status = TableStatus.production
    # Could also log who approved it in an audit log here
    session.add(table)
    session.commit()
    
    return {"message": "Table approved for production", "table_id": table_id, "status": table.status}

@router.post("/{table_id}/reject")
def reject_table(
    table_id: str,
    rejection: RejectionNote,
    current_admin: Admin = Depends(get_current_admin),
    session: Session = Depends(get_session)
):
    """Reject a verified table, sending it back to sandbox."""
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
    if table.status != TableStatus.verified:
        raise HTTPException(
            status_code=400, 
            detail=f"Table must be in 'verified' status to be rejected. Current status: {table.status}"
        )
    
    table.status = TableStatus.sandbox
    # Log rejection note in audit log or feedback table if desired
    session.add(table)
    session.commit()
    
    return {
        "message": "Table rejected and returned to sandbox", 
        "table_id": table_id, 
        "status": table.status,
        "note": rejection.note
    }

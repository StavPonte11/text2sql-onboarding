import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from datetime import datetime, timezone
import uuid

from core.models.models import (
    Table,
    TableProfile,
    ProfilingRun,
    ProfilingStatus,
    ColumnProfile,
    SecurityUser,
)
from app.main import app

@pytest.fixture
def table_id(db_session: Session) -> str:
    # Ensure test user exists
    user = db_session.get(SecurityUser, "test-user-id")
    if not user:
        user = SecurityUser(
            id="test-user-id",
            email="test-user@example.com",
            name="Test User",
            is_active=True,
            is_admin=True,
        )
        db_session.add(user)
        db_session.commit()

    # Create a test table
    t = Table(
        id=str(uuid.uuid4()),
        catalog="test_catalog",
        schema_name="test_schema",
        name="test_table",
        owner_id=user.id,
        oasis_source_id="test-source",
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t.id

def test_get_profile_not_found(client: TestClient, table_id: str):
    response = client.get(f"/api/tables/{table_id}/profile")
    assert response.status_code == 404

@patch("app.routers.profiling.trigger_temporal_profiling_workflow", new_callable=AsyncMock)
def test_start_profiling_run(mock_trigger, client: TestClient, db_session: Session, table_id: str):
    mock_trigger.return_value = True

    response = client.post(f"/api/tables/{table_id}/profile/run")
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["table_id"] == table_id

    # Verify database state
    runs = db_session.exec(
        select(ProfilingRun).where(ProfilingRun.table_id == table_id)
    ).all()
    assert len(runs) == 1
    assert runs[0].status == ProfilingStatus.pending

@patch("app.routers.profiling.Client.connect", new_callable=AsyncMock)
def test_terminate_profiling_run(mock_connect, client: TestClient, db_session: Session, table_id: str):
    # Setup mock temporal client
    mock_client = MagicMock()
    mock_handle = AsyncMock()
    mock_client.get_workflow_handle.return_value = mock_handle
    mock_connect.return_value = mock_client

    # Add a running profiling run
    run = ProfilingRun(table_id=table_id, status=ProfilingStatus.running)
    db_session.add(run)
    db_session.commit()

    # Terminate the run
    response = client.post(f"/api/tables/{table_id}/profile/terminate")
    assert response.status_code == 200

    # Verify run is canceled
    db_session.refresh(run)
    assert run.status == ProfilingStatus.canceled



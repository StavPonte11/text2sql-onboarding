from unittest.mock import MagicMock, patch

from core.models.models import (
    AuditQuery,
    EvalStatus,
    GoldenQuestion,
    Table,
    TableStatus,
    EnrichmentVersion
)

# ── Mock objects for testing ──────────────────────────────────────────────────
MOCK_OM_METADATA = {
    "name": "ecommerce_orders",
    "databaseSchema": {"name": "ecommerce"},
    "service": {"name": "local_trino"},
    "database": {"name": "minio"},
    "description": "Orders table for testing",
    "columns": [
        {"name": "order_id", "description": "Primary key", "dataType": "VARCHAR"},
        {
            "name": "user_id",
            "description": "Foreign key for users",
            "dataType": "VARCHAR",
        },
        {
            "name": "total_amount",
            "description": "Total purchase cost",
            "dataType": "DOUBLE",
        },
    ],
}


# ── Base health test ───────────────────────────────────────────────────────────
def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── Tables API Tests ──────────────────────────────────────────────────────────
@patch("app.routers.tables.httpx.get")
@patch("app.routers.tables.get_fqn_from_source_id")
def test_create_table(mock_fqn, mock_get, client, db_session):
    mock_fqn.return_value = "local_trino.minio.ecommerce.ecommerce_orders"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = MOCK_OM_METADATA
    mock_get.return_value = mock_response

    payload = {"oasis_source_id": "test-source-id-123"}
    response = client.post("/api/tables", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "ecommerce_orders"
    assert data["schema_name"] == "ecommerce"
    assert data["catalog"] == "minio"
    assert data["service"] == "local_trino"
    assert data["oasis_source_id"] == "test-source-id-123"

    # Verify database persistence
    table = db_session.get(Table, data["id"])
    assert table is not None
    assert table.status == TableStatus.draft


def test_list_tables(client, db_session):
    # Add dummy tables to database
    t1 = Table(
        name="test_1",
        schema_name="s",
        owner_id="u",
        oasis_source_id="o1",
        status=TableStatus.draft,
    )
    t2 = Table(
        name="test_2",
        schema_name="s",
        owner_id="u",
        oasis_source_id="o2",
        status=TableStatus.sandbox,
    )
    db_session.add(t1)
    db_session.add(t2)
    db_session.commit()

    response = client.get("/api/tables")
    assert response.status_code == 200
    assert len(response.json()) >= 2

    # Test filtering by status
    response = client.get("/api/tables", params={"status": "sandbox"})
    assert response.status_code == 200
    results = response.json()
    assert all(r["status"] == "sandbox" for r in results)


def test_get_table_not_found(client):
    response = client.get("/api/tables/non-existent-uuid")
    assert response.status_code == 404
    assert response.json()["detail"] == "Table not found"


# ── Scopes API Tests ──────────────────────────────────────────────────────────
def test_create_and_list_scopes(client, db_session):
    payload = {"user_id": "test-user", "name": "Marketing Team"}
    response = client.post("/api/scopes", json=payload)
    assert response.status_code == 201

    scope_data = response.json()
    assert scope_data["name"] == "Marketing Team"
    assert scope_data["is_active"] is False

    # List scopes
    response = client.get("/api/scopes")
    assert response.status_code == 200
    assert len(response.json()) >= 1

    # Activate scope
    response = client.post(f"/api/scopes/{scope_data['id']}/activate")
    assert response.status_code == 200
    assert response.json()["is_active"] is True


# ── Questions API Tests ────────────────────────────────────────────────────────
def test_question_lifecycle(client, db_session):
    # Seed a table
    table = Table(
        name="orders",
        schema_name="s",
        owner_id="u",
        oasis_source_id="o",
        status=TableStatus.draft,
    )
    db_session.add(table)
    db_session.commit()
    db_session.refresh(table)

    # Create question
    payload = {
        "question": "Show all orders last month",
        "expected_sql": "SELECT * FROM orders WHERE date > now()",
        "difficulty": "simple",
        "question_type": "simple",
    }
    response = client.post(f"/api/tables/{table.id}/questions", json=payload)
    assert response.status_code == 201
    q_data = response.json()
    assert q_data["question"] == "Show all orders last month"

    # List questions
    response = client.get(f"/api/tables/{table.id}/questions")
    assert response.status_code == 200
    assert len(response.json()) == 1

    # Delete question
    response = client.delete(f"/api/tables/{table.id}/questions/{q_data['id']}")
    assert response.status_code == 204


# ── Evaluation API Tests ───────────────────────────────────────────────────────
@patch("app.routers.evaluation.langfuse_client")
def test_trigger_evaluation_run(mock_lf, client, db_session):
    mock_lf.enabled = False  # mock client disabled

    # Create Table
    table = Table(
        name="sales",
        schema_name="s",
        owner_id="u",
        oasis_source_id="o",
        status=TableStatus.draft,
    )
    db_session.add(table)
    db_session.commit()
    db_session.refresh(table)

    # Seed an enrichment version (required by the validation check)
    enrichment = EnrichmentVersion(
        table_id=table.id,
        version=1,
        data={
            "table_description": "Sales table for testing",
            "columns": [{"name": "id", "dataType": "INT"}],
        },
    )
    db_session.add(enrichment)

    # Add golden questions (required to trigger evaluation run)
    q = GoldenQuestion(table_id=table.id, question="Q?", expected_sql="SELECT 1")
    db_session.add(q)
    db_session.commit()

    response = client.post(f"/api/tables/{table.id}/eval/run")
    assert response.status_code == 202
    run_data = response.json()
    assert run_data["status"] == EvalStatus.running
    assert run_data["table_name"] == "sales"


# ── Audit API Tests ────────────────────────────────────────────────────────────
def test_list_audit_queries(client, db_session):
    audit = AuditQuery(
        user_id="test-user", raw_question="GET /tables", status="success"
    )
    db_session.add(audit)
    db_session.commit()

    response = client.get("/api/audit/queries")
    assert response.status_code == 200
    assert len(response.json()) >= 1

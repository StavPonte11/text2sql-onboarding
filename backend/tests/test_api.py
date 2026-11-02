from unittest.mock import MagicMock, patch

from core.models.models import (
    AuditQuery,
    EnrichmentVersion,
    EvalStatus,
    GoldenQuestion,
    Table,
    TableStatus,
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

    response = client.post(f"/api/tables/{table.id}/eval/run?connection_id=89")
    assert response.status_code == 202
    run_data = response.json()
    assert run_data["status"] == EvalStatus.running
    assert run_data["table_name"] == "sales"
    assert run_data["connection_id"] == 89


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


# ── Query API Tests ────────────────────────────────────────────────────────────
@patch("app.routers.query.settings")
def test_execute_query_disabled(mock_settings, client):
    mock_settings.TRINO_ENABLED = False
    payload = {"sql": "SELECT * FROM users LIMIT 10"}
    response = client.post("/api/query/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["rows"] == []
    assert data["columns"] == []
    assert data["row_count"] == 0
    assert data["error"] is not None
    assert "disabled" in data["error"]


@patch("app.routers.query.get_trino_connection")
@patch("app.routers.query.settings")
def test_execute_query_success(mock_settings, mock_get_conn, client):
    mock_settings.TRINO_ENABLED = True

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.description = [("id",), ("name",), ("email",)]
    mock_cur.fetchall.return_value = [
        (1, "Alice", "alice@example.com"),
        (2, "Bob", "bob@example.com"),
    ]
    mock_conn.cursor.return_value = mock_cur
    mock_get_conn.return_value = mock_conn

    payload = {"sql": "SELECT id, name, email FROM users"}
    response = client.post("/api/query/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["columns"] == ["id", "name", "email"]
    assert data["rows"] == [
        [1, "Alice", "alice@example.com"],
        [2, "Bob", "bob@example.com"],
    ]
    assert data["row_count"] == 2
    assert data["error"] is None
    assert isinstance(data["execution_time_ms"], float)


@patch("app.routers.query.get_trino_connection")
@patch("app.routers.query.settings")
def test_execute_query_failure(mock_settings, mock_get_conn, client):
    mock_settings.TRINO_ENABLED = True

    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.execute.side_effect = Exception("Syntax error near 'LIMIT'")
    mock_conn.cursor.return_value = mock_cur
    mock_get_conn.return_value = mock_conn

    payload = {"sql": "SELECT id FROM users LIMIT"}
    response = client.post("/api/query/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["rows"] == []
    assert data["columns"] == []
    assert data["row_count"] == 0
    assert data["error"] is not None
    assert "Syntax error" in data["error"]


# ── Connection Golden Questions via Jeen Metadata ──────────────────────────────
@patch("app.routers.questions.fetch_jeen_sql_pairs")
def test_list_connection_questions_from_jeen(mock_fetch_jeen, client):
    mock_fetch_jeen.return_value = [
        {
            "question": "What are sales by category?",
            "sql_statement": "SELECT category, SUM(amount) FROM sales GROUP BY category",
            "category": "sales",
        }
    ]
    response = client.get("/api/connections/89/questions")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["question"] == "What are sales by category?"
    assert "SELECT category" in data[0]["expected_sql"]
    assert data[0]["connection_id"] == 89


@patch("app.routers.questions.save_jeen_sql_pair")
def test_create_and_update_connection_question(mock_save_jeen, client):
    mock_save_jeen.return_value = {"status": "ok"}

    # Create
    payload = {
        "question": "How many orders were placed today?",
        "expected_sql": "SELECT COUNT(*) FROM orders WHERE created_at >= CURRENT_DATE",
        "difficulty": "simple",
        "question_type": "simple",
    }
    response = client.post("/api/connections/89/questions", json=payload)
    assert response.status_code == 201
    created = response.json()
    assert created["question"] == payload["question"]
    assert created["expected_sql"] == payload["expected_sql"]
    assert created["connection_id"] == 89
    mock_save_jeen.assert_called_once()

    # Update
    qid = created["id"]
    update_payload = {
        "question": "How many orders were placed today?",
        "expected_sql": "SELECT COUNT(*) FROM orders WHERE date(created_at) = CURRENT_DATE",
        "difficulty": "medium",
        "question_type": "aggregate",
    }
    update_resp = client.put(
        f"/api/connections/89/questions/{qid}", json=update_payload
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["id"] == qid
    assert updated["expected_sql"] == update_payload["expected_sql"]
    assert updated["difficulty"] == "medium"

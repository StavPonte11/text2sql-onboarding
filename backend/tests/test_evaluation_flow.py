import pytest
from unittest.mock import patch, MagicMock

from sqlmodel import Session
from app.routers.evaluation import execute_single_table_eval
from core.models.models import EvalRun, Table, GoldenQuestion, TableStatus, EvalStatus

@pytest.fixture
def mock_session():
    session = MagicMock(spec=Session)
    return session

def test_execute_single_table_eval_success(mock_session):
    run_id = "test-run-id"
    table_id = "test-table-id"
    
    # Setup mock data
    mock_run = MagicMock(spec=EvalRun)
    mock_run.total_questions = 0
    mock_run.id = run_id
    
    mock_table = MagicMock(spec=Table)
    mock_table.id = table_id
    mock_table.catalog = "minio"
    mock_table.schema_name = "test_schema"
    mock_table.name = "test_table"
    mock_table.status = TableStatus.draft
    
    mock_q1 = MagicMock(spec=GoldenQuestion)
    mock_q1.id = "q1"
    mock_q1.question = "Show me the data"
    mock_q1.expected_sql = "SELECT * FROM test_table"
    mock_q1.table_id = table_id
    mock_q1.question_type = "basic"
    mock_q1.difficulty = "easy"
    
    def mock_get(model, id):
        if model == EvalRun and id == run_id:
            return mock_run
        if model == Table and id == table_id:
            return mock_table
        return None
    
    mock_session.get.side_effect = mock_get
    
    # Mock exec().all() to return a list of GoldenQuestions
    mock_exec_result = MagicMock()
    mock_exec_result.all.return_value = [mock_q1]
    mock_session.exec.return_value = mock_exec_result

    with patch("app.routers.evaluation.requests.post") as mock_post:
        # Mock successful evaluation service response
        mock_post_response = MagicMock()
        mock_post_response.raise_for_status.return_value = None
        mock_post.return_value = mock_post_response
        
        with patch("app.routers.evaluation.langfuse_client.ensure_dataset_synced"):
            with patch("app.routers.evaluation._map_and_save_run_metrics") as mock_map:
                with patch("app.routers.evaluation.RunDatasetResponse") as mock_response_class:
                    
                    # Create a mock response object that has the fields accessed by execute_single_table_eval
                    mock_eval_resp = MagicMock()
                    mock_eval_resp.accuracy.contains_accuracy = 1.0
                    mock_eval_resp.accuracy.execution_accuracy = 1.0
                    mock_eval_resp.accuracy.sql_exact_match = 1.0
                    mock_eval_resp.total_cases = 1
                    mock_eval_resp.failure_rate = 0.0
                    mock_response_class.return_value = mock_eval_resp
                    
                    score = execute_single_table_eval(table_id, run_id, mock_session)
                    
                    # Verify requests.post was called with correct payload
                    mock_post.assert_called_once()
                    args, kwargs = mock_post.call_args
                    assert "json" in kwargs
                    assert kwargs["json"]["dataset_name"] == f"text2sql_sandbox_{table_id}"
                    assert kwargs["json"]["additional_tables"] == ["minio.test_schema.test_table"]
                    
                    # Verify run mapping was called
                    mock_map.assert_called_once()
                    
                    # Verify run table state was changed from draft to sandbox
                    assert mock_table.status == TableStatus.sandbox
                
                # Return value is the contains_accuracy or similar
                # execute_single_table_eval currently returns nothing or float?
                # Actually _map_and_save_run_metrics sets run.score. Let's assume it returns normally.

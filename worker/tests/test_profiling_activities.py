import pytest
from unittest.mock import patch, MagicMock
from app.workflows.profiling_activities import persist_profiling_results_activity, PersistResultsParams
from core.models.models import ProfilingRun, ProfilingStatus, TableProfile
from datetime import datetime, timezone
import uuid

def test_persist_results_fatal_error():
    params = PersistResultsParams(
        table_id=str(uuid.uuid4()),
        profile_id=str(uuid.uuid4()),
        table_fqn="catalog.schema.table",
        row_count=100,
        sample_size=10,
        column_count=2,
        sample_data=[],
        column_stats=[],
        is_partial=True,
        errors=["Workflow crashed: something bad happened"]
    )

    with patch("app.workflows.profiling_activities.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        latest_run = ProfilingRun(table_id=params.table_id, status=ProfilingStatus.running)
        mock_session.exec.return_value.first.return_value = latest_run

        persist_profiling_results_activity(params)

        # Ensure TableProfile was not created or added
        # Ensure status was changed to failed
        assert latest_run.status == ProfilingStatus.failed
        mock_session.add.assert_called_once_with(latest_run)
        mock_session.commit.assert_called_once()

def test_persist_results_canceled():
    params = PersistResultsParams(
        table_id=str(uuid.uuid4()),
        profile_id=str(uuid.uuid4()),
        table_fqn="catalog.schema.table",
        row_count=100,
        sample_size=10,
        column_count=2,
        sample_data=[],
        column_stats=[],
        is_partial=True,
        errors=["Workflow crashed: Canceled by user"]
    )

    with patch("app.workflows.profiling_activities.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        latest_run = ProfilingRun(table_id=params.table_id, status=ProfilingStatus.canceled)
        mock_session.exec.return_value.first.return_value = latest_run

        persist_profiling_results_activity(params)

        # Status should remain canceled, not overwritten to failed
        assert latest_run.status == ProfilingStatus.canceled
        mock_session.add.assert_called_once_with(latest_run)
        mock_session.commit.assert_called_once()

def test_persist_results_success():
    params = PersistResultsParams(
        table_id=str(uuid.uuid4()),
        profile_id=str(uuid.uuid4()),
        table_fqn="catalog.schema.table",
        row_count=100,
        sample_size=10,
        column_count=2,
        sample_data=[],
        column_stats=[],
        is_partial=False,
        errors=[]
    )

    with patch("app.workflows.profiling_activities.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        latest_run = ProfilingRun(table_id=params.table_id, status=ProfilingStatus.running)
        mock_session.exec.return_value.first.side_effect = [None, latest_run]
        
        mock_session.get.return_value = None

        persist_profiling_results_activity(params)

        # Status should be completed
        assert latest_run.status == ProfilingStatus.completed
        
        # We should have three additions: TableProfile (creation), TableProfile (update), and ProfilingRun
        assert mock_session.add.call_count == 3
        
        args, _ = mock_session.add.call_args_list[-1]
        assert isinstance(args[0], ProfilingRun)

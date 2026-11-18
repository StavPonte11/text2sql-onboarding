"""Dataset items keep the expected SQL under "sql" only."""

from unittest.mock import MagicMock, patch

from app.services.langfuse_client import LangfuseDatasetService

QUESTION = {"question_id": "q1", "question_text": "How many?", "expected_sql": "SELECT 1"}


def _sync(existing_items):
    tracer = MagicMock(public_key="pk", private_key="sk", host="https://lf.test")
    service = LangfuseDatasetService(tracer)
    listing = MagicMock(status_code=200, **{"json.return_value": {"data": existing_items}})
    with (
        patch("app.services.langfuse_client.requests.get", return_value=listing),
        patch("app.services.langfuse_client.requests.delete") as delete,
        patch("app.services.langfuse_client.time.sleep"),
    ):
        service.sync_dataset("ds", [QUESTION])
    return tracer.client, delete


def _item(expected_output):
    return {
        "id": "q1",
        "input": {"query": "How many?"},
        "expected_output": None,
        "expectedOutput": expected_output,
        "metadata": {"question_id": "q1"},
    }


def test_new_items_store_only_sql():
    client, _ = _sync([])
    assert client.create_dataset_item.call_args.kwargs["expected_output"] == {"sql": "SELECT 1"}


def test_items_with_response_are_rewritten_in_place():
    client, delete = _sync([_item({"sql": "SELECT 1", "response": "SELECT 1"})])
    delete.assert_not_called()
    kwargs = client.create_dataset_item.call_args.kwargs
    assert kwargs["id"] == "q1"
    assert kwargs["expected_output"] == {"sql": "SELECT 1"}


def test_up_to_date_items_are_left_alone():
    client, delete = _sync([_item({"sql": "SELECT 1"})])
    client.create_dataset_item.assert_not_called()
    delete.assert_not_called()


def test_empty_question_list_never_deletes_items():
    tracer = MagicMock(public_key="pk", private_key="sk", host="https://lf.test")
    with (
        patch("app.services.langfuse_client.requests.get") as get,
        patch("app.services.langfuse_client.requests.delete") as delete,
    ):
        assert LangfuseDatasetService(tracer).sync_dataset("ds", []) is None
    get.assert_not_called()
    delete.assert_not_called()

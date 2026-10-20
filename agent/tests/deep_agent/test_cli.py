import json

from langchain_core.messages import AIMessage, ToolMessage

from deep_agent.cli import RunRecord, append_run_record, print_formatted_message


def test_run_record_is_valid_jsonl(tmp_path):
    log = tmp_path / "runs" / "log.jsonl"
    for i in range(2):
        append_run_record(log, RunRecord("2026-10-20T10:00:00", 1.5, f"t{i}", "שאלה\nשורה", "SELECT 1", "תשובה",
                                         "matched", True))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert rows[0]["question"] == "שאלה\nשורה"
    assert set(rows[1]) == {"timestamp", "execution_time_s", "thread_id", "question", "final_sql", "final_answer",
                            "verification", "verified"}


def test_print_formatted_message_handles_list_content(capsys):
    print_formatted_message(AIMessage(content=[{"type": "text", "text": "hello"}], tool_calls=[
        {"name": "execute_trino_query", "args": {"sql": "SELECT 1"}, "id": "1"},
    ]))
    print_formatted_message(ToolMessage(content=[{"type": "text", "text": "Error: boom"}], tool_call_id="1", name="x"))
    out = capsys.readouterr().out
    assert "hello" in out and "execute_trino_query" in out and "Error: boom" in out

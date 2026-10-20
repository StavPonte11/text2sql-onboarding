import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deep_agent.cli import RunRecord, append_run_record, extract_final_sql_and_answer, print_formatted_message


def test_extracts_last_sql_block_and_strips_it_from_answer():
    answer = "הסבר קצר\n```sql\nSELECT 1\n```\nעוד טקסט\n```sql\nSELECT 2\n```"
    sql, text = extract_final_sql_and_answer([HumanMessage("q"), AIMessage(answer)])
    assert sql == "SELECT 2"
    assert "SELECT" not in text
    assert "הסבר קצר" in text


def test_handles_list_content_blocks_without_crashing():
    final = AIMessage(content=[
        {"type": "reasoning", "reasoning": "thinking..."},
        {"type": "text", "text": "תשובה\n```sql\nSELECT COUNT(*) FROM t\n```"},
    ])
    sql, text = extract_final_sql_and_answer([HumanMessage("q"), final])
    assert sql == "SELECT COUNT(*) FROM t"
    assert text == "תשובה"


def test_falls_back_to_history_when_final_answer_has_no_sql():
    history = [
        HumanMessage("q"),
        AIMessage("draft\n```sql\nSELECT 3\n```"),
        ToolMessage("ok", tool_call_id="1"),
        AIMessage("final answer, no sql"),
    ]
    sql, text = extract_final_sql_and_answer(history)
    assert sql == "SELECT 3"
    assert text == "final answer, no sql"


def test_strips_thought_blocks():
    sql, text = extract_final_sql_and_answer([AIMessage("<thought>hidden</thought>visible")])
    assert sql is None
    assert text == "visible"


def test_empty_history():
    assert extract_final_sql_and_answer([]) == (None, "")


def test_run_record_is_valid_jsonl(tmp_path):
    log = tmp_path / "runs" / "log.jsonl"
    for i in range(2):
        append_run_record(log, RunRecord("2026-10-20T10:00:00", 1.5, f"t{i}", "שאלה\nשורה", "SELECT 1", "תשובה"))
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert rows[0]["question"] == "שאלה\nשורה"
    assert set(rows[1]) == {"timestamp", "execution_time_s", "thread_id", "question", "final_sql", "final_answer"}


def test_print_formatted_message_handles_list_content(capsys):
    print_formatted_message(AIMessage(content=[{"type": "text", "text": "hello"}], tool_calls=[
        {"name": "execute_trino_query", "args": {"sql": "SELECT 1"}, "id": "1"},
    ]))
    print_formatted_message(ToolMessage(content=[{"type": "text", "text": "Error: boom"}], tool_call_id="1", name="x"))
    out = capsys.readouterr().out
    assert "hello" in out and "execute_trino_query" in out and "Error: boom" in out

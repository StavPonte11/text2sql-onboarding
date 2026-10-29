from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from core.trino import TrinoExecutionResult
from deep_agent import answer as answer_module
from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.answer import SubmitAnswer, answer_schema, normalize_sql, render_markdown, verify_answer
from tests.deep_agent.conftest import system_text

EXECUTED = "SELECT COUNT(DISTINCT flight_id) AS flights FROM \"postgres\".\"public\".\"flights_table\" WHERE launch_country = 'France'"
# Same query as the model might restate it: different whitespace/case and a trailing semicolon.
RESTATED = "select count(distinct flight_id) as flights\nfrom \"postgres\".\"public\".\"flights_table\"\nwhere launch_country = 'France';"


def _trino(rows):
    return TrinoExecutionResult(success=True, rows=rows, columns=["flights"], row_count=len(rows), query_id="q-1")


def _policy(final_args, content=""):
    def policy(messages):
        tool_msgs = [m for m in messages if m.type == "tool"]
        if not tool_msgs:
            return AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": EXECUTED}, "id": "e1"}])
        return AIMessage(content, tool_calls=[{"name": "submit_answer", "args": final_args, "id": "final"}])
    return policy


ANSWER = {"sql": RESTATED, "explanation_he": "סופרת טיסות ייחודיות שהמריאו מצרפת", "assumptions": ["טיסה = flight_id"],
          "confidence": "high"}


async def _run(policy_model, policy, tool_results, reexecution_results=(), clarifications=False):
    agent = create_deep_agent(model=policy_model(policy), clarifications_enabled=clarifications)
    with patch.object(tools, "execute_query_sync", side_effect=list(tool_results)) as t1, \
         patch.object(answer_module, "execute_query_sync", side_effect=list(reexecution_results)) as t2:
        result = await agent.ainvoke({"messages": [("user", "כמה טיסות המריאו מצרפת")]},
                                     {"configurable": {"thread_id": "a"}})
    return result, t1, t2


@pytest.mark.asyncio
async def test_answer_is_bound_to_the_executed_query(policy_model):
    result, _, reexec = await _run(policy_model, _policy(ANSWER), [_trino([[466]])])

    final = result["final_result"]
    assert final["status"] == "matched" and final["executed"] is True
    assert final["sql"] == EXECUTED                     # the executed text, no trailing ';'
    assert final["result"]["rows"] == [[466]]           # rows from Trino, not model text
    assert final["answer"]["confidence"] == "high"
    reexec.assert_not_called()
    assert len(result["executed_queries"]) == 1


@pytest.mark.asyncio
async def test_unexecuted_answer_sql_is_re_executed(policy_model):
    other = {**ANSWER, "sql": "SELECT COUNT(*) AS flights FROM \"postgres\".\"public\".\"flights_table\""}
    result, _, reexec = await _run(policy_model, _policy(other), [_trino([[466]])], [_trino([[1000]])])

    final = result["final_result"]
    assert final["status"] == "re_executed" and final["executed"] is True
    assert final["result"]["rows"] == [[1000]]
    reexec.assert_called_once()
    assert reexec.call_args.args[0].endswith("LIMIT 101")  # re-execution goes through the guard


@pytest.mark.asyncio
async def test_forbidden_answer_sql_is_rejected(policy_model):
    bad = {**ANSWER, "sql": "DELETE FROM \"postgres\".\"public\".\"flights_table\""}
    result, _, reexec = await _run(policy_model, _policy(bad), [_trino([[466]])])

    final = result["final_result"]
    assert final["status"] == "rejected" and final["executed"] is False
    assert final["result"] is None
    reexec.assert_not_called()


@pytest.mark.asyncio
async def test_clarification_path_when_enabled(policy_model):
    ask = {"sql": None, "explanation_he": "", "confidence": "low", "needs_clarification": "האם הכוונה לנחיתות או להמראות?"}
    result, _, _ = await _run(policy_model, _policy(ask), [_trino([[466]])], clarifications=True)

    final = result["final_result"]
    assert final["status"] == "clarification"
    assert render_markdown(final) == "האם הכוונה לנחיתות או להמראות?"


@pytest.mark.asyncio
async def test_list_content_from_reasoning_models_is_fine(policy_model):
    reasoning = [{"type": "reasoning", "reasoning": "thinking..."}, {"type": "text", "text": "submitting"}]
    result, _, _ = await _run(policy_model, _policy(ANSWER, content=reasoning), [_trino([[466]])])
    assert result["final_result"]["status"] == "matched"


def test_render_markdown_builds_table_from_rows():
    final = {
        "status": "matched", "executed": True, "sql": "SELECT a, b FROM t",
        "answer": {"explanation_he": "הסבר", "assumptions": ["הנחה"], "confidence": "high"},
        "result": {"columns": ["a", "b"], "rows": [["x|y", None], [1, "line\nbreak"]], "row_count": 2,
                   "truncated": True, "query_id": "q"},
    }
    md = render_markdown(final)
    assert "| a | b |" in md
    assert "| x\\|y |  |" in md and "| 1 | line break |" in md
    assert "**הנחות:**\n- הנחה" in md
    assert "השורות הראשונות" in md
    assert md.endswith("```sql\nSELECT a, b FROM t\n```")


def test_render_reports_failed_final_sql():
    final = {"status": "re_execution_failed", "executed": False, "sql": "SELECT x FROM t", "result": None,
             "error": "Column 'x' cannot be resolved", "answer": {"explanation_he": "הסבר"}}
    assert "לא ניתן היה להריץ" in render_markdown(final)


def test_normalize_sql_equivalence():
    assert normalize_sql(EXECUTED) == normalize_sql(RESTATED)
    assert normalize_sql("```sql\nSELECT 1;\n```") == normalize_sql("select 1")
    assert normalize_sql("SELECT 1") != normalize_sql("SELECT 2")


def test_verify_without_structured_answer():
    assert verify_answer(None, [])["status"] == "no_answer"


def test_answer_schema_requires_explanation_and_confidence():
    with pytest.raises(ValueError):
        SubmitAnswer(sql="SELECT 1")


def test_clarifications_are_off_by_default():
    schema = answer_schema()
    assert schema.__name__ == "submit_answer"
    assert "needs_clarification" not in schema.model_fields
    assert schema.model_fields["sql"].is_required()          # the model must always commit to a SQL answer
    enabled = answer_schema(True)
    assert enabled.__name__ == "submit_answer"             # same tool name either way
    assert "needs_clarification" in enabled.model_fields and not enabled.model_fields["sql"].is_required()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_clarification_only_mentioned_in_prompt_when_enabled(scripted_model, enabled):
    model = scripted_model(AIMessage("", tool_calls=[{"name": "submit_answer", "args": {**ANSWER}, "id": "f"}]))
    agent = create_deep_agent(model=model, clarifications_enabled=enabled)
    with patch.object(answer_module, "execute_query_sync", return_value=_trino([[1]])):
        await agent.ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": f"c{enabled}"}})
    assert ("needs_clarification" in system_text(model.seen[0])) is enabled


@pytest.mark.asyncio
async def test_matched_exploration_sample_is_re_executed_for_the_full_answer(policy_model):
    """The model explored with limit=5 and submitted that same SQL: the user must get the full result, not the sample."""
    def policy(messages):
        if not [m for m in messages if m.type == "tool"]:
            return AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": EXECUTED, "limit": 5}, "id": "e1"}])
        return AIMessage("", tool_calls=[{"name": "submit_answer", "args": ANSWER, "id": "final"}])

    sample = _trino([[i] for i in range(6)])                 # 5 + 1 rows -> truncated sample
    full = _trino([[i] for i in range(40)])
    result, _, reexec = await _run(policy_model, policy, [sample], [full])

    final = result["final_result"]
    assert final["status"] == "re_executed" and final["executed"] is True
    assert final["result"]["row_count"] == 40 and final["result"]["truncated"] is False
    assert reexec.call_args.args[0].endswith("LIMIT 101")


def test_re_execution_without_a_row_ceiling(monkeypatch):
    monkeypatch.setattr(answer_module.settings, "DEEP_AGENT_MAX_ROWS", None)
    answer = SubmitAnswer(sql="SELECT a FROM t", explanation_he="הסבר", confidence="high")
    with patch.object(answer_module, "execute_query_sync", return_value=_trino([[i] for i in range(500)])) as run:
        final = verify_answer(answer, [])
    assert run.call_args.args[0] == "SELECT a FROM t"       # no LIMIT added
    assert final["executed"] is True and final["result"]["row_count"] == 500 and final["result"]["truncated"] is False


# ── recovery of answers written as text (gpt-oss behind the configured server) ──

def _text_ending_policy(final_text):
    def policy(messages):
        if not [m for m in messages if m.type == "tool"]:
            return AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": EXECUTED}, "id": "e1"}])
        return AIMessage(final_text, response_metadata={"finish_reason": "tool_calls"})
    return policy


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [lambda j: j, lambda j: f"```json\n{j}\n```"], ids=["bare", "fenced"])
async def test_answer_written_as_json_text_is_recovered(policy_model, wrap):
    import json as _json

    result, _, reexec = await _run(policy_model, _text_ending_policy(wrap(_json.dumps(ANSWER, ensure_ascii=False))),
                                   [_trino([[466]])])
    final = result["final_result"]
    assert final["status"] == "matched" and final["executed"] is True
    assert final["result"]["rows"] == [[466]]
    assert final["recovered_from_text"] is True
    reexec.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "סיימתי, התשובה היא 466",                                  # prose, not an answer object
    '{"sql": "SELECT 1"}',                                    # JSON, but missing required fields
    '{"explanation_he": "x", "confidence": "high"',           # broken JSON
], ids=["prose", "invalid-schema", "broken-json"])
async def test_text_that_is_not_a_valid_answer_is_not_recovered(policy_model, text):
    result, _, _ = await _run(policy_model, _text_ending_policy(text), [_trino([[466]])])
    final = result["final_result"]
    assert final["status"] == "no_answer" and final["executed"] is False
    assert final["recovered_from_text"] is False


@pytest.mark.asyncio
async def test_a_real_tool_call_is_not_marked_recovered(policy_model):
    result, _, _ = await _run(policy_model, _policy(ANSWER), [_trino([[466]])])
    assert result["final_result"]["recovered_from_text"] is False

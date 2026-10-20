from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from core.trino import TrinoExecutionResult
from deep_agent import answer as answer_module
from deep_agent import tools
from deep_agent.agent import create_deep_agent
from deep_agent.answer import DeepAgentAnswer, normalize_sql, render_markdown, verify_answer

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
        return AIMessage(content, tool_calls=[{"name": "DeepAgentAnswer", "args": final_args, "id": "final"}])
    return policy


ANSWER = {"sql": RESTATED, "explanation_he": "סופרת טיסות ייחודיות שהמריאו מצרפת", "assumptions": ["טיסה = flight_id"],
          "confidence": "high"}


async def _run(policy_model, policy, tool_results, reexecution_results=()):
    agent = create_deep_agent(model=policy_model(policy))
    with patch.object(tools, "execute_query_sync", side_effect=list(tool_results)) as t1, \
         patch.object(answer_module, "execute_query_sync", side_effect=list(reexecution_results)) as t2:
        result = await agent.ainvoke({"messages": [("user", "כמה טיסות המריאו מצרפת")]},
                                     {"configurable": {"thread_id": "a"}})
    return result, t1, t2


@pytest.mark.asyncio
async def test_answer_is_bound_to_the_executed_query(policy_model):
    result, _, reexec = await _run(policy_model, _policy(ANSWER), [_trino([[466]])])

    final = result["final_result"]
    assert final["status"] == "matched" and final["verified"] is True
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
    assert final["status"] == "re_executed" and final["verified"] is True
    assert final["result"]["rows"] == [[1000]]
    reexec.assert_called_once()
    assert reexec.call_args.args[0].endswith("LIMIT 101")  # re-execution goes through the guard


@pytest.mark.asyncio
async def test_forbidden_answer_sql_is_rejected(policy_model):
    bad = {**ANSWER, "sql": "DELETE FROM \"postgres\".\"public\".\"flights_table\""}
    result, _, reexec = await _run(policy_model, _policy(bad), [_trino([[466]])])

    final = result["final_result"]
    assert final["status"] == "rejected" and final["verified"] is False
    assert final["result"] is None
    reexec.assert_not_called()


@pytest.mark.asyncio
async def test_clarification_path(policy_model):
    ask = {"sql": None, "explanation_he": "", "confidence": "low", "needs_clarification": "האם הכוונה לנחיתות או להמראות?"}
    result, _, _ = await _run(policy_model, _policy(ask), [_trino([[466]])])

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
        "status": "matched", "verified": True, "sql": "SELECT a, b FROM t",
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
    final = {"status": "re_execution_failed", "verified": False, "sql": "SELECT x FROM t", "result": None,
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
        DeepAgentAnswer(sql="SELECT 1")

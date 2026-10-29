import json
import os
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from core.trino import TrinoExecutionResult
from deep_agent import answer, service, tools
from deep_agent.agent import create_deep_agent
from deep_agent.service import run_deep_chat, to_response

SQL = "SELECT COUNT(*) AS orders FROM orders WHERE status = 'shipped'"
OK = TrinoExecutionResult(success=True, rows=[[466]], columns=["orders"], row_count=1, query_id="q-1")


def _answer(sql=SQL, **extra):
    return AIMessage("", tool_calls=[{"name": "submit_answer", "id": f"f{uuid.uuid4().hex[:4]}", "args": {
        "sql": sql, "explanation_he": "סופר הזמנות שנשלחו", "confidence": "high", **extra}}])


def _policy(messages):
    """Turn 1 asks for clarification; after the user's reply, executes and answers."""
    humans = [m for m in messages if m.type == "human"]
    if len(humans) == 1:
        return _answer(sql=None, needs_clarification="האם הכוונה להזמנות שנשלחו או שנמסרו?")
    last_tool = next((m for m in reversed(messages) if m.type == "tool"), None)
    if last_tool is None or last_tool.name == "submit_answer":
        return AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": SQL}, "id": f"e{len(messages)}"}])
    return _answer()


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    """run_deep_chat builds a real Langfuse handler when tracing is on; never send test runs upstream."""
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_TRACING_ENABLED", False)


@pytest.fixture
def published():
    with patch.object(service, "publish_node_event", AsyncMock()) as pub:
        yield pub


# ── response mapping ─────────────────────────────────────────────────────────

def test_completed_response_matches_legacy_contract():
    final = {"status": "matched", "verified": True, "sql": SQL, "error": None,
             "answer": {"explanation_he": "הסבר", "confidence": "high", "assumptions": []},
             "result": {"columns": ["orders"], "rows": [[466]], "row_count": 1, "truncated": False, "query_id": "q"}}
    r = to_response("deep-1", final, "trace-1", ["deep:execute_trino_query"])
    assert r["status"] == "completed" and r["sql_query"] == SQL and r["sql_explanation"] == "הסבר"
    assert "| 466 |" in r["summary"] and "```sql" not in r["summary"]  # SQL is returned separately
    assert r["is_unanswerable"] is False and r["engine"] == "deep" and r["verification"] == "matched"
    assert r["executed"] is False and r["recovered_from_text"] is False  # absent in final -> False
    assert to_response("t", {**final, "executed": True, "recovered_from_text": True}, None, [])["executed"] is True
    assert {"thread_id", "status", "summary", "raw_data_ref", "sql_query", "sql_explanation", "trace_id",
            "execution_path", "is_unanswerable"} <= set(r)


@pytest.mark.parametrize("status", ["no_answer", "rejected", "re_execution_failed"])
def test_unanswered_statuses(status):
    assert to_response("t", {"status": status, "answer": None}, None, [])["is_unanswerable"] is True


def test_clarification_maps_to_free_text_hitl():
    r = to_response("t", {"status": "clarification", "answer": {"needs_clarification": "איזו שנה?"}}, None, [])
    assert r["status"] == "interrupted"
    assert r["interrupt_details"] == {"type": "hitl_escalation", "subtype": "clarification", "reason": "איזו שנה?"}


# ── full turns ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clarify_then_resume_on_the_same_thread(policy_model, published):
    agent = create_deep_agent(model=policy_model(_policy), clarifications_enabled=True)
    with patch.object(tools, "execute_query_sync", return_value=OK):
        first = await run_deep_chat(connection_id=89, query="כמה הזמנות נשלחו", thread_id="deep-t1", agent=agent)
        second = await run_deep_chat(connection_id=89, thread_id="deep-t1", agent=agent,
                                     resume_value={"approved": False, "feedback": "שנשלחו"})

    assert first["status"] == "interrupted" and "שנשלחו" in first["interrupt_details"]["reason"]
    assert second["status"] == "completed" and second["sql_query"] == SQL
    assert second["verification"] == "matched" and second["executed"] is True and "466" in second["summary"]
    assert second["execution_path"] == ["deep:execute_trino_query"]

    events = [(c.args[1], c.args[2]) for c in published.await_args_list]
    assert ("deep:execute_trino_query", "active") in events and ("deep:execute_trino_query", "completed") in events
    assert events[-1] == ("deep:answer", "completed")
    assert all(c.args[0] == "deep-t1" for c in published.await_args_list)


@pytest.mark.asyncio
async def test_turn_without_new_answer_does_not_repeat_the_previous_one(scripted_model, published):
    model = scripted_model(
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": SQL}, "id": "e1"}]),
        _answer(),
        AIMessage("small talk, no answer"),
        AIMessage("still no answer"),  # reply to the submit reminder
    )
    agent = create_deep_agent(model=model)
    with patch.object(tools, "execute_query_sync", return_value=OK):
        first = await run_deep_chat(connection_id=89, query="q1", thread_id="deep-t2", agent=agent)
        second = await run_deep_chat(connection_id=89, query="ותודה", thread_id="deep-t2", agent=agent)
    assert first["verification"] == "matched"
    assert second["verification"] == "no_answer" and second["sql_query"] is None


@pytest.mark.asyncio
async def test_answer_only_matches_sql_executed_in_its_own_turn(scripted_model, published):
    """Turn 2 submits turn 1's SQL without running it: not 'matched' — it is re-executed to get real rows."""
    model = scripted_model(
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": SQL}, "id": "e1"}]),
        _answer(),
        _answer(),
    )
    agent = create_deep_agent(model=model)
    with patch.object(tools, "execute_query_sync", return_value=OK), \
         patch.object(answer, "execute_query_sync", return_value=OK) as rerun:
        first = await run_deep_chat(connection_id=89, query="q1", thread_id="deep-t3", agent=agent)
        second = await run_deep_chat(connection_id=89, query="ושוב", thread_id="deep-t3", agent=agent)
    assert first["verification"] == "matched"
    assert second["verification"] == "re_executed" and second["executed"] is True
    rerun.assert_called_once()


@pytest.mark.asyncio
async def test_checkpoints_hold_only_plain_data(scripted_model, published, caplog):
    """The answer object isn't kept in thread state (checkpoints can't restore its class); final_result is."""
    agent = create_deep_agent(model=scripted_model(
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": SQL}, "id": "e1"}]), _answer()))
    with patch.object(tools, "execute_query_sync", return_value=OK):
        await run_deep_chat(connection_id=89, query="q", thread_id="deep-t5", agent=agent)
    state = (await agent.aget_state({"configurable": {"thread_id": "deep-t5"}})).values
    assert state.get("structured_response") is None
    assert state["final_result"]["answer"]["sql"] == SQL
    assert "unregistered type" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_s_cancels_the_run(scripted_model, published):
    model = scripted_model(
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": SQL}, "id": "e1"}]),
        _answer(),
    )
    agent = create_deep_agent(model=model)

    def slow_query(*args, **kwargs):
        time.sleep(1.0)
        return OK

    # A query already running in a worker thread can't be interrupted; the run stops there.
    with patch.object(tools, "execute_query_sync", side_effect=slow_query):
        r = await run_deep_chat(connection_id=89, query="q", thread_id="deep-t4", agent=agent, timeout_s=0.3)
    assert r["verification"] == "no_answer" and r["executed"] is False and r["is_unanswerable"] is True
    assert "stopped after 0.3s" in r["summary"]
    assert model.seen and len(model.seen) == 1  # the model was never called again after the cancel


@pytest.mark.asyncio
async def test_missing_query_is_an_error():
    assert "error" in await run_deep_chat(connection_id=1, query=None, thread_id="deep-x", agent=object())
    assert "error" in await run_deep_chat(connection_id=1, thread_id="deep-x", resume_value={"approved": True},
                                          agent=object())


# ── MCP tool routing ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine, thread_id, default, deep", [
    ("deep", None, "legacy", True),
    (None, None, "deep", True),
    (None, None, "legacy", False),
    ("legacy", "deep-abc", "legacy", True),     # a resumed deep thread stays on the deep agent
    ("deep", "legacy-thread", "deep", False),   # ...and a legacy thread stays legacy
])
def test_engine_routing(engine, thread_id, default, deep, monkeypatch):
    from agent import mcp_server
    from agent.config import settings

    monkeypatch.setattr(settings, "AGENT_ENGINE", default)
    assert mcp_server._use_deep_agent(engine, thread_id) is deep


@pytest.mark.asyncio
@pytest.mark.parametrize("hitl, expected_status", [(True, "interrupted"), (False, "completed")])
async def test_chat_with_agent_deep_path(hitl, expected_status):
    from agent import mcp_server

    clarification = to_response("deep-z", {"status": "clarification", "answer": {"needs_clarification": "?"}}, None, [])
    with patch("deep_agent.service.run_deep_chat", AsyncMock(return_value=clarification)) as run, \
         patch.object(mcp_server.agent_graph, "ainvoke") as legacy:
        raw = await mcp_server.chat_with_agent(connection_id=89, query="q", engine="deep", hitl_enabled=hitl)
    out = json.loads(raw)
    legacy.assert_not_called()
    assert run.await_args.kwargs["thread_id"].startswith("deep-")
    assert run.await_args.kwargs["timeout_s"] is None  # no limit unless the caller passes one
    assert out["status"] == expected_status


# ── durable threads (real Postgres) ──────────────────────────────────────────

PG_URL = os.environ.get("DEEP_AGENT_TEST_PG_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not PG_URL, reason="set DEEP_AGENT_TEST_PG_URL to a throwaway Postgres database")
async def test_threads_survive_a_restart_with_postgres(scripted_model, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_CHECKPOINT_DB_URL", PG_URL)
    thread = f"deep-durable-{uuid.uuid4().hex[:8]}"

    first_process = create_deep_agent(model=scripted_model(AIMessage("first")), checkpointer=await service.build_checkpointer())
    await first_process.ainvoke({"messages": [("user", "שאלה ראשונה")]}, {"configurable": {"thread_id": thread}})
    await service._pg_pool.close()

    # "Restart": a new agent with a new pool sees the same thread.
    second_process = create_deep_agent(model=scripted_model(AIMessage("second")), checkpointer=await service.build_checkpointer())
    state = await second_process.aget_state({"configurable": {"thread_id": thread}})
    assert [m.text for m in state.values["messages"] if m.type == "human"] == ["שאלה ראשונה"]
    await service._pg_pool.close()


@pytest.mark.asyncio
async def test_reviewer_tools_are_labelled_in_the_stream(scripted_model, published):
    main = scripted_model(
        AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": SQL}, "id": "e1"}]), _answer())
    reviewer = scripted_model(
        AIMessage("", tool_calls=[{"name": "get_sample_rows", "args": {"table_name": "orders"}, "id": "r1"}]),
        AIMessage("", tool_calls=[{"name": "submit_verdict", "args": {"verdict": "pass"}, "id": "v1"}]),
    )
    agent = create_deep_agent(model=main, semantic_validation="always", validator_model=reviewer)
    with patch.object(tools, "execute_query_sync", return_value=OK):
        r = await run_deep_chat(connection_id=89, query="q", thread_id="deep-t6", agent=agent)
    assert r["execution_path"] == ["deep:execute_trino_query", "deep:review:get_sample_rows"]
    assert r["semantic_validation"]["verdict"] == "passed"

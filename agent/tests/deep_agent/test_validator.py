"""Independent semantic review of answers (deep_agent.validator): "always" gate and "on_demand" subagent."""

import itertools
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from core.trino import TrinoExecutionResult
from langchain_core.messages import AIMessage

from deep_agent.agent import create_deep_agent
from deep_agent.answer import verification
from deep_agent.answer.render import render_markdown
from deep_agent.answer.schema import ANSWER_RECEIVED, answer_schema
from deep_agent.answer.verification import validation_summary
from deep_agent.observability import run_metrics
from deep_agent.tools import sql as sql_tools
from deep_agent.validator.modes import feedback_text
from deep_agent.validator.reviewer import VALIDATOR_TOOLS, SubmitVerdict

SQL = "SELECT COUNT(*) AS n FROM orders WHERE status = 'shipped'"
FIXED = "SELECT COUNT(DISTINCT order_id) AS n FROM orders WHERE status = 'shipped'"
_ids = itertools.count()


def _trino(*args, **kwargs):
    """A successful execution with a fresh query id per run, like real Trino."""
    return TrinoExecutionResult(success=True, rows=[[42]], columns=["n"], row_count=1, query_id=f"q{next(_ids)}")


@contextmanager
def _fake_trino():
    """Every execution in the run (agent, reviewer, re-execution) — never the real Trino."""
    with patch.object(sql_tools, "execute_query_sync", side_effect=_trino), \
            patch.object(verification, "execute_query_sync", side_effect=_trino):
        yield
ISSUE = {"problem": "סופרים שורות ולא הזמנות", "evidence": "COUNT(*)=42 אבל COUNT(DISTINCT order_id)=40"}


def _execute(sql, call_id="e1"):
    return AIMessage("", tool_calls=[{"name": "execute_trino_query", "args": {"sql": sql}, "id": call_id}])


def _submit(sql=SQL, call_id="s1", **extra):
    return AIMessage("", tool_calls=[{"name": "submit_answer", "id": call_id, "args": {
        "sql": sql, "explanation_he": "מספר ההזמנות שנשלחו", "confidence": "high", **extra}}])


def _verdict(verdict, issues=(), call_id="v1"):
    return AIMessage("", tool_calls=[{"name": "submit_verdict", "id": call_id,
                                      "args": {"verdict": verdict, "issues": list(issues)}}])


async def _run(main, reviewer, question="כמה הזמנות נשלחו?", thread="t"):
    agent = create_deep_agent(model=main, semantic_validation="always", validator_model=reviewer)
    with _fake_trino():
        return await agent.ainvoke({"messages": [("user", question)]}, {"configurable": {"thread_id": thread}})


def _texts(messages):
    return "\n".join(m.text for m in messages)


# ── always: every submitted answer is reviewed ───────────────────────────────

@pytest.mark.asyncio
async def test_pass_ends_the_run(scripted_model):
    main = scripted_model(AIMessage("my private reasoning", tool_calls=_execute(SQL).tool_calls), _submit())
    reviewer = scripted_model(_verdict("pass"))
    state = await _run(main, reviewer)

    final = state["final_result"]
    assert final["status"] == "matched"
    assert final["semantic_validation"] == {"verdict": "passed", "rounds": 1, "issues": [], "response": None,
                                            "reason": None, "reviewed_final_sql": True}
    # Fresh context: the reviewer sees the request, the SQL, the explanation and the rows — not the reasoning.
    review = _texts(reviewer.seen[0])
    assert "כמה הזמנות נשלחו?" in review and SQL in review and "מספר ההזמנות שנשלחו" in review and "[42]" in review
    assert "my private reasoning" not in review
    assert '"catalog"."schema"."orders"' in review  # the catalog is in the reviewer's system prompt
    assert state["messages"][-1].text == ANSWER_RECEIVED


@pytest.mark.asyncio
async def test_fail_sends_feedback_and_the_revision_is_reviewed_again(scripted_model):
    main = scripted_model(_execute(SQL), _submit(), _execute(FIXED, "e2"), _submit(FIXED, "s2"))
    reviewer = scripted_model(_verdict("fail", [ISSUE]), _verdict("pass", call_id="v2"))
    state = await _run(main, reviewer)

    # The model saw the review instead of "Answer received." for its first submission.
    after_fail = _texts(main.seen[2])
    assert ISSUE["problem"] in after_fail and ISSUE["evidence"] in after_fail
    first_submission = next(m for m in state["messages"] if m.type == "tool" and m.tool_call_id == "s1")
    assert first_submission.text == feedback_text([ISSUE])

    final = state["final_result"]
    assert final["sql"] == FIXED and final["status"] == "matched"
    assert final["semantic_validation"]["verdict"] == "passed" and final["semantic_validation"]["rounds"] == 2
    assert FIXED in _texts(reviewer.seen[1])


@pytest.mark.asyncio
async def test_resubmitting_the_same_sql_with_a_justification_is_a_dispute(scripted_model):
    main = scripted_model(_execute(SQL), _submit(),
                          _submit(SQL, "s2", validation_response="כל שורה היא הזמנה אחת, אין כפילויות"))
    reviewer = scripted_model(_verdict("fail", [ISSUE]))
    state = await _run(main, reviewer)

    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "disputed" and review["rounds"] == 1
    assert review["issues"] == [ISSUE] and review["response"] == "כל שורה היא הזמנה אחת, אין כפילויות"
    assert len(reviewer.seen) == 1  # the dispute ends the loop without another review
    shown = render_markdown(state["final_result"])
    assert "בדיקה עצמאית העלתה הסתייגות" in shown and ISSUE["problem"] in shown and "תגובת הסוכן" in shown


@pytest.mark.asyncio
async def test_stopping_after_a_fail_keeps_the_answer_marked_failed(scripted_model):
    main = scripted_model(_execute(SQL), _submit(), AIMessage("I think it is fine."))
    reviewer = scripted_model(_verdict("fail", [ISSUE]))
    state = await _run(main, reviewer)

    final = state["final_result"]
    assert final["sql"] == SQL and final["executed"] is True
    assert final["semantic_validation"]["verdict"] == "failed"
    assert "בדיקה עצמאית מצאה בעיות בתשובה" in render_markdown(final)


@pytest.mark.asyncio
async def test_review_limit_is_optional(scripted_model, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_VALIDATOR_MAX_ROUNDS", 1)
    main = scripted_model(_execute(SQL), _submit(), _execute(FIXED, "e2"), _submit(FIXED, "s2"))
    reviewer = scripted_model(_verdict("fail", [ISSUE]))
    state = await _run(main, reviewer)

    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "not_validated" and review["reason"] == "review limit reached (1)"
    assert len(reviewer.seen) == 1


@pytest.mark.asyncio
async def test_a_broken_review_does_not_lose_the_answer(scripted_model):
    main = scripted_model(_execute(SQL), _submit())
    reviewer = scripted_model()  # no scripted reply: the reviewer's model call raises
    state = await _run(main, reviewer)

    final = state["final_result"]
    assert final["status"] == "matched"
    assert final["semantic_validation"]["verdict"] == "not_validated"
    assert final["semantic_validation"]["reason"]


@pytest.mark.asyncio
async def test_follow_up_turn_is_reviewed_with_its_context(scripted_model):
    main = scripted_model(_execute(SQL), _submit(), _execute(FIXED, "e2"), _submit(FIXED, "s2"))
    reviewer = scripted_model(_verdict("pass"), _verdict("pass", call_id="v2"))
    agent = create_deep_agent(model=main, semantic_validation="always", validator_model=reviewer)
    config = {"configurable": {"thread_id": "follow-up"}}
    with _fake_trino():
        await agent.ainvoke({"messages": [("user", "כמה הזמנות נשלחו?")]}, config)
        state = await agent.ainvoke({"messages": [("user", "ובלי כפילויות?")]}, config)

    second = _texts(reviewer.seen[1])
    assert "Earlier user messages" in second and "כמה הזמנות נשלחו?" in second
    assert "Current user request\nובלי כפילויות?" in second
    assert state["final_result"]["semantic_validation"]["rounds"] == 1  # counted per turn


# ── on_demand: the agent calls the reviewer through `task` ───────────────────

def _review(sql=SQL, call_id="r1", extra=""):
    brief = f"User request: כמה הזמנות נשלחו?\n```sql\n{sql}\n```\nResult: [[42]]{extra}"
    return {"name": "task", "id": call_id, "args": {"description": brief, "subagent_type": "semantic-validator"}}


async def _run_on_demand(main, reviewer, thread="od"):
    agent = create_deep_agent(model=main, semantic_validation="on_demand", validator_model=reviewer)
    with _fake_trino():
        return await agent.ainvoke({"messages": [("user", "כמה הזמנות נשלחו?")]}, {"configurable": {"thread_id": thread}})


@pytest.mark.asyncio
async def test_on_demand_review_returns_the_verdict_to_the_agent(scripted_model):
    main = scripted_model(_execute(SQL), AIMessage("my private reasoning", tool_calls=[_review()]), _submit())
    reviewer = scripted_model(_verdict("pass"))
    state = await _run_on_demand(main, reviewer)

    assert '"verdict":"pass"' in _texts(main.seen[2])  # the verdict came back as the task result (JSON)
    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "passed" and review["rounds"] == 1 and review["reviewed_final_sql"] is True
    seen = _texts(reviewer.seen[0])
    assert SQL in seen and "my private reasoning" not in seen  # fresh context: only the brief
    assert '"catalog"."schema"."orders"' in seen
    assert "`semantic-validator` subagent is available" in _texts(main.seen[0])  # the Phase 6 nudge


@pytest.mark.asyncio
async def test_on_demand_review_of_a_different_sql_does_not_count(scripted_model):
    main = scripted_model(_execute(SQL), AIMessage("", tool_calls=[_review()]),
                          _execute(FIXED, "e2"), _submit(FIXED))
    reviewer = scripted_model(_verdict("fail", [ISSUE]))
    state = await _run_on_demand(main, reviewer)

    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "not_validated" and review["reviewed_final_sql"] is False
    assert review["reason"] == "the final SQL changed after the last review" and review["rounds"] == 1
    assert "בדיקה עצמאית" not in render_markdown(state["final_result"])  # no warning about issues that were fixed
    assert run_metrics([], 1.0, state["final_result"]).semantic_validation_passed is None


@pytest.mark.asyncio
async def test_on_demand_without_a_review(scripted_model):
    state = await _run_on_demand(scripted_model(_execute(SQL), _submit()), scripted_model())
    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "not_validated" and review["rounds"] == 0 and review["reason"] == "no review"


@pytest.mark.asyncio
async def test_on_demand_failed_review_of_the_final_sql_is_shown(scripted_model):
    main = scripted_model(_execute(SQL), AIMessage("", tool_calls=[_review()]), _submit())
    state = await _run_on_demand(main, scripted_model(_verdict("fail", [ISSUE])))
    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "failed" and review["issues"] == [ISSUE]
    assert "בדיקה עצמאית מצאה בעיות בתשובה" in render_markdown(state["final_result"])


@pytest.mark.asyncio
async def test_parallel_on_demand_reviews_are_both_recorded(policy_model):
    def main_policy(messages):
        tool_names = [m.name for m in messages if m.type == "tool"]
        if not tool_names:
            return _execute(SQL)
        if "task" not in tool_names:
            return AIMessage("", tool_calls=[_review(call_id="r1"), _review(FIXED, "r2")])
        return _submit()

    def reviewer_policy(messages):
        return _verdict("pass")

    state = await _run_on_demand(policy_model(main_policy), policy_model(reviewer_policy))
    records = state["semantic_validations"]
    assert len(records) == 2 and len({r["id"] for r in records}) == 2
    assert state["final_result"]["semantic_validation"]["rounds"] == 2


def test_modes_wire_the_reviewer(scripted_model):
    for mode, subagent in [("on_demand", True), ("always", False), ("off", False)]:
        agent = create_deep_agent(model=scripted_model(), semantic_validation=mode)
        tools_desc = str(agent.get_graph().nodes)  # built without errors in every mode
        assert tools_desc
        assert ("semantic-validator" in _task_description(agent)) is subagent, mode


def _task_description(agent) -> str:
    node = agent.nodes["tools"].bound
    task = node.tools_by_name.get("task")
    return task.description if task else ""


def test_reviewer_tools_are_read_only_diagnostics():
    assert {t.name for t in VALIDATOR_TOOLS} == {
        "execute_trino_query", "get_sample_rows", "get_column_distinct_values", "search_column_values",
        "get_column_profile"}


def test_fail_verdict_needs_evidence():
    with pytest.raises(ValueError):
        SubmitVerdict(verdict="fail", issues=[])
    assert SubmitVerdict(verdict="pass").issues == []


def test_validation_response_only_exists_with_validation():
    assert "validation_response" in answer_schema(False, True).model_fields   # "always" mode only
    assert "validation_response" not in answer_schema(False, False).model_fields


def test_summary_and_scores():
    assert validation_summary(None) is None  # reviewer off
    assert validation_summary([])["verdict"] == "not_validated"
    unreviewed = run_metrics([], 1.0, {"semantic_validation": validation_summary([])})
    assert unreviewed.semantic_validation_passed is None and unreviewed.validation_rounds == 0
    metrics = run_metrics([], 1.0, {"semantic_validation": {"verdict": "passed", "rounds": 2}})
    assert metrics.semantic_validation_passed is True and metrics.validation_rounds == 2
    off = run_metrics([], 1.0, {"semantic_validation": None})
    assert off.semantic_validation_passed is None and off.validation_rounds is None  # no score when off


# ── rejected submit_answer arguments are retried (every turn) ────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("validation", ["off", "always", "on_demand"])
async def test_invalid_submission_is_retried_on_every_turn(scripted_model, validation):
    bad = AIMessage("", tool_calls=[{"name": "submit_answer", "id": "bad", "args": {"sql": SQL}}])  # missing fields
    main = scripted_model(_execute(SQL), bad, _submit(), _execute(SQL, "e2"), bad, _submit(call_id="s2"))
    reviewer = scripted_model(_verdict("pass"), _verdict("pass", call_id="v2"))
    agent = create_deep_agent(model=main, semantic_validation=validation, validator_model=reviewer)
    config = {"configurable": {"thread_id": f"retry-{validation}"}}
    with _fake_trino():
        first = await agent.ainvoke({"messages": [("user", "q1")]}, config)
        second = await agent.ainvoke({"messages": [("user", "q2")]}, config)
    assert first["final_result"]["status"] == "matched"
    assert second["final_result"]["status"] == "matched"


@pytest.mark.asyncio
async def test_parallel_general_purpose_tasks_do_not_collide(policy_model):
    """Every subagent returns the parent's state keys; two in one step must not be rejected."""
    def main_policy(messages):
        if not [m for m in messages if m.type == "tool"]:
            return AIMessage("", tool_calls=[
                {"name": "task", "id": f"g{i}", "args": {"description": "look", "subagent_type": "general-purpose"}}
                for i in (1, 2)])
        return AIMessage("done")

    agent = create_deep_agent(model=policy_model(main_policy), semantic_validation="off")
    state = await agent.ainvoke({"messages": [("user", "q")]}, {"configurable": {"thread_id": "gp"}})
    assert [m.name for m in state["messages"] if m.type == "tool"] == ["task", "task"]


# ── submit reminder (model ends with text instead of the submit call) ────────

def test_default_mode_is_always():
    from agent.config import AgentSettings

    assert AgentSettings.model_fields["DEEP_AGENT_SEMANTIC_VALIDATION"].default == "always"


@pytest.mark.asyncio
async def test_reviewer_that_forgets_its_verdict_is_reminded_once(scripted_model):
    reviewer = scripted_model(AIMessage("I found the join is broken."), _verdict("fail", [ISSUE]))
    main = scripted_model(_execute(SQL), _submit(), _submit(SQL, "s2", validation_response="בטוח"))
    state = await _run(main, reviewer)

    assert "submit_verdict" in _texts(reviewer.seen[1])  # the reminder
    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "disputed" and review["issues"] == [ISSUE]  # the verdict was not lost


@pytest.mark.asyncio
async def test_main_agent_that_answers_in_text_is_reminded_once(scripted_model):
    main = scripted_model(_execute(SQL), AIMessage("There are 42 shipped orders."), _submit())
    agent = create_deep_agent(model=main, semantic_validation="off")
    with _fake_trino():
        state = await agent.ainvoke({"messages": [("user", "כמה הזמנות נשלחו?")]}, {"configurable": {"thread_id": "rm"}})
    assert state["final_result"]["status"] == "matched"
    reminder = main.seen[2][-1]
    assert reminder.type == "human" and reminder.name == "submit_reminder" and "submit_answer" in reminder.text
    assert not reminder.text.startswith("[Today")  # the date stays on the user's message
    assert main.seen[2][[m.type for m in main.seen[2]].index("human")].text.startswith("[Today")


@pytest.mark.asyncio
async def test_reminder_is_sent_only_once_per_turn(scripted_model):
    main = scripted_model(AIMessage("no"), AIMessage("still no"), AIMessage("next turn"), AIMessage("still not"))
    agent = create_deep_agent(model=main, semantic_validation="off")
    config = {"configurable": {"thread_id": "once"}}
    first = await agent.ainvoke({"messages": [("user", "q1")]}, config)
    second = await agent.ainvoke({"messages": [("user", "q2")]}, config)
    reminders = [m for m in second["messages"] if getattr(m, "name", None) == "submit_reminder"]
    assert len(reminders) == 2  # one per turn, not more
    assert first["final_result"]["status"] == second["final_result"]["status"] == "no_answer"
    assert len(main.seen) == 4


@pytest.mark.asyncio
async def test_no_reminder_after_a_failed_review(scripted_model):
    """The agent may stop after a fail: its (failed) answer stands — that is not a missing answer."""
    main = scripted_model(_execute(SQL), _submit(), AIMessage("I think it is fine."))
    state = await _run(main, scripted_model(_verdict("fail", [ISSUE])))
    assert not [m for m in state["messages"] if getattr(m, "name", None) == "submit_reminder"]
    assert state["final_result"]["semantic_validation"]["verdict"] == "failed"


def test_reminder_is_not_the_users_words():
    from langchain_core.messages import HumanMessage

    from deep_agent.context import REMINDER_NAME, current_turn, user_request

    messages = [HumanMessage("the question"), AIMessage("text"), HumanMessage("submit!", name=REMINDER_NAME)]
    assert current_turn(messages) == messages[1:]
    assert user_request(SimpleNamespace(state={"messages": messages})) == "the question"

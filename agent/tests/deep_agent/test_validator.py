"""Independent semantic review (deep_agent.validator) and the two approvals submit_answer needs.

The agent runs the SQL, has the `semantic-validator` subagent review it, then submits. submit_answer
only checks the records: the SQL ran this turn, and the reviewer passed it (or the agent disputes).
"""

import itertools
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from core.trino import TrinoExecutionResult
from langchain_core.messages import AIMessage

from deep_agent.agent import create_deep_agent
from deep_agent.answer import verification
from deep_agent.answer.middleware import NOT_REVIEWED, NOT_RUN
from deep_agent.answer.render import render_markdown
from deep_agent.answer.schema import ANSWER_RECEIVED, answer_schema
from deep_agent.answer.verification import normalize_sql, validation_summary
from deep_agent.observability import run_metrics
from deep_agent.tools import sql as sql_tools
from deep_agent.validator.reviewer import SubmitVerdict, parse_brief, review_request

SQL = "SELECT COUNT(*) AS n FROM orders WHERE status = 'shipped'"
FIXED = "SELECT COUNT(DISTINCT order_id) AS n FROM orders WHERE status = 'shipped'"
QUESTION = "כמה הזמנות נשלחו?"
ISSUE = {"problem": "סופרים שורות ולא הזמנות", "evidence": "COUNT(*)=42 אבל COUNT(DISTINCT order_id)=40"}
_ids = itertools.count()


def _trino(*args, **kwargs):
    """A successful execution with a fresh query id per run, like real Trino."""
    return TrinoExecutionResult(success=True, rows=[[42]], columns=["n"], row_count=1, query_id=f"q{next(_ids)}")


@contextmanager
def _fake_trino():
    """Every execution in the run (agent, reviewer, full-rows run) — never the real Trino."""
    with patch.object(sql_tools, "execute_query_sync", side_effect=_trino), \
            patch.object(verification, "execute_query_sync", side_effect=_trino):
        yield


def _call(name, args, call_id):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _execute(sql=SQL, call_id="e1", **extra):
    return _call("execute_trino_query", {"sql": sql, **extra}, call_id)


def _review(sql=SQL, call_id="r1", prose=""):
    brief = f"{prose}\n```sql\n{sql}\n```\nAssumptions:\n- הזמנה = שורה בטבלה"
    return _call("task", {"description": brief, "subagent_type": "semantic-validator"}, call_id)


def _submit(sql=SQL, call_id="s1", **extra):
    return _call("submit_answer", {"sql": sql, "explanation_he": "מספר ההזמנות שנשלחו", "confidence": "high", **extra},
                 call_id)


def _verdict(verdict, issues=(), call_id="v1"):
    return _call("submit_verdict", {"verdict": verdict, "issues": list(issues)}, call_id)


async def _run(main, reviewer=None, mode="always", question=QUESTION, thread="t"):
    agent = create_deep_agent(model=main, semantic_validation=mode, validator_model=reviewer)
    with _fake_trino():
        return await agent.ainvoke({"messages": [("user", question)]}, {"configurable": {"thread_id": thread}})


def _texts(messages):
    return "\n".join(m.text for m in messages)


def _submit_results(state):
    return [m.text for m in state["messages"] if m.type == "tool" and m.name == "submit_answer"]


# ── the happy path: run → review → submit ────────────────────────────────────

@pytest.mark.asyncio
async def test_run_review_submit_is_accepted(scripted_model):
    main = scripted_model(_execute(), _review(), _submit())
    state = await _run(main, scripted_model(_verdict("pass")))
    assert _submit_results(state) == [ANSWER_RECEIVED]
    final = state["final_result"]
    assert final["status"] == "matched" and final["executed"] is True
    assert final["semantic_validation"]["verdict"] == "passed" and final["semantic_validation"]["reviewed_final_sql"]


@pytest.mark.asyncio
async def test_reviewer_input_is_built_from_facts_not_the_brief(scripted_model):
    main = scripted_model(_execute(), _review(prose="my private reasoning: I already checked it, it is fine"), _submit())
    reviewer = scripted_model(_verdict("pass"))
    await _run(main, reviewer)

    seen = reviewer.seen[0]
    review = _texts(seen)
    assert QUESTION in review                                              # the user's own words, from the thread
    assert SQL in review and "[42]" in review                              # the SQL and its recorded rows
    assert "claims to check, not facts" in review and "הזמנה = שורה בטבלה" in review
    assert "my private reasoning" not in review and "already checked" not in review   # the brief's prose is dropped
    assert '"catalog"."schema"."orders"' in review                          # the catalog, as for every call
    user = [m for m in seen if m.type == "human"][0]
    assert user.text.startswith("[Today")                                   # the date, on the built input


# ── submit_answer checks the two approvals ───────────────────────────────────

@pytest.mark.asyncio
async def test_submit_without_running_is_refused(scripted_model):
    main = scripted_model(_review(), _submit(), _execute(), _review(call_id="r2"), _submit(call_id="s2"))
    reviewer = scripted_model(_verdict("pass"), _verdict("pass", call_id="v2"))
    state = await _run(main, reviewer)
    assert _submit_results(state) == [NOT_RUN, ANSWER_RECEIVED]
    assert state["final_result"]["status"] == "matched"


@pytest.mark.asyncio
async def test_submit_without_review_is_refused(scripted_model):
    main = scripted_model(_execute(), _submit(), _review(), _submit(call_id="s2"))
    state = await _run(main, scripted_model(_verdict("pass")))
    assert _submit_results(state) == [NOT_REVIEWED, ANSWER_RECEIVED]
    assert state["final_result"]["semantic_validation"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_sql_changed_after_review_is_refused(scripted_model):
    main = scripted_model(_execute(), _execute(FIXED, "e2"), _review(), _submit(FIXED), _review(FIXED, "r2"),
                          _submit(FIXED, "s2"))
    reviewer = scripted_model(_verdict("pass"), _verdict("pass", call_id="v2"))
    state = await _run(main, reviewer)
    assert _submit_results(state) == [NOT_REVIEWED, ANSWER_RECEIVED]
    assert state["final_result"]["sql"] == FIXED and state["final_result"]["semantic_validation"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_failed_review_refuses_submit_and_the_fix_is_reviewed(scripted_model):
    main = scripted_model(_execute(), _review(), _submit(), _execute(FIXED, "e2"), _review(FIXED, "r2"),
                          _submit(FIXED, "s2"))
    reviewer = scripted_model(_verdict("fail", [ISSUE]), _verdict("pass", call_id="v2"))
    state = await _run(main, reviewer)

    refusal = _submit_results(state)[0]
    assert refusal.startswith("Not submitted: the semantic-validator failed") and ISSUE["problem"] in refusal
    final = state["final_result"]
    assert final["sql"] == FIXED and final["semantic_validation"]["verdict"] == "passed"
    assert final["semantic_validation"]["rounds"] == 2


@pytest.mark.asyncio
async def test_submitting_a_failed_sql_with_a_justification_is_a_dispute(scripted_model):
    main = scripted_model(_execute(), _review(), _submit(validation_response="כל שורה היא הזמנה אחת"))
    state = await _run(main, scripted_model(_verdict("fail", [ISSUE])))
    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "disputed" and review["issues"] == [ISSUE]
    assert review["response"] == "כל שורה היא הזמנה אחת"
    shown = render_markdown(state["final_result"])
    assert "בדיקה עצמאית העלתה הסתייגות" in shown and "תגובת הסוכן" in shown


@pytest.mark.asyncio
async def test_review_limit_accepts_an_unreviewed_sql_as_not_validated(scripted_model, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_VALIDATOR_MAX_ROUNDS", 1)
    main = scripted_model(_execute(), _review(), _execute(FIXED, "e2"), _submit(FIXED))
    state = await _run(main, scripted_model(_verdict("fail", [ISSUE])))
    review = state["final_result"]["semantic_validation"]
    assert state["final_result"]["sql"] == FIXED
    assert review["verdict"] == "not_validated" and review["reason"] == "review limit reached (1)"


@pytest.mark.asyncio
async def test_brief_without_a_sql_block_cannot_approve_anything(scripted_model):
    no_sql = _call("task", {"description": "please check my count query", "subagent_type": "semantic-validator"}, "r1")
    main = scripted_model(_execute(), no_sql, _submit(), _review(call_id="r2"), _submit(call_id="s2"))
    reviewer = scripted_model(_verdict("fail", [{"problem": "no SQL", "evidence": "-"}]), _verdict("pass", call_id="v2"))
    state = await _run(main, reviewer)
    assert "none was provided" in _texts(reviewer.seen[0])
    assert _submit_results(state) == [NOT_REVIEWED, ANSWER_RECEIVED]
    assert state["final_result"]["semantic_validation"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_off_mode_still_requires_that_the_sql_ran(scripted_model):
    main = scripted_model(_submit(), _execute(), _submit(call_id="s2"))
    state = await _run(main, mode="off")
    assert _submit_results(state) == [NOT_RUN, ANSWER_RECEIVED]
    assert state["final_result"]["status"] == "matched"
    assert state["final_result"]["semantic_validation"] is None  # review off


@pytest.mark.asyncio
async def test_truncated_sample_is_re_run_unchanged_for_all_rows(scripted_model):
    sample = TrinoExecutionResult(success=True, rows=[[i] for i in range(6)], columns=["n"], row_count=6, query_id="s")
    full = TrinoExecutionResult(success=True, rows=[[i] for i in range(40)], columns=["n"], row_count=40, query_id="f")
    main = scripted_model(_execute(limit=5), _review(), _submit())
    agent = create_deep_agent(model=main, semantic_validation="always", validator_model=scripted_model(_verdict("pass")))
    with patch.object(sql_tools, "execute_query_sync", return_value=sample), \
            patch.object(verification, "execute_query_sync", return_value=full) as rerun:
        state = await agent.ainvoke({"messages": [("user", QUESTION)]}, {"configurable": {"thread_id": "s"}})
    assert state["final_result"]["status"] == "re_executed" and state["final_result"]["result"]["row_count"] == 40
    assert rerun.call_args.args[0].startswith(SQL)


# ── strict submit: no fallback ────────────────────────────────────────────────

ANSWER_AS_TEXT = json.dumps({"sql": SQL, "explanation_he": "x", "confidence": "high"})


@pytest.mark.asyncio
async def test_strict_submit_has_no_recovery_or_reminder(scripted_model, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "DEEP_AGENT_STRICT_SUBMIT", True)
    state = await _run(scripted_model(_execute(), AIMessage(ANSWER_AS_TEXT)), mode="off")
    assert state["final_result"]["status"] == "no_answer"
    assert not [m for m in state["messages"] if getattr(m, "name", None) == "submit_reminder"]


@pytest.mark.asyncio
async def test_default_submit_fallback_recovers_an_answer_written_as_text(scripted_model):
    state = await _run(scripted_model(_execute(), AIMessage(ANSWER_AS_TEXT)), mode="off")
    assert state["final_result"]["status"] == "matched" and state["final_result"]["recovered_from_text"] is True


# ── the reviewer and its records ─────────────────────────────────────────────

def test_parse_brief_takes_only_the_sql_and_the_bullets():
    sql, assumptions = parse_brief("I'm confident.\n```sql\nSELECT 1\n```\nAssumptions:\n- a\n* b\n1. c\nMore prose.")
    assert sql == "SELECT 1" and assumptions == ["a", "b", "c"]
    assert parse_brief("no sql here") == (None, [])


def test_review_request_marks_earlier_messages_and_missing_runs():
    text = review_request(["כמה טיסות יצאו מצרפת?", "ומאיטליה?"], SQL, [], [])
    assert "Earlier user messages" in text and "כמה טיסות יצאו מצרפת?" in text
    assert "Current user request\nומאיטליה?" in text
    assert "has not run in this turn" in text and "(none stated)" in text


@pytest.mark.asyncio
async def test_follow_up_turn_gives_the_reviewer_the_earlier_question(scripted_model):
    main = scripted_model(_execute(), _review(), _submit(), _execute(FIXED, "e2"), _review(FIXED, "r2"),
                          _submit(FIXED, "s2"))
    reviewer = scripted_model(_verdict("pass"), _verdict("pass", call_id="v2"))
    agent = create_deep_agent(model=main, semantic_validation="always", validator_model=reviewer)
    config = {"configurable": {"thread_id": "follow-up"}}
    with _fake_trino():
        await agent.ainvoke({"messages": [("user", QUESTION)]}, config)
        state = await agent.ainvoke({"messages": [("user", "ובלי כפילויות?")]}, config)
    second = _texts(reviewer.seen[1])
    assert "Earlier user messages" in second and QUESTION in second and "ובלי כפילויות?" in second
    assert state["final_result"]["semantic_validation"]["rounds"] == 1  # counted per turn


def test_fail_verdict_needs_evidence():
    with pytest.raises(ValueError):
        SubmitVerdict(verdict="fail", issues=[])
    assert SubmitVerdict(verdict="pass").issues == []


def test_validation_response_only_exists_when_review_is_required():
    assert "validation_response" in answer_schema(False, True).model_fields
    assert "validation_response" not in answer_schema(False, False).model_fields


def test_defaults():
    from agent.config import AgentSettings

    assert AgentSettings.model_fields["DEEP_AGENT_SEMANTIC_VALIDATION"].default == "always"
    assert AgentSettings.model_fields["DEEP_AGENT_STRICT_SUBMIT"].default is False


def test_summary_looks_at_the_review_of_the_final_sql():
    records = [{"id": "1", "verdict": "pass", "sql": normalize_sql(SQL), "issues": []},
               {"id": "2", "verdict": "fail", "sql": normalize_sql(FIXED), "issues": [ISSUE]}]
    assert validation_summary(records, SQL)["verdict"] == "passed"     # not the later review of another SQL
    assert validation_summary(records, FIXED)["verdict"] == "failed"
    assert validation_summary([], SQL)["reason"] == "no review of the final SQL"
    assert validation_summary(None, SQL) is None                       # review off
    passed = run_metrics([], 1.0, {"semantic_validation": validation_summary(records, SQL)})
    assert passed.semantic_validation_passed is True and passed.validation_rounds == 2


# ── unchanged harness behaviour ──────────────────────────────────────────────

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
    assert [m.name for m in state["messages"] if m.type == "tool"][:2] == ["task", "task"]


def test_reminder_is_not_the_users_words():
    from langchain_core.messages import HumanMessage

    from deep_agent.context import REMINDER_NAME, current_turn, user_request

    messages = [HumanMessage("the question"), AIMessage("text"), HumanMessage("submit!", name=REMINDER_NAME)]
    assert current_turn(messages) == messages[1:]
    assert user_request(SimpleNamespace(state={"messages": messages})) == "the question"


@pytest.mark.asyncio
async def test_on_demand_offers_the_reviewer_but_submit_only_needs_a_run(scripted_model):
    main = scripted_model(_execute(), _submit())
    state = await _run(main, scripted_model(), mode="on_demand")
    assert _submit_results(state) == [ANSWER_RECEIVED]
    review = state["final_result"]["semantic_validation"]
    assert review["verdict"] == "not_validated" and review["reason"] == "no review of the final SQL"
    assert "worth it before submit_answer" in _texts(main.seen[0]) or "semantic-validator" in _texts(main.seen[0])


@pytest.mark.asyncio
async def test_on_demand_review_is_reported_when_the_agent_asks_for_it(scripted_model):
    main = scripted_model(_execute(), _review(), _submit())
    state = await _run(main, scripted_model(_verdict("pass")), mode="on_demand")
    assert state["final_result"]["semantic_validation"]["verdict"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, offered", [("always", True), ("on_demand", True), ("off", False)])
async def test_modes_offer_the_reviewer(scripted_model, mode, offered):
    main = scripted_model(_execute(), _submit(), _review(), _submit(call_id="s2"))
    reviewer = scripted_model(_verdict("pass"))
    await _run(main, reviewer, mode=mode)
    task_tool = next((t for t in main.bound_tool_names[0] if t == "task"), None)
    assert task_tool == "task"  # general-purpose is always there
    assert ("semantic-validator" in _texts(main.seen[0])) is offered


def _needs_check(checks, call_id="v1"):
    return _call("submit_verdict", {"verdict": "needs_check", "checks": checks}, call_id)


CHECK = "SELECT COUNT(DISTINCT order_id) FROM orders"


@pytest.mark.asyncio
async def test_needs_check_sends_the_agent_to_run_it_and_the_reviewer_sees_the_result(scripted_model):
    main = scripted_model(_execute(), _review(), _submit(), _execute(CHECK, "e2"), _review(call_id="r2"),
                          _submit(call_id="s2"))
    reviewer = scripted_model(_needs_check(["check whether order_id repeats across rows"]),
                              _verdict("pass", call_id="v2"))
    state = await _run(main, reviewer)

    refusal = _submit_results(state)[0]
    assert refusal.startswith("Not submitted: the semantic-validator asks for these checks")
    assert "order_id repeats" in refusal
    second = _texts(reviewer.seen[1])
    assert "Checks you asked for in your previous review" in second and "order_id repeats" in second
    assert CHECK in second and "[42]" in second                     # the check's real recorded result
    final = state["final_result"]
    assert final["semantic_validation"]["verdict"] == "passed" and final["semantic_validation"]["rounds"] == 2


@pytest.mark.asyncio
async def test_the_reviewer_has_no_sql_tools(scripted_model):
    reviewer = scripted_model(_verdict("pass"))
    await _run(scripted_model(_execute(), _review(), _submit()), reviewer)
    assert reviewer.bound_tool_names[0] == ["submit_verdict"]


def test_needs_check_requires_named_checks():
    with pytest.raises(ValueError):
        SubmitVerdict(verdict="needs_check")
    assert SubmitVerdict(verdict="needs_check", checks=["x"]).checks == ["x"]


def test_pending_checks_without_new_queries_are_shown_as_none():
    records = [{"verdict": "needs_check", "checks": ["c1"], "after_execution": 1}]
    executed = [{"submitted_sql": SQL, "executed_sql": SQL, "columns": ["n"], "rows": [[42]], "row_count": 1,
                 "truncated": False}]
    text = review_request([QUESTION], SQL, [], executed, records)
    assert "- c1" in text and "Queries the analyst ran since\n(none)" in text



def test_parse_brief_ignores_an_inline_mention_of_a_sql_block():
    brief = ("Provide the SQL in a ```sql block``` and list assumptions. Also ```sql``` here.\n"
             "```sql\nSELECT 1\n```\n- a")
    assert parse_brief(brief) == ("SELECT 1", ["a"])


@pytest.mark.parametrize("sql, expected", [
    ("SELECT a FROM t ORDER BY b DESC LIMIT 1", "it ends with LIMIT 1"),
    ("WITH r AS (SELECT a, row_number() OVER (ORDER BY b DESC) rn FROM t) SELECT a FROM r WHERE rn = 1", "row_number()"),
    ("SELECT max_by(a, b) FROM t", "max_by"),
])
def test_notes_flag_sql_that_keeps_a_single_row(sql, expected):
    from deep_agent.validator.reviewer import answer_notes

    notes = answer_notes(sql, None)
    assert len(notes) == 1 and expected in notes[0] and "ties" in notes[0]


def test_notes_flag_cut_off_and_empty_results_and_stay_quiet_otherwise():
    from deep_agent.validator.reviewer import answer_notes

    rec = {"columns": ["a"], "rows": [[1]] * 100, "row_count": 100, "truncated": True}
    assert "cut off at 100 rows" in answer_notes("SELECT a FROM t", rec)[0]
    assert "empty (0 rows)" in answer_notes("SELECT a FROM t", {**rec, "rows": [], "row_count": 0, "truncated": False})[0]
    assert answer_notes("SELECT a FROM t LIMIT 10", {**rec, "row_count": 3, "truncated": False}) == []


def test_notes_reach_the_reviewer():
    executed = [{"submitted_sql": "SELECT a FROM t ORDER BY b DESC LIMIT 1", "executed_sql": "x", "columns": ["a"],
                 "rows": [[1]], "row_count": 1, "truncated": False}]
    text = review_request([QUESTION], "SELECT a FROM t ORDER BY b DESC LIMIT 1", [], executed)
    assert "## Notes on this answer" in text and "LIMIT 1" in text

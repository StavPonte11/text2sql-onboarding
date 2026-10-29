"""
Independent semantic review of the Deep Agent's answers.

The reviewer is a separate agent with a fresh context and read-only diagnostic tools. It gets
the user's request, the SQL, and (usually) the result rows and stated assumptions — never the
main agent's reasoning, so it can't simply agree with it — and returns `submit_verdict`
(pass / fail + issues with evidence). DEEP_AGENT_SEMANTIC_VALIDATION picks how it is used:

on_demand
    A `semantic-validator` subagent the agent calls through `task` when it judges a review
    worthwhile (its description says when, and what to send). The verdict comes back to the
    agent as JSON; acting on it is up to the agent. Each review is recorded.

always (default)
    Every accepted `submit_answer` call is reviewed before the run can end:
    - pass -> the run ends normally;
    - fail -> the "Answer received." tool message is replaced by the review's feedback and the
      model is sent back. It either fixes the SQL and submits again (reviewed again), or submits
      the same SQL with `validation_response` — a dispute, which ends the loop. If it stops
      without resubmitting, its last (failed) answer stands, marked "failed".
    DEEP_AGENT_VALIDATOR_MAX_ROUNDS optionally limits reviews per turn (default: no limit).

off
    No reviewer.

Both modes record reviews in `semantic_validations`; the outcome for the final answer goes to
final_result["semantic_validation"] (deep_agent.answer.verification.validation_summary). Semantic checks also
stay mandatory in the main agent's own prompt; the reviewer is an extra, independent check.

- reviewer.py  the reviewer agent: verdict format, read-only tools, its input, review records
- modes.py     how it is used: the `always` gate, the `on_demand` subagent, per-turn reset
"""

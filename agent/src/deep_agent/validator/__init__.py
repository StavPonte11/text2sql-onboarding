"""
Independent semantic review of the Deep Agent's SQL — before it is submitted.

The order is: run the SQL (execute_trino_query) → have it reviewed (the `semantic-validator` subagent,
through `task`) → submit it (submit_answer). The reviewer is a separate agent with a fresh context and
read-only diagnostic tools; its input is built by our code from facts — the user's own messages, the
SQL, its recorded rows, the agent's assumptions (as claims to check), the catalog and the date — never
from the agent's reasoning. It returns pass, or fail with issues and evidence; on fail the agent goes
back to work, or disputes (submits the same SQL with `validation_response`, shown to the user).

DEEP_AGENT_SEMANTIC_VALIDATION: `always` (default) makes submit_answer accept only SQL the reviewer passed
(or that the agent disputes); `on_demand` offers the reviewer and lets the agent decide; `off` drops it. submit_answer itself only checks the records
(deep_agent.answer.middleware.SubmitApprovalMiddleware) — the judging happens before it.

- reviewer.py  the reviewer agent: verdict format, read-only tools, its facts-only input, verdict records
- modes.py     the wiring: the subagent, the "before you submit" prompt, the per-turn reset
"""

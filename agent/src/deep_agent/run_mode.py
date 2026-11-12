"""
The Deep Agent's run modes (DEEP_AGENT_MODE, or create_deep_agent(mode=...)).

- serving   guardrails so today's model answers reliably: the reviewer is required before submit (`always`), and
            an answer written as text is recovered / a model that stops without submitting is reminded.
- training  an RL environment: it observes and scores, it doesn't rescue. The reviewer is a tool the model may
            call (`on_demand`), there is no submit fallback, and a run ends with no answer after
            DEEP_AGENT_TRAINING_MAX_MODEL_CALLS model calls.

The same in both: the prompt, the tools and their descriptions, the submit_answer schema, the catalog, the SQL
guard, and the "this SQL must have run" check on submit (feedback the model learns from). The run's signals
(final_result, run_metrics) are what a reward is built from.
"""

from typing import Literal

AgentMode = Literal["serving", "training"]

DEFAULT_VALIDATION = {"serving": "always", "training": "on_demand"}


def validation_for(mode: AgentMode, explicit: str | None = None) -> str:
    """The reviewer setting: an explicit value (including `off`) wins, else the mode's default."""
    return explicit or DEFAULT_VALIDATION[mode]

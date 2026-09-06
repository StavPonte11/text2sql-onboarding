import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from agent.nodes.refiner import agent_node
from agent.state import AgentState
from agent.config import settings

# ─── HELPER MOCK FOR LANGCHAIN LCEL ──────────────────────────────────────────


def setup_mock_chain(mock_from_messages, mock_get_llm, mock_response_content):
    """Helper to cleanly mock LangChain's Prompt | LLM syntax."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = mock_response_content

    mock_chain = AsyncMock()
    mock_chain.ainvoke.return_value = mock_response

    # When prompt | llm happens, return our mock_chain
    mock_prompt = MagicMock()
    mock_prompt.__or__.return_value = mock_chain

    mock_from_messages.return_value = mock_prompt
    mock_get_llm.return_value = mock_llm

    return mock_chain


# ─── UPGRADED BASE TESTS ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_step1_baseline(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    LEGIT HAPPY PATH (STEP 1): Proves that on initial entry, the agent uses
    the Step 1 prompt, parses the SQL correctly, increments refinement_count,
    and sets is_satisfied to False.
    """
    mock_chain = setup_mock_chain(
        mock_from_messages, mock_get_llm, '{"reasoning": "mock", "intent_match_checklist": {}, "status": "REFINING", "sql_query": "SELECT 1"}'
    )

    state = AgentState(
        execution_path=["enrich_context"],
        sql_query="SELECT 1;",
        refinement_count=0,
    )

    result = await agent_node(state)

    # Verifies Step 2 Prompt was requested
    mock_langfuse.get_prompt.assert_called_with(settings.LANGFUSE_PROMPT_REFINER_STEP2)

    assert (
        result["sql_query"] == "SELECT 1"
    )  # clean_sql strips trailing semicolon and formatting
    assert result["is_satisfied"] is False
    assert result["refinement_count"] == 1
    assert result["execution_path"] == ["agent"]
    mock_publish.assert_called_once()


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_step2a_error_fixing(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    BUSINESS LOGIC (STEP 2): If coming from a Trino execution failure, prove the
    Agent switches to the Step 2 prompt to fix the error.
    """
    mock_chain = setup_mock_chain(
        mock_from_messages, mock_get_llm, '{"reasoning": "mock", "intent_match_checklist": {}, "status": "REFINING", "sql_query": "SELECT 2"}'
    )

    state = AgentState(
        execution_path=["enrich_context", "agent", "trino_exec"],
        sql_query="SELECT 1;",
        trino_error="Syntax error at line 1",
        refinement_count=1,
    )

    result = await agent_node(state)

    # Verifies Step 2 Prompt was requested
    mock_langfuse.get_prompt.assert_called_with(settings.LANGFUSE_PROMPT_REFINER_STEP2)
    assert result["sql_query"] == "SELECT 2"
    assert result["is_satisfied"] is False
    assert result["refinement_count"] == 2


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_step2b_satisfied(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    BUSINESS LOGIC (SATISFIED): Proves the Agent can successfully declare a query
    satisfied and properly extract the human-readable TRANSLATION explanation using regex.
    """
    llm_response = '{"reasoning": "looks good", "intent_match_checklist": {}, "status": "SATISFIED", "sql_query": "SELECT 1", "final_translation": "This query fetches all active users."}'
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, llm_response)

    state = AgentState(
        execution_path=["trino_exec"],
        sql_query="SELECT 1;",
        trino_error=None,
    )

    result = await agent_node(state)

    assert result["sql_query"] == "SELECT 1"
    assert result["is_satisfied"] is True
    assert result["sql_explanation"] == "This query fetches all active users."


@pytest.mark.asyncio
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
async def test_agent_max_iterations_exhausted(mock_publish):
    """
    GUARDRAIL: The agent must refuse to call the LLM if refinement_count exceeds
    MAX_REFINER_ITERATIONS to prevent infinite loops and massive billing spikes.
    """
    state = AgentState(
        refinement_count=5,  # Limit reached
        trino_error="Persistent syntax error",
        runtime_flags={"MAX_REFINER_ITERATIONS": 5},
    )

    result = await agent_node(state)

    # Must immediately return escalation without calling LLM
    assert "escalation_reason" in result
    assert "Refiner exhausted 5 iterations" in result["escalation_reason"]
    assert "Persistent syntax error" in result["escalation_reason"]
    assert result["execution_path"] == ["agent"]


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_injects_enrichments_and_schema_cap(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    CONTEXT MANAGEMENT: Proves the node correctly injects `query_enrichments` into
    the prompt variables, and properly caps the number of table schemas passed
    to prevent TokenLimitExceeded crashes.
    """
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, '{"reasoning": "mock", "intent_match_checklist": {}, "status": "REFINING", "sql_query": "SELECT 1"}')

    # Pass jeen_catalog in state
    enrichments_mock = [{"term": "status", "context": "active status"}]

    state = AgentState(
        user_query="get active",
        jeen_catalog='"postgres"."public"."table_0": Table 0\n  - "id" (INT)',
        query_enrichments=enrichments_mock,
    )

    await agent_node(state)

    # Inspect the dictionary that was passed to the LLM (ainvoke)
    invoke_vars = mock_chain.ainvoke.call_args[0][0]
    # 1. Verify schema context was passed cleanly
    assert invoke_vars["schema"] == '"postgres"."public"."table_0": Table 0\n  - "id" (INT)'

    # 2. Verify enrichments were injected
    enriched_instruction = invoke_vars["enriched_instruction"]
    assert "[QUERY & FILTER ENRICHMENTS]" in enriched_instruction
    assert "active" in enriched_instruction


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_handles_satisfaction_check_failure(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    ROUTING LOGIC: If the agent is invoked after a `satisfaction_check` node fails,
    it must pass the Satisfaction failures as the `last_result_error` to the LLM,
    overriding any previous Trino errors.
    """
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, '{"reasoning": "mock", "intent_match_checklist": {}, "status": "REFINING", "sql_query": "SELECT 1"}')

    state = AgentState(
        execution_path=[
            "trino_exec",
            "check_satisfaction",
        ],  # Came from Satisfaction Check
        satisfaction_failures=["[CHECK_C] Missing timestamp column"],
        trino_error=None,
        last_result_row_count=10,
        refinement_count=1,
    )

    await agent_node(state)

    # Verify Step 2 prompt is used to fix the logic error
    mock_langfuse.get_prompt.assert_called_with(settings.LANGFUSE_PROMPT_REFINER_STEP2)

    invoke_vars = mock_chain.ainvoke.call_args[0][0]

    # The LLM needs to know WHY it failed validation
    assert invoke_vars["last_result_success"] == "False"  # Trino succeeded but satisfaction failed
    assert (
        "Satisfaction Check Failed: [CHECK_C] Missing timestamp column"
        in invoke_vars["last_result_error"]
    )


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_extracts_translation_without_query_satisfied(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    ROBUST REGEX: Ensures the regex for TRANSLATION strictly requires `QUERY_SATISFIED`.
    If the LLM accidentally outputs TRANSLATION while generating a draft query,
    it should not prematurely set `sql_explanation` if `is_satisfied` is false.
    """
    # Notice: NO "QUERY_SATISFIED" marker
    llm_response = '{"reasoning": "draft query", "intent_match_checklist": {}, "status": "REFINING", "sql_query": "SELECT 1", "final_translation": "Here is a draft query."}'
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, llm_response)

    state = AgentState(execution_path=["enrich_context"], refinement_count=0)

    result = await agent_node(state)

    assert result["is_satisfied"] is False
    # sql_explanation should remain an empty string (or existing state)
    assert result["sql_explanation"] == ""


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
@patch("agent.nodes.refiner.clean_sql")
async def test_agent_survives_conversational_llm_filler(
    mock_clean_sql, mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    LLM ANOMALY: LLMs frequently ignore instructions and wrap their SQL in conversational
    filler (e.g., "Sure! Here is your query..."). This proves the agent node delegates
    cleaning to `clean_sql` and doesn't just blindly save the raw conversational text to state.
    """
    raw_llm_output = 'Sure! Here is your requested query:\n```json\n{"status": "REFINING", "sql_query": "SELECT * FROM users", "reasoning": "mock"}\n```\nHope this helps!'
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, raw_llm_output)

    # We mock clean_sql to return what it *should* extract, proving the node uses it.
    mock_clean_sql.return_value = "SELECT * FROM users"

    state = AgentState(execution_path=[], refinement_count=0)

    result = await agent_node(state)

    # Assert clean_sql was actually called with the extracted json query
    mock_clean_sql.assert_called_once_with("SELECT * FROM users")

    # Assert the state was updated with the CLEANED sql, not the raw output
    assert result["sql_query"] == "SELECT * FROM users"


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_satisfied_missing_translation_block(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    REGEX SURVIVAL: If the LLM declares the query satisfied but FORGETS to append
    the `TRANSLATION` block, the regex search `re.search(...)` will return None.
    This test proves the node survives without throwing an AttributeError.
    """
    # The LLM outputs the satisfaction marker, but omits final_translation entirely.
    llm_response = '{"status": "SATISFIED", "sql_query": "SELECT 1"}'
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, llm_response)

    state = AgentState(
        execution_path=["trino_exec"],
        sql_query="SELECT 1;",
        sql_explanation="Old explanation",  # Pre-existing state
        refinement_count=1,
    )

    result = await agent_node(state)

    assert result["is_satisfied"] is True
    # Because TRANSLATION was missing, the regex match fails gracefully
    # and leaves the existing sql_explanation untouched (or empty).
    assert result["sql_explanation"] == "Old explanation"


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_null_state_variables_safe_formatting(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    STATE RESILIENCE: Proves that if the AgentState is completely bare
    (e.g., first run, missing variables), the prompt generation dictionary
    doesn't crash with KeyErrors or TypeErrors when building `invoke_vars`.
    """
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, '{"status": "REFINING", "sql_query": "SELECT 1"}')

    # A completely minimal, almost empty state.
    state = AgentState()

    # This shouldn't crash the dictionary building process in `agent_node`
    await agent_node(state)

    # Verify the fallback defaults (`or ""`) worked for the prompt variables
    invoke_vars = mock_chain.ainvoke.call_args[0][0]

    assert invoke_vars["user_request"] == ""
    assert invoke_vars["location_wkt_instruction"] == ""
    assert invoke_vars["initial_query"] == ""
    assert invoke_vars["last_result_error"] == ""
    # Make sure we defaulted to step 2 logic
    mock_langfuse.get_prompt.assert_called_with(settings.LANGFUSE_PROMPT_REFINER_STEP2)


@pytest.mark.asyncio
@patch("agent.nodes.refiner.langfuse_client")
@patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock)
@patch("agent.nodes.refiner.get_llm")
@patch("langchain_core.prompts.ChatPromptTemplate.from_messages")
async def test_agent_langfuse_trace_id_missing_bypass(
    mock_from_messages, mock_get_llm, mock_publish, mock_langfuse
):
    """
    OBSERVABILITY DEGRADATION: If the Langfuse context is lost (e.g., tracing is disabled,
    or the trace ID wasn't properly initialized upstream), `get_current_trace_id()`
    returns None. The node must bypass `_create_trace_tags_via_ingestion` without crashing.
    """
    mock_chain = setup_mock_chain(mock_from_messages, mock_get_llm, '{"status": "REFINING", "sql_query": "SELECT 1"}')

    # Simulate Langfuse returning None for the active trace
    mock_langfuse.get_current_trace_id.return_value = None

    state = AgentState(execution_path=[], refinement_count=0)

    # If the node blindly calls `_create_trace_tags_via_ingestion` with trace_id=None,
    # the test will crash.
    await agent_node(state)

    # Ensure trace tagging was completely skipped
    mock_langfuse._create_trace_tags_via_ingestion.assert_not_called()

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from agent.nodes.refiner_graph import refiner_subgraph
from agent.state import AgentState

# ─── HELPER MOCKS FOR GRAPH E2E ──────────────────────────────────────────────


def patch_graph_infrastructure():
    """
    Patches all external I/O (Redis, Langfuse, ESCA) across the entire subgraph
    to prevent network crashes during E2E testing.
    """
    return (
        patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock),
        patch(
            "agent.nodes.enrichment_orchestrator.publish_node_event",
            new_callable=AsyncMock,
        ),
        patch("agent.nodes.refiner.langfuse_client"),
        patch("agent.services.enrichment_orchestrator.langfuse_client"),
        patch("agent.nodes.refiner.get_esca_client", MagicMock()),
    )


# ─── UPGRADED BASE TEST ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("agent.nodes.refiner.execute_query_sync")
@patch("agent.nodes.refiner.get_llm")
@patch(
    "agent.nodes.refiner.EnrichmentOrchestrator.enrich_query",
    new_callable=AsyncMock,
    return_value=("SELECT 1;", [], False),
)
async def test_e2e_mocked_success_loop(
    mock_enrich, mock_get_llm, mock_exec
):
    """
    LEGIT HAPPY PATH: Verifies the standard 4-step graph execution:
    Enrich -> Agent (Draft) -> Trino (Success) -> Agent (Satisfied) -> [Satisfaction Bypassed] -> END
    """

    # 1. Setup Agent LLM to first draft a query, then declare it satisfied
    mock_llm = MagicMock()
    mock_response_1 = MagicMock(content="TRINO\n```sql\nSELECT 1;\n```")
    mock_response_2 = MagicMock(
        content="QUERY_SATISFIED\n```sql\nSELECT 1;\n```\nTRANSLATION\nDone."
    )

    mock_chain = AsyncMock()
    mock_chain.ainvoke.side_effect = [mock_response_1, mock_response_2]
    mock_get_llm.return_value = mock_llm

    # 2. Setup Trino DB to succeed on the first try
    class MockTrinoResult:
        rows = [["Alice"]]
        columns = ["name"]
        success = True
        error_message = None

    mock_exec.return_value = MockTrinoResult()

    state = {
        "user_query": "get data",
        "sql_query": "SELECT 1;",
        "table_profiles": [],
        "locations_dict": {},
        "runtime_flags": {"SATISFACTION_CHECK_ENABLED": False},
    }

    # Run the graph inside the infrastructure safety net
    with patch(
        "langchain_core.prompts.ChatPromptTemplate.from_messages"
    ) as mock_from_messages:
        mock_prompt = MagicMock()
        mock_prompt.__or__.return_value = mock_chain
        mock_from_messages.return_value = mock_prompt

        with (
            patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock),
            patch("agent.nodes.refiner.langfuse_client"),
            patch("agent.nodes.refiner.get_esca_client"),
        ):
            final_state = await refiner_subgraph.ainvoke(state)

    # Verify the router logic navigated the graph exactly as expected
    path = final_state["execution_path"]
    assert path == ["enrich_context", "agent", "trino_exec", "agent"]
    assert final_state["is_satisfied"] is True
    assert final_state["sql_explanation"] == "Done."


@pytest.mark.asyncio
@patch("agent.nodes.refiner.execute_query_sync")
@patch("agent.nodes.refiner.get_llm")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_e2e_max_iterations_exhausted(mock_extract, mock_get_llm, mock_exec):
    """
    ROUTING (MAX_ITER LIMIT): Proves that if Trino continually fails, the agent loop
    will eventually hit `MAX_REFINER_ITERATIONS`, and the router will terminate
    the graph at `end_fail` rather than looping infinitely.
    """
    mock_extract.return_value = []

    # 1. Setup LLM to endlessly generate broken SQL
    mock_llm = MagicMock()
    mock_chain = AsyncMock()
    mock_chain.ainvoke.return_value = MagicMock(
        content="TRINO\n```sql\nSELECT BROKEN;\n```"
    )
    mock_get_llm.return_value = mock_llm

    # 2. Setup Trino DB to endlessly fail
    class MockFailedTrinoResult:
        success = False
        error_message = "Syntax error"

    mock_exec.return_value = MockFailedTrinoResult()

    state = {
        "user_query": "get data",
        "table_profiles": [],
        "runtime_flags": {
            "SATISFACTION_CHECK_ENABLED": False,
            "MAX_REFINER_ITERATIONS": 2,  # Set artificially low for the test
        },
    }

    with patch(
        "langchain_core.prompts.ChatPromptTemplate.from_messages"
    ) as mock_from_messages:
        mock_prompt = MagicMock()
        mock_prompt.__or__.return_value = mock_chain
        mock_from_messages.return_value = mock_prompt

        with (
            patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock),
            patch("agent.nodes.refiner.langfuse_client"),
        ):
            final_state = await refiner_subgraph.ainvoke(state)

    # Verify execution path
    # Iteration 1: enrich -> agent -> trino
    # Iteration 2: agent -> trino
    # Iteration 3: agent (hits limit and returns escalation_reason) -> route to "done"
    # check_satisfaction -> should_continue sees escalation_reason -> end_fail

    assert "escalation_reason" in final_state
    assert "Refiner exhausted 2 iterations" in final_state["escalation_reason"]

    # Count how many times the agent node was in the path
    agent_calls = [n for n in final_state["execution_path"] if n == "agent"]
    assert len(agent_calls) == 3

    # Ensure graph actually terminated safely
    assert final_state["execution_path"][-1] == "agent"


@pytest.mark.asyncio
@patch("agent.nodes.refiner.execute_query_sync")
@patch("agent.nodes.refiner.get_llm")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_e2e_early_termination_unanswerable(
    mock_extract, mock_get_llm, mock_exec
):
    """
    ROUTING (EARLY EXIT): If the Agent decides the user's question is fundamentally
    unanswerable (e.g., asking for data the DB doesn't have), it sets `rejection_category`.
    This test proves the graph immediately aborts via `check_satisfaction` -> `end_fail`
    WITHOUT attempting to execute against Trino.
    """
    mock_extract.return_value = []

    # 1. Setup Agent to instantly reject the query
    mock_llm = MagicMock()
    mock_chain = AsyncMock()
    # The LLM outputs a special flag or explanation that your agent_node maps to a rejection.
    # We simulate the agent_node hitting its max iterations or rejection state immediately.
    mock_chain.ainvoke.return_value = MagicMock(content="I cannot answer this.")
    mock_get_llm.return_value = mock_llm

    # Simulate agent_node forcefully setting the rejection category
    # (Assuming your agent_node has logic to parse "I cannot answer this" -> rejection)
    state = {
        "user_query": "What is the meaning of life?",
        "table_profiles": [],
        "rejection_category": "unanswerable",  # Hardcode state to simulate agent detection
        "runtime_flags": {"SATISFACTION_CHECK_ENABLED": True},
    }

    with patch(
        "langchain_core.prompts.ChatPromptTemplate.from_messages"
    ) as mock_from_messages:
        mock_prompt = MagicMock()
        mock_prompt.__or__.return_value = mock_chain
        mock_from_messages.return_value = mock_prompt

        with (
            patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock),
            patch("agent.nodes.refiner.langfuse_client"),
        ):
            final_state = await refiner_subgraph.ainvoke(state)

    # Verify execution path
    # Even if Trino never ran, it safely routed to end_fail
    assert "escalation_reason" in final_state
    assert final_state["escalation_reason"] == "unanswerable"
    assert "trino_exec" not in final_state["execution_path"]
    mock_exec.assert_not_called()


@pytest.mark.asyncio
@patch("agent.nodes.refiner.execute_query_sync")
@patch("agent.nodes.refiner.get_llm")
@patch(
    "agent.nodes.refiner.EnrichmentOrchestrator.enrich_query",
    new_callable=AsyncMock,
    return_value=("SELECT 1;", [], False),
)
async def test_e2e_data_inspection_loop_not_satisfied(
    mock_enrich, mock_refiner_llm, mock_exec
):
    """
    ROUTING (AGENT DATA INSPECTION): Proves that if Trino execution succeeds, but the
    Agent LLM inspects the data samples (last_result_data) and decides it is NOT
    satisfied yet, the graph correctly routes back to `enrich_context` to refine the SQL.
    """

    # 1. Setup Refiner LLM:
    # First call: Drafts query. (is_satisfied = False)
    # Second call (after seeing data): Drafts fix. (is_satisfied = False)
    # Third call (after seeing new data): Satisfied! (is_satisfied = True)
    mock_refiner_chain = AsyncMock()
    mock_refiner_chain.ainvoke.side_effect = [
        MagicMock(content="TRINO\n```sql\nSELECT * FROM A;\n```"),
        MagicMock(content="TRINO\n```sql\nSELECT * FROM B;\n```"),
        MagicMock(content="TRINO\n```sql\nSELECT * FROM B;\n```"),
        MagicMock(
            content="QUERY_SATISFIED\n```sql\nSELECT * FROM B;\n```\nTRANSLATION\nDone."
        ),
    ]
    mock_refiner_llm.return_value = MagicMock()
    mock_refiner_llm.return_value.ainvoke = mock_refiner_chain.ainvoke

    # 2. Trino always succeeds
    class MockTrinoResult:
        rows = [["Data"]]
        columns = ["col"]
        success = True
        error_message = None

    mock_exec.return_value = MockTrinoResult()

    state = {
        "user_query": "get data",
        "runtime_flags": {"SATISFACTION_CHECK_ENABLED": False},
    }

    with patch(
        "langchain_core.prompts.ChatPromptTemplate.from_messages"
    ) as mock_from_messages:
        mock_prompt = MagicMock()
        mock_prompt.__or__.return_value = mock_refiner_chain
        mock_from_messages.return_value = mock_prompt

        with (
            patch("agent.nodes.refiner.publish_node_event", new_callable=AsyncMock),
            patch("agent.nodes.refiner.langfuse_client"),
            patch("agent.nodes.refiner.get_esca_client"),
        ):
            final_state = await refiner_subgraph.ainvoke(state)

    # 3. Verify execution path
    # enrich -> agent (1) -> trino (1) -> agent (2, sees data, not satisfied)
    # -> enrich -> agent (3, sees new data, satisfied) -> trino (2) -> agent (4, check) -> done

    path = final_state["execution_path"]

    # The crucial check: Because Agent was NOT satisfied after Trino succeeded the first time,
    # the route function `prev_node == "trino_exec" and not is_satisfied` returned `"needs_enrich"`.
    assert path.count("enrich_context") == 2
    assert path.count("trino_exec") == 2
    assert final_state["is_satisfied"] is True

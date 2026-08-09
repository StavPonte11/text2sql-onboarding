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
    LEGIT HAPPY PATH: Verifies the direct execution flow:
    Enrich -> Trino (Success) -> Agent (Satisfied) -> END
    """

    # 1. Setup Agent LLM to declare the query satisfied upon inspecting execution results
    mock_llm = MagicMock()
    mock_response = MagicMock(
        content="QUERY_SATISFIED\n```sql\nSELECT 1;\n```\nTRANSLATION\nDone."
    )

    mock_chain = AsyncMock()
    mock_chain.ainvoke.return_value = mock_response
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

    # Verify the router logic navigated the graph directly: enrich -> trino_exec -> agent
    path = final_state["execution_path"]
    assert path == ["enrich_context", "trino_exec", "agent"]
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

    assert "escalation_reason" in final_state
    assert "Refiner exhausted 2 iterations" in final_state["escalation_reason"]


@pytest.mark.asyncio
@patch("agent.nodes.refiner.execute_query_sync")
@patch("agent.nodes.refiner.get_llm")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_e2e_early_termination_unanswerable(
    mock_extract, mock_get_llm, mock_exec
):
    """
    ROUTING (EARLY EXIT): If the state already has `rejection_category`,
    the graph immediately routes from `enrich_context` to `end_fail`
    WITHOUT attempting to execute against Trino.
    """
    mock_extract.return_value = []

    mock_llm = MagicMock()
    mock_chain = AsyncMock()
    mock_chain.ainvoke.return_value = MagicMock(content="I cannot answer this.")
    mock_get_llm.return_value = mock_llm

    state = {
        "user_query": "What is the meaning of life?",
        "rejection_category": "unanswerable",
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

    # Verify execution path: routed directly to fail without trino_exec
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
    Agent LLM inspects the data samples and decides it is NOT satisfied yet, the graph
    dispatches the revised TRINO query back to `trino_exec`.
    """

    mock_refiner_chain = AsyncMock()
    mock_refiner_chain.ainvoke.side_effect = [
        MagicMock(content="TRINO\n```sql\nSELECT * FROM B;\n```"),
        MagicMock(
            content="QUERY_SATISFIED\n```sql\nSELECT * FROM B;\n```\nTRANSLATION\nDone."
        ),
    ]
    mock_refiner_llm.return_value = MagicMock()
    mock_refiner_llm.return_value.ainvoke = mock_refiner_chain.ainvoke

    # Trino always succeeds
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

    path = final_state["execution_path"]

    # enrich -> trino (1) -> agent (sees data, emits new TRINO query) -> enrich -> trino (2) -> agent (satisfied)
    assert path == [
        "enrich_context",
        "trino_exec",
        "agent",
        "enrich_context",
        "trino_exec",
        "agent",
    ]
    assert path.count("enrich_context") == 2
    assert path.count("trino_exec") == 2
    assert path.count("agent") == 2
    assert final_state["is_satisfied"] is True

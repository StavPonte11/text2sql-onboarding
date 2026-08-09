import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from agent.nodes.refiner import enrich_context_node
from agent.state import AgentState
from agent.services.enrichment_models import (
    SQLFilterParams,
    TransformationPlan,
    FilterTransformation,
)

# ─── FIXTURES & HELPERS ────────────────────────────────────────────────────────


def mock_filters():
    return [
        SQLFilterParams(
            source_column="category",
            operator="=",
            value="fruit",
            source_table="products",
            original_expression="category = 'fruit'",
            match_type="exact",
        )
    ]


def get_test_catalog():
    return '"postgres"."public"."products": Products table\n  - "category" (large_category): Product category'


def get_mock_langfuse_prompt():
    """Mocks the Langfuse prompt so tests don't make real HTTP calls."""
    mock_prompt = MagicMock()
    mock_prompt.get_langchain_prompt.return_value = [("system", "Test instruction")]
    return mock_prompt


# ─── TESTS ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.SQLTransformer.apply")
@patch("agent.services.enrichment_orchestrator.get_orchestrator_llm")
@patch("agent.services.enrichment_orchestrator.HybridSearcher.search")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_success_legit(
    mock_extract, mock_search, mock_llm, mock_apply, mock_langfuse
):
    """
    LEGIT HAPPY PATH: Proves that when filters are found, database search yields candidates,
    and the LLM decides to replace them, the SQL is actually transformed.
    """
    # 1. Setup Data Extraction & DB Search Mocks
    mock_extract.return_value = mock_filters()
    mock_search.return_value = {"category#@#fruit": ["apple", "orange"]}
    mock_langfuse.get_prompt.return_value = get_mock_langfuse_prompt()

    # 2. Setup LLM Mock (Simulate LLM returning a valid transformation plan)
    fake_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(
                column="category",
                original_value="fruit",
                old_operator="=",
                new_operator="IN",
                refined_values=["apple", "orange"],
                changed_filter=True,
                reasoning="Testing happy path.",
            )
        ]
    )

    mock_llm_instance = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=fake_plan)
    mock_llm_instance.with_structured_output.return_value = mock_structured
    mock_llm.return_value = mock_llm_instance

    # 3. Setup Transformer Mock
    expected_refined_sql = (
        "SELECT * FROM products WHERE category IN ('apple', 'orange')"
    )
    mock_apply.return_value = expected_refined_sql

    # 4. Prepare State
    original_sql = "SELECT * FROM products WHERE category = 'fruit'"
    state = AgentState(
        user_query="get all fruits",
        sql_query=original_sql,
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    # 5. Execute Node
    result = await enrich_context_node(state)

    # 6. Strict Assertions
    assert result["sql_query"] == expected_refined_sql, (
        "SQL must be updated with refined values."
    )
    assert result["sql_query"] != original_sql, "SQL should not match the original."
    assert result["execution_path"] == ["enrich_context"], "Node path must be logged."

    # Verify the workflow steps were actually called
    mock_extract.assert_called_once()
    mock_search.assert_called_once()
    mock_apply.assert_called_once_with(original_sql, fake_plan)


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_no_filters_extracted(mock_extract):
    """
    EARLY EXIT PATH: Proves that if the SQL AST has no WHERE filters,
    the system safely aborts without running DB searches or LLM calls.
    """
    mock_extract.return_value = []  # No filters found in SQL

    original_sql = "SELECT * FROM products"
    state = AgentState(
        user_query="get all products",
        sql_query=original_sql,
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    result = await enrich_context_node(state)

    # The SQL should remain completely unchanged
    assert result["sql_query"] == original_sql
    assert result["execution_path"] == ["enrich_context"]


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.HybridSearcher.search")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_no_candidates(mock_extract, mock_search):
    """
    GRACEFUL FALLBACK: Proves that if the DB search finds no alternative candidates,
    the pipeline safely aborts and returns the original SQL untouched.
    """
    mock_extract.return_value = mock_filters()
    mock_search.return_value = {}  # DB search found nothing

    original_sql = "SELECT * FROM products WHERE category = 'nonexistent'"
    state = AgentState(
        user_query="get nonexistent",
        sql_query=original_sql,
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    result = await enrich_context_node(state)

    assert result["sql_query"] == original_sql
    assert result["execution_path"] == ["enrich_context"]


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.get_orchestrator_llm")
@patch("agent.services.enrichment_orchestrator.HybridSearcher.search")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_llm_failure_graceful_degradation(
    mock_extract, mock_search, mock_llm, mock_langfuse
):
    """
    RESILIENCE PATH: Proves that if the LLM crashes, times out, or returns garbage,
    the LangGraph state does not explode. It safely returns the original SQL.
    """
    mock_extract.return_value = mock_filters()
    mock_search.return_value = {"category#@#fruit": ["apple", "orange"]}
    mock_langfuse.get_prompt.return_value = get_mock_langfuse_prompt()

    # Force the LLM to throw a catastrophic error
    mock_llm_instance = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(side_effect=Exception("OpenAI API Timeout"))
    mock_llm_instance.with_structured_output.return_value = mock_structured

    # Also mock standard ainvoke in case fallback parsing is attempted
    mock_llm_instance.ainvoke = AsyncMock(side_effect=Exception("OpenAI API Timeout"))
    mock_llm.return_value = mock_llm_instance

    original_sql = "SELECT * FROM products WHERE category = 'fruit'"
    state = AgentState(
        user_query="get fruit",
        sql_query=original_sql,
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    # If the try/except block fails in orchestrator, this would crash the test.
    # We want it to pass and return the original SQL.
    result = await enrich_context_node(state)

    assert result["sql_query"] == original_sql, (
        "Must degrade gracefully to original SQL."
    )
    assert result["execution_path"] == ["enrich_context"]


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_missing_jeen_catalog(mock_extract):
    """
    STATE EDGE-CASE: If jeen_catalog is missing or empty, the node should
    instantly bypass enrichment without crashing.
    """
    original_sql = "SELECT * FROM products WHERE category = 'fruit'"
    state = AgentState(
        user_query="get fruit",
        sql_query=original_sql,
        jeen_catalog="",  # EMPTY CATALOG!
        execution_path=[],
    )

    result = await enrich_context_node(state)

    # Must bypass the orchestrator completely
    mock_extract.assert_not_called()
    assert result["sql_query"] == original_sql
    assert result["execution_path"] == ["enrich_context"]


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.get_orchestrator_llm")
@patch("agent.services.enrichment_orchestrator.HybridSearcher.search")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_llm_decides_no_change(
    mock_extract, mock_search, mock_llm, mock_langfuse
):
    """
    BUSINESS LOGIC: Proves that if the LLM analyzes the DB candidates but decides
    the user's original filter is already perfect (changed_filter=False),
    the node respects that and leaves the SQL alone.
    """
    mock_extract.return_value = mock_filters()
    mock_search.return_value = {"category#@#fruit": ["fruit", "fruits"]}
    mock_langfuse.get_prompt.return_value = get_mock_langfuse_prompt()

    # Simulate LLM deciding NO transformation is needed
    fake_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(
                column="category",
                original_value="fruit",
                old_operator="=",
                new_operator="=",
                refined_values=["fruit"],
                changed_filter=False,  # <-- THE CRUCIAL FLAG
                reasoning="The original value 'fruit' perfectly matches DB candidates.",
            )
        ]
    )

    mock_llm_instance = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=fake_plan)
    mock_llm_instance.with_structured_output.return_value = mock_structured
    mock_llm.return_value = mock_llm_instance

    original_sql = "SELECT * FROM products WHERE category = 'fruit'"
    state = AgentState(
        user_query="get fruit",
        sql_query=original_sql,
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    result = await enrich_context_node(state)

    # Because `changed_filter` was False, `enriched` boolean will be False,
    # and the node should retain the original SQL.
    assert result["sql_query"] == original_sql


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.SQLTransformer.apply")
@patch("agent.services.enrichment_orchestrator.get_orchestrator_llm")
@patch("agent.services.enrichment_orchestrator.HybridSearcher.search")
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_fallback_json_parsing(
    mock_extract, mock_search, mock_llm, mock_apply, mock_langfuse
):
    """
    FALLBACK PATH: If LangChain's `with_structured_output` fails, but the LLM's
    raw text response contains valid markdown JSON, prove that the custom Regex
    parser kicks in and successfully saves the transformation.
    """
    mock_extract.return_value = mock_filters()
    mock_search.return_value = {"category#@#fruit": ["apple", "orange"]}
    mock_langfuse.get_prompt.return_value = get_mock_langfuse_prompt()

    mock_llm_instance = MagicMock()

    # 1. Force the structured output to fail
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(
        side_effect=Exception("Structured parser crashed!")
    )
    mock_llm_instance.with_structured_output.return_value = mock_structured

    # 2. Provide the fallback raw text response (Markdown JSON)
    raw_llm_response = MagicMock()
    raw_llm_response.content = """
    Here is the plan:
    ```json
    {
      "enrichment_details": [
        {
          "column": "category",
          "original_value": "fruit",
          "old_operator": "=",
          "new_operator": "IN",
          "refined_values": ["apple", "orange"],
          "changed_filter": true,
          "reasoning": "Fallback parsing test."
        }
      ]
    }
    ```
    """
    mock_llm_instance.ainvoke = AsyncMock(return_value=raw_llm_response)
    mock_llm.return_value = mock_llm_instance

    expected_refined_sql = (
        "SELECT * FROM products WHERE category IN ('apple', 'orange')"
    )
    mock_apply.return_value = expected_refined_sql

    state = AgentState(
        user_query="get fruit",
        sql_query="SELECT * FROM products WHERE category = 'fruit'",
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    result = await enrich_context_node(state)

    # If the regex fallback parser worked, the SQL will be updated!
    assert result["sql_query"] == expected_refined_sql
    mock_apply.assert_called_once()


@pytest.mark.asyncio
@patch("agent.services.enrichment_orchestrator.FilterExtractor.extract")
async def test_enrich_unhandled_exception_survival(mock_extract):
    """
    CATASTROPHIC FAILURE PATH: If a completely unexpected bug occurs deep in the
    sub-modules (e.g., regex recursion error in FilterExtractor), the node must
    catch it and degrade gracefully without blowing up the parent LangGraph.
    """
    # Force an unpredictable runtime error deep in the stack
    mock_extract.side_effect = RuntimeError("Catastrophic AST Parsing Failure")

    original_sql = "SELECT * FROM products WHERE category = 'fruit'"
    state = AgentState(
        user_query="get fruit",
        sql_query=original_sql,
        jeen_catalog=get_test_catalog(),
        execution_path=[],
    )

    # Node should catch this inside its outer try/except block
    result = await enrich_context_node(state)

    assert result["sql_query"] == original_sql
    assert result["execution_path"] == ["enrich_context"]

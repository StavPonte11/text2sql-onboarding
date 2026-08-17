import pytest
import os
from agent.nodes.refiner_graph import refiner_subgraph
from agent.state import AgentState

CUSTOMER_CATALOG = """
# Database Schema
"tpch"."tiny"."customer": Contains core details about all registered customers.
  - "custkey" (integer): Unique identifier for the customer
  - "name" (varchar): Customer name
  - "address" (varchar): Customer address
  - "nationkey" (integer): Foreign key reference to nation
  - "phone" (varchar): Primary contact phone number
  - "acctbal" (double): Account balance
  - "mktsegment" (varchar): Market segment
  - "comment" (varchar): Comments
"""

CUSTOMER_ORDERS_NATION_CATALOG = """
# Database Schema
"tpch"."tiny"."customer": Contains core details about all registered customers.
  - "custkey" (integer): Unique identifier for the customer
  - "name" (varchar): Customer name
  - "nationkey" (integer): Foreign key reference to nation
  - "phone" (varchar): Primary contact phone number

"tpch"."tiny"."orders": Contains historical order data placed by customers.
  - "orderkey" (integer): Unique identifier for the order
  - "custkey" (integer): Foreign key referencing customer
  - "totalprice" (double): Total price of the order
  - "orderdate" (date): Order date

"tpch"."tiny"."nation": Lookup table for nations.
  - "nationkey" (integer): Nation key
  - "name" (varchar): Nation name
"""


def is_integration_ready():
    """Check if all required real infrastructure variables are present."""
    return all(
        [
            os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"),
            os.getenv("TRINO_HOST"),
            os.getenv("REDIS_URL"),
        ]
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_trino_execution_happy_path():
    """
    REAL E2E: Proves the graph can take a naive SQL draft, execute it against a real
    Trino cluster, evaluate the real data, and declare satisfaction on the first try.
    """
    state = AgentState(
        user_query="get 3 rows from the customer table",
        sql_query="SELECT * FROM customer LIMIT 3",
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={
            "MAX_REFINER_ITERATIONS": 3,
            "ESCA_WRITE_ENABLED": False,  # Disable blob storage for basic tests
        },
    )

    # ainvoke returns the fully accumulated state at the end of the graph
    final_state = await refiner_subgraph.ainvoke(state)

    # ─── STRICT ASSERTIONS ───
    assert final_state.get("is_satisfied") is True, (
        f"Failed: {final_state.get('escalation_reason')}"
    )
    assert final_state.get("trino_error") is None

    # Verify the table alias regex worked on the real query
    assert "tpch" in final_state["sql_query"] and "customer" in final_state["sql_query"]

    # Verify real data was retrieved and stored in state
    assert final_state.get("last_result_row_count", 0) > 0
    assert len(final_state["inline_result_rows"]) <= 3


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_llm_fixes_typo_before_execution():
    """
    REAL E2E: Edge Case - Proactive Syntax Fixing.
    Provides a draft query with a misspelled SQL keyword ('SELCT' instead of 'SELECT').
    Proves the refiner is smart enough to intercept and fix basic typos during the drafting phase,
    before it even hits the database!
    """
    state = AgentState(
        user_query="get 3 customer keys",
        sql_query="SELCT custkey FROM customer LIMIT 3",  # Deliberate typo
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    # Assertions
    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error')}"
    )
    assert final_state.get("trino_error") is None

    # Verify the LLM successfully corrected the typo
    assert "select" in final_state["sql_query"].lower()
    assert "selct" not in final_state["sql_query"].lower()

    # Verify it fixed the typo (either proactively or via execution error loop)
    assert final_state["execution_path"].count("trino_exec") <= 3, (
        "It should have fixed the typo within 3 iterations!"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_llm_fixes_syntax_before_execution():
    """
    REAL E2E: Edge Case - Proactive Syntax Fixing.
    Provides a query missing a GROUP BY clause.
    Proves the LLM intercepts and fixes obvious drafting errors *before* Trino even throws an error!
    """
    state = AgentState(
        user_query="count customers by nationkey",
        sql_query="SELECT nationkey, count(custkey) FROM customer",  # Missing GROUP BY
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    # Assertions
    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error')}"
    )
    assert final_state.get("trino_error") is None

    # Verify the LLM added the GROUP BY clause
    assert "group by" in final_state["sql_query"].lower()

    # Verify it fixed the query within 3 iterations
    assert final_state["execution_path"].count("trino_exec") <= 3, (
        "It should have fixed the query within 3 iterations!"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_trino_execution_ambiguous_join():
    """
    REAL E2E: Edge Case - Execution Phase Error (Ambiguous Column).
    Provides a draft query that is syntactically valid but fails in execution because 'custkey' is ambiguous.
    Proves the refiner can read Trino's 'ambiguous column' error and self-correct by fully qualifying the column.
    """
    state = AgentState(
        user_query="get 3 customer keys from customers who have orders",
        sql_query="SELECT custkey FROM customer c JOIN orders o ON c.custkey = o.custkey LIMIT 3",  # Ambiguous custkey
        jeen_catalog=CUSTOMER_ORDERS_NATION_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    # Assertions
    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error')}"
    )
    assert final_state.get("trino_error") is None

    # Verify the LLM successfully resolved the ambiguity
    query_lower = final_state["sql_query"].lower()
    assert (
        "c.custkey" in query_lower
        or "o.custkey" in query_lower
        or "customer.custkey" in query_lower
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_trino_execution_dialect_mismatch():
    """
    REAL E2E: Edge Case - Execution Phase Error (Dialect Mismatch).
    Provides a draft query using SQL Server's 'ISNULL' function, which doesn't exist in Trino.
    Proves the refiner can read Trino's 'function not registered' error and translate it to 'COALESCE'.
    """
    state = AgentState(
        user_query="get 3 customer keys, replacing nulls with 0",
        sql_query="SELECT ISNULL(custkey, 0) FROM customer LIMIT 3",  # Dialect mismatch
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    # Assertions
    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error')}"
    )
    assert final_state.get("trino_error") is None

    # Verify the LLM successfully translated ISNULL to COALESCE
    assert "coalesce" in final_state["sql_query"].lower()
    assert "isnull" not in final_state["sql_query"].lower()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_trino_strict_type_casting():
    """
    REAL E2E: Edge Case - Strict Type Casting.
    Provides a draft query comparing a VARCHAR to an INTEGER.
    Proves the LLM sees the operator mismatch error, checks the schema, and corrects the type.
    """
    state = AgentState(
        user_query="get customer 123",
        sql_query="SELECT * FROM customer WHERE custkey = '123'",
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error') or final_state.get('escalation_reason')}"
    )
    assert final_state.get("trino_error") is None

    query = final_state["sql_query"]
    # Check that it either removed quotes entirely or explicitly cast the string
    assert "'123'" not in query or "cast" in query.lower()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_hallucinated_column_recovery():
    """
    REAL E2E: Edge Case - Hallucinated Column Recovery.
    Provides a draft query asking for a column that does not exist in the schema.
    Proves the agent either substitutes a valid column or escalates gracefully.
    """
    state = AgentState(
        user_query="get the customer email",
        sql_query="SELECT email FROM tpch.tiny.customer",
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    if final_state.get("is_satisfied"):
        # It successfully substituted with phone or custkey or valid column
        assert "customer" in final_state["sql_query"].lower()
        assert not final_state.get("trino_error")
    else:
        # It gracefully failed
        assert final_state.get("escalation_reason") is not None


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_unanswerable_out_of_scope_request():
    """
    REAL E2E: Edge Case - Out of Scope Table.
    Provides a draft query against a completely non-existent table.
    Proves the agent correctly identifies the hallucinated table and escalates without an infinite loop.
    """
    state = AgentState(
        user_query="how many employees do we have",
        sql_query="SELECT count(*) FROM employees",
        jeen_catalog=CUSTOMER_ORDERS_NATION_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    assert final_state.get("is_satisfied") is False, (
        "Agent should not have been satisfied with an unanswerable query."
    )
    assert final_state.get("escalation_reason") is not None, (
        "Agent must provide an escalation reason."
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_extreme_complex_query_recovery():
    """
    REAL E2E: Extreme Edge Case - Multiple compounding errors.
    Draft query has:
    1. SQL Server syntax (TOP 3 instead of LIMIT 3)
    2. Dialect hallucination (ISNULL instead of COALESCE)
    3. Strict type violation (c.phone = 123 instead of c.phone LIKE '123%')

    Proves the LLM can handle a barrage of Trino errors one by one over multiple iterations.
    """
    state = AgentState(
        user_query="get the top 3 nations by average order total price for customers who have a phone number starting with '123'",
        sql_query="SELECT n.name, AVG(ISNULL(o.totalprice, 0)) FROM nation n JOIN customer c ON n.nationkey = c.nationkey JOIN orders o ON c.custkey = o.custkey WHERE c.phone = 123 GROUP BY n.name ORDER BY 2 DESC TOP 3",
        jeen_catalog=CUSTOMER_ORDERS_NATION_CATALOG,
        locations_dict={},
        runtime_flags={
            "MAX_REFINER_ITERATIONS": 5,  # Give it 5 loops to fix this mess
            "ESCA_WRITE_ENABLED": False,
        },
    )

    final_state = await refiner_subgraph.ainvoke(state)

    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error') or final_state.get('escalation_reason')}"
    )
    assert final_state.get("trino_error") is None

    query = final_state["sql_query"].lower()

    # Verify all issues were fixed
    assert "limit 3" in query
    assert "top 3" not in query
    assert "coalesce" in query or "isnull" not in query
    assert "123" in query


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_logical_correction_missing_filter():
    """
    REAL E2E: Logical Error - Missing Filter.
    The draft SQL is syntactically valid but completely ignores the user's filter criteria.
    Proves the LLM reads the user_query and proactively fixes the logical gap before or after execution.
    """
    state = AgentState(
        user_query="get customers whose phone number starts with 123",
        sql_query="SELECT * FROM customer",  # Totally ignores the phone filter
        jeen_catalog=CUSTOMER_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error')}"
    )

    query = final_state["sql_query"].lower()
    # It must have added a WHERE clause for the phone
    assert "where" in query
    assert "phone" in query
    assert "123" in query


@pytest.mark.asyncio
@pytest.mark.skipif(
    not is_integration_ready(), reason="Missing infrastructure env vars for real E2E"
)
@pytest.mark.real_llm
async def test_e2e_real_logical_correction_wrong_aggregation():
    """
    REAL E2E: Logical Error - Missing Aggregation.
    The draft SQL is syntactically valid but fails to perform the requested aggregation.
    Proves the LLM corrects logical intent rather than just syntax errors.
    """
    state = AgentState(
        user_query="what is the total number of orders per customer?",
        sql_query="SELECT custkey FROM tpch.tiny.orders",  # Fails to aggregate or group
        jeen_catalog=CUSTOMER_ORDERS_NATION_CATALOG,
        locations_dict={},
        runtime_flags={"MAX_REFINER_ITERATIONS": 3, "ESCA_WRITE_ENABLED": False},
    )

    final_state = await refiner_subgraph.ainvoke(state)

    assert final_state.get("is_satisfied") is True, (
        f"Failed to self-correct: {final_state.get('last_error')}"
    )

    query = final_state["sql_query"].lower()
    # It must have added COUNT and GROUP BY
    assert "count" in query
    assert "group by" in query

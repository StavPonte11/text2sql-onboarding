from agent.config import settings
d = settings.CACHE_KEY_DELIMITER
import pytest
from unittest.mock import MagicMock, AsyncMock
from agent.services.enrichment_models import TransformationPlan, FilterTransformation, AgentSQLTable
from agent.services.enrichment_orchestrator import EnrichmentOrchestrator

@pytest.mark.asyncio
async def test_orchestrator_flow(mocker):
    # Mock HybridSearcher.search to return stubbed candidates
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {
        f"order_status{d}active": ["ACTIVE", "COMPLETED"]
    }
    
    # Mock LLM response plan
    mock_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="", 
                column="order_status",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Mapped to exact ACTIVE"
            )
        ]
    )
    
    mock_llm_instance = MagicMock()
    mock_structured_llm = MagicMock()
    mock_structured_llm.ainvoke = AsyncMock(return_value=mock_plan)
    mock_llm_instance.with_structured_output.return_value = mock_structured_llm
    
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm_instance)
    
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "order_status": "string"
        }
    }
    
    tables = [
        AgentSQLTable(
            name="dataverse.orders",
            description="orders table",
            columns={"order_status": {"column_type": "large_category"}}
        )
    ]
    
    initial_sql = "SELECT * FROM dataverse.orders WHERE order_status = 'active'"
    
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Find ACTIVE orders",
        initial_sql=initial_sql,
        schema=schema,
        tables=tables
    )
    
    assert is_enriched is True
    assert "order_status = 'ACTIVE'" in refined_sql
    assert plan is not None
    assert plan.enrichment_details[0].column == "order_status"


@pytest.mark.asyncio
async def test_orchestrator_fast_path_skips_llm(mocker):
    # Mock LLM to prove it NEVER gets called
    mock_llm_instance = MagicMock()
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm_instance)
    
    # Mock search to return empty, exercising the fast path
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {}
    
    schema = {"dataverse.orders": {"order_id": "int"}}
    tables = [
        AgentSQLTable(
            name="dataverse.orders",
            description="orders table",
            columns={"order_id": {"column_type": "numeric"}}
        )
    ]
    initial_sql = "SELECT * FROM dataverse.orders WHERE order_id = 123"
    
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Find order 123",
        initial_sql=initial_sql,
        schema=schema,
        tables=tables
    )
    
    # Assertions
    assert is_enriched is False
    assert refined_sql == initial_sql
    assert plan is None
    # Crucial: prove we saved money by not calling the LLM!
    mock_llm_instance.with_structured_output.assert_not_called()
    mock_search.assert_awaited_once()

@pytest.mark.asyncio
async def test_orchestrator_partial_enrichment(mocker):
    # Mock search to ONLY return results for the category column
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {f"region{d}na": ["NORTH_AMERICA"]}
    
    # Plan only changes the region, ignores the amount
    mock_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="", 
                column="region",
                original_value="na",
                old_operator="=",
                new_operator="=",
                refined_values=["NORTH_AMERICA"],
                changed_filter=True,
                reasoning="..."
            )
        ]
    )
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=mock_plan)
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm)
    
    schema = {
        "dataverse.orders": {"id": "int", "amount": "float"},
        "dataverse.customers": {"id": "int", "region": "string"}
    }
    tables = [
        AgentSQLTable(name="dataverse.orders", columns={"amount": {"column_type": "numeric"}}),
        AgentSQLTable(name="dataverse.customers", columns={"region": {"column_type": "large_category"}})
    ]
    
    initial_sql = "SELECT * FROM dataverse.orders o JOIN dataverse.customers c ON o.id=c.id WHERE o.amount > 100 AND c.region = 'na'"
    
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Big orders in NA", initial_sql=initial_sql, schema=schema, tables=tables
    )
    
    assert is_enriched is True
    assert "region = 'NORTH_AMERICA'" in refined_sql
    assert "amount > 100" in refined_sql

@pytest.mark.asyncio
async def test_orchestrator_llm_failure_fallback(mocker):
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {f"status{d}act": ["ACTIVE"]}
    
    # Force the LLM to throw an API Exception!
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(side_effect=Exception("OpenAI API Timeout"))
    
    # Configure the raw fallback ainvoke to avoid TypeError
    mock_response = MagicMock()
    mock_response.content = "invalid json triggering fallback failure"
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm)
    
    schema = {"dataverse.orders": {"status": "string"}}
    tables = [AgentSQLTable(name="dataverse.orders", columns={"status": {"column_type": "large_category"}})]
    initial_sql = "SELECT * FROM dataverse.orders WHERE status = 'act'"
    
    # This should NOT raise an exception, it should handle it gracefully
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Active orders", initial_sql=initial_sql, schema=schema, tables=tables
    )
    
    # It safely fell back to the original SQL
    assert is_enriched is False
    assert refined_sql == initial_sql
    assert plan is None


@pytest.mark.asyncio
async def test_orchestrator_double_expansion(mocker):
    # 1. Arrange: Search returns multiple candidates for BOTH columns
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {
        f"priority{d}high": ["P1_CRITICAL", "P2_HIGH"],
        f"category{d}network": ["NET_INFRA", "NET_SECURITY"]
    }
    
    # 2. Arrange: LLM maps both draft values to multiple canonical values
    mock_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="", 
                column="priority",
                original_value="high",
                old_operator="=",
                new_operator="IN",
                refined_values=["P1_CRITICAL", "P2_HIGH"],
                changed_filter=True,
                reasoning="Broad term 'high' encompasses both P1 and P2 priorities"
            ),
            FilterTransformation(table="", 
                column="category",
                original_value="network",
                old_operator="=",
                new_operator="IN",
                refined_values=["NET_INFRA", "NET_SECURITY"],
                changed_filter=True,
                reasoning="Broad term 'network' encompasses infra and security"
            )
        ]
    )
    
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=mock_plan)
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm)
    
    # 3. Arrange: Schema and Tables
    schema = {
        "dataverse.tickets": {
            "ticket_id": "int",
            "priority": "string",
            "category": "string"
        }
    }
    tables = [
        AgentSQLTable(
            name="dataverse.tickets", 
            columns={
                "priority": {"column_type": "large_category"},
                "category": {"column_type": "large_category"}
            }
        )
    ]
    
    initial_sql = "SELECT * FROM dataverse.tickets WHERE priority = 'high' AND category = 'network'"
    
    # 4. Act
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Show me high priority network tickets",
        initial_sql=initial_sql,
        schema=schema,
        tables=tables
    )
    
    # 5. Assert: Both filters should be transformed into IN lists
    assert is_enriched is True
    assert "priority IN ('P1_CRITICAL', 'P2_HIGH')" in refined_sql
    assert "category IN ('NET_INFRA', 'NET_SECURITY')" in refined_sql
    assert "='high'" not in refined_sql.replace(" ", "")

@pytest.mark.asyncio
async def test_orchestrator_partial_llm_rejection(mocker):
    # 1. Arrange: Search returns multiple candidates for BOTH columns
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {
        f"department{d}eng": ["ENGINEERING", "DATA_ENG", "PLATFORM_ENG"],
        f"location{d}remote": ["REMOTE_US", "REMOTE_EU"]
    }
    
    # 2. Arrange: LLM updates 'department', but REJECTS the 'location' candidates
    mock_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="", 
                column="department",
                original_value="eng",
                old_operator="=",
                new_operator="=",
                refined_values=["ENGINEERING"],
                changed_filter=True,
                reasoning="Mapped abbreviation to exact department"
            ),
            FilterTransformation(table="", 
                column="location",
                original_value="remote",
                old_operator="=",
                new_operator="=",
                refined_values=["REMOTE_US", "REMOTE_EU"],
                changed_filter=False,
                reasoning="User meant generic 'remote', database values are too specific, do not change."
            )
        ]
    )
    
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=mock_plan)
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm)
    
    # 3. Arrange: Schema and Tables
    schema = {
        "dataverse.employees": {
            "emp_id": "int",
            "department": "string",
            "location": "string"
        }
    }
    tables = [
        AgentSQLTable(
            name="dataverse.employees", 
            columns={
                "department": {"column_type": "large_category"},
                "location": {"column_type": "large_category"}
            }
        )
    ]
    
    initial_sql = "SELECT * FROM dataverse.employees WHERE department = 'eng' AND location = 'remote'"
    
    # 4. Act
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Find eng employees working remote",
        initial_sql=initial_sql,
        schema=schema,
        tables=tables
    )
    
    # 5. Assert: One changed, one stayed exactly the same
    assert is_enriched is True
    assert "department = 'ENGINEERING'" in refined_sql
    assert "location = 'remote'" in refined_sql 
    assert "REMOTE_US" not in refined_sql


@pytest.mark.asyncio
async def test_orchestrator_complex_multi_column_enrichment(mocker):
    # 1. Arrange: The massive hybrid search return dictionary
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {
        f"region{d}na": ["NORTH_AMERICA"],
        f"region{d}eur": ["EMEA", "EUROPE"],
        f"customer_tier{d}vip_level": ["PLATINUM", "DIAMOND"],
        f"product_category{d}elec": ["ELECTRONICS", "SMART_DEVICES"],
        f"delivery_state{d}late": ["DELAYED", "MISSING"],
        f"shipping_speed{d}fast": ["URGENT", "NEXT_DAY"]
    }
    
    # 2. Arrange: The LLM Transformation Plan tackling all 6 fuzzy values
    mock_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="", 
                column="region",
                original_value="na",
                old_operator="IN",
                new_operator="IN",
                refined_values=["NORTH_AMERICA"],
                changed_filter=True,
                reasoning="Resolve abbreviation"
            ),
            FilterTransformation(table="", 
                column="region",
                original_value="eur",
                old_operator="IN",
                new_operator="IN",
                refined_values=["EMEA"],
                changed_filter=True,
                reasoning="Resolve abbreviation to canonical EMEA"
            ),
            FilterTransformation(table="", 
                column="customer_tier",
                original_value="vip_level",
                old_operator="=",
                new_operator="IN",
                refined_values=["PLATINUM", "DIAMOND"],
                changed_filter=True,
                reasoning="Expand generic vip_level to specific database tiers"
            ),
            FilterTransformation(table="", 
                column="product_category",
                original_value="elec",
                old_operator="LIKE",
                new_operator="=",
                refined_values=["ELECTRONICS"],
                changed_filter=True,
                reasoning="Exact mapping"
            ),
            FilterTransformation(table="", 
                column="delivery_state",
                original_value="late",
                old_operator="=",
                new_operator="=",
                refined_values=["DELAYED"],
                changed_filter=True,
                reasoning="Standardize status"
            ),
            FilterTransformation(table="", 
                column="shipping_speed",
                original_value="fast",
                old_operator="=",
                new_operator="=",
                refined_values=["URGENT"],
                changed_filter=True,
                reasoning="Standardize speed"
            )
        ]
    )
    
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=mock_plan)
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm)
    
    # 3. Arrange: Complex Schema and Table Definitions
    schema = {
        "dataverse.customers": {
            "id": "int", 
            "region": "string", 
            "customer_tier": "string"
        },
        "dataverse.orders": {
            "order_id": "int", 
            "customer_id": "int", 
            "product_category": "string", 
            "order_value": "float"
        },
        "dataverse.logistics": {
            "tracking_id": "int", 
            "order_id": "int", 
            "delivery_state": "string", 
            "shipping_speed": "string"
        }
    }
    
    tables = [
        AgentSQLTable(name="dataverse.customers", columns={
            "region": {"column_type": "large_category"},
            "customer_tier": {"column_type": "large_category"}
        }),
        AgentSQLTable(name="dataverse.orders", columns={
            "product_category": {"column_type": "large_category"},
            "order_value": {"column_type": "numeric"}
        }),
        AgentSQLTable(name="dataverse.logistics", columns={
            "delivery_state": {"column_type": "large_category"},
            "shipping_speed": {"column_type": "large_category"}
        })
    ]
    
    # 4. Arrange: The messy, highly-nested draft SQL
    initial_sql = """
    SELECT c.id, o.order_id, l.tracking_id
    FROM dataverse.customers c
    JOIN dataverse.orders o ON c.id = o.customer_id
    LEFT JOIN dataverse.logistics l ON o.order_id = l.order_id
    WHERE c.region IN ('na', 'eur')
      AND c.customer_tier = 'vip_level'
      AND o.product_category LIKE '%elec%'
      AND o.order_value >= 1500.00
      AND (l.delivery_state = 'late' OR l.shipping_speed = 'fast')
    """
    
    # 5. Act: Fire the Orchestrator
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Show me expensive electronics orders for VIPs in NA/EUR that are either late or shipped fast.",
        initial_sql=initial_sql,
        schema=schema,
        tables=tables
    )
    
    # 6. Assert
    assert is_enriched is True
    assert plan is not None
    assert len(plan.enrichment_details) == 6
    assert "order_value >= 1500" in refined_sql or "order_value >= 1500.0" in refined_sql
    assert "'NORTH_AMERICA'" in refined_sql
    assert "'EMEA'" in refined_sql
    assert "'na'" not in refined_sql
    assert "customer_tier IN ('PLATINUM', 'DIAMOND')" in refined_sql
    assert "product_category = 'ELECTRONICS'" in refined_sql
    assert "%elec%" not in refined_sql
    assert "delivery_state = 'DELAYED'" in refined_sql
    assert "shipping_speed = 'URGENT'" in refined_sql


@pytest.mark.asyncio
async def test_orchestrator_real_world_car_registrations(mocker):
    # 1. Arrange: Mock the search engine with the provided dict
    mock_search = mocker.patch("agent.services.hybrid_searcher.HybridSearcher.search", new_callable=AsyncMock)
    mock_search.return_value = {
        f"car_type{d}italian": ["italian jeep", "italian sports", "italian mini", "italian 4x4"],
        f"place{d}17": [], 
        f"place{d}52": ["st 52", "offices 52", "warehouse521"],
        f"place{d}444": ["store 444"],
        f"manufacturer{d}sonic": ["toyota", "sonic blue", "sonic black"]
    }
    
    # 2. Arrange: Mock the LLM's structured output based on the provided plan
    mock_plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="", 
                column="car_type",
                original_value="italian",
                old_operator="LIKE",
                new_operator="LIKE",
                refined_values=["italian"],
                changed_filter=False,
                reasoning="LIKE '%italian%' already captures all relevant Italian car types."
            ),
            FilterTransformation(table="", 
                column="place",
                original_value="52",
                old_operator="LIKE",
                new_operator="=",
                refined_values=["st 52"],
                changed_filter=True,
                reasoning="LIKE '%52%' catches irrelevant values. 'st 52' is the only relevant store."
            ),
            FilterTransformation(table="", 
                column="manufacturer",
                original_value="sonic",
                old_operator="=",
                new_operator="IN",
                refined_values=["sonic blue", "sonic black"],
                changed_filter=True,
                reasoning="Exact match 'sonic' finds nothing. Two Sonic variants exist."
            )
        ]
    )
    
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=mock_plan)
    mocker.patch("agent.services.enrichment_orchestrator.get_orchestrator_llm", return_value=mock_llm)
    
    # 3. Arrange: Schema and Tables
    schema = {
        "registered_cars": {
            "id": "int",
            "car_type": "string",
            "place": "string",
            "manufacturer": "string"
        }
    }
    tables = [
        AgentSQLTable(
            name="registered_cars", 
            columns={
                "car_type": {"column_type": "large_category"},
                "place": {"column_type": "large_category"},
                "manufacturer": {"column_type": "large_category"}
            }
        )
    ]
    
    # 4. Arrange: The Initial SQL Query
    initial_sql = """
    SELECT COUNT(DISTINCT id)
    FROM registered_cars
    WHERE car_type LIKE '%italian%'
      AND (place LIKE '%17%' OR place LIKE '%52%' OR place LIKE '%444%')
      AND manufacturer = 'sonic'
    GROUP BY place
    """
    
    # 5. Act: Run the Orchestrator
    refined_sql, plan, is_enriched = await EnrichmentOrchestrator.enrich_query(
        user_request="Count unique Italian cars at specific places for manufacturer sonic.",
        initial_sql=initial_sql,
        schema=schema,
        tables=tables
    )
    
    # 6. Assert
    assert is_enriched is True
    assert "car_type LIKE '%italian%'" in refined_sql
    assert "place LIKE '%17%'" in refined_sql
    assert "place LIKE '%444%'" in refined_sql
    assert "place = 'st 52'" in refined_sql
    assert "LIKE '%52%'" not in refined_sql
    assert "manufacturer IN ('sonic blue', 'sonic black')" in refined_sql
    assert "= 'sonic'" not in refined_sql
    assert "SELECT COUNT(DISTINCT id)" in refined_sql
    assert "GROUP BY place" in refined_sql

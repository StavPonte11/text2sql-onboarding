import pytest
from agent.services.enrichment_models import TransformationPlan, FilterTransformation
from agent.services.sql_transformer import SQLTransformer

def test_transform_eq_to_eq():
    sql = "SELECT * FROM dataverse.orders WHERE order_status = 'active'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.orders", 
                column="order_status",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Exact match refinement"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "order_status = 'ACTIVE'" in refined

def test_transform_like_to_eq():
    sql = "SELECT * FROM dataverse.orders WHERE order_status LIKE '%active%'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.orders", 
                column="order_status",
                original_value="%active%",
                old_operator="LIKE",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="LIKE to EQ refinement"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "order_status = 'ACTIVE'" in refined

def test_transform_eq_to_in():
    sql = "SELECT * FROM dataverse.orders WHERE order_status = 'active'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.orders", 
                column="order_status",
                original_value="active",
                old_operator="=",
                new_operator="IN",
                refined_values=["ACTIVE", "COMPLETED"],
                changed_filter=True,
                reasoning="One to many refinement"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "order_status IN ('ACTIVE', 'COMPLETED')" in refined

def test_transform_no_change():
    sql = "SELECT * FROM dataverse.orders WHERE order_status = 'active'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.orders", 
                column="order_status",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=False,
                reasoning="Keep unchanged"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "order_status = 'active'" in refined

def test_transform_multiple_columns():
    sql = "SELECT * FROM orders WHERE status = 'act' AND region LIKE 'na%'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="status",
                original_value="act",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Standardize status"
            ),
            FilterTransformation(table="orders", 
                column="region",
                original_value="na%",
                old_operator="LIKE",
                new_operator="=",
                refined_values=["NORTH_AMERICA"],
                changed_filter=True,
                reasoning="Standardize region and swap LIKE for EQ"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "status = 'ACTIVE'" in refined
    assert "region = 'NORTH_AMERICA'" in refined

def test_transform_in_to_eq():
    sql = "SELECT * FROM orders WHERE status IN ('active', 'fake_status')"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="status",
                original_value="active",
                old_operator="IN",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Removed invalid status and downgraded to EQ"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "status IN ('ACTIVE', 'fake_status')" in refined

def test_transform_with_table_alias():
    sql = "SELECT * FROM dataverse.orders o WHERE o.order_status = 'active'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.orders", 
                column="order_status",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Alias handling"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "o.order_status = 'ACTIVE'" in refined

def test_transform_numeric_value():
    sql = "SELECT * FROM orders WHERE order_id = 12"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="order_id",
                original_value="12",
                old_operator="=",
                new_operator="=",
                refined_values=["12345"],
                changed_filter=True,
                reasoning="Corrected typo in ID"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "order_id = '12345'" in refined

def test_transform_unrelated_plan():
    sql = "SELECT * FROM orders WHERE region = 'US'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="order_status",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Standardize status"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "region = 'US'" in refined
    assert "order_status" not in refined

def test_transform_case_insensitive_matching():
    sql = "SELECT * FROM orders WHERE sTaTuS = 'AcTiVe'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="STATUS",
                original_value="ACTIVE",
                old_operator="=",
                new_operator="=",
                refined_values=["COMPLETED"],
                changed_filter=True,
                reasoning="Case insensitivity test"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "status = 'completed'" in refined.lower()

def test_transform_multiple_identical_columns():
    sql = "SELECT * FROM orders WHERE status = 'active' OR status = 'pending'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="status",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE_REFINED"],
                changed_filter=True,
                reasoning="Only refine one of the OR conditions"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "status = 'ACTIVE_REFINED'" in refined
    assert "status = 'pending'" in refined

def test_transform_is_null():
    sql = "SELECT * FROM orders WHERE order_notes IS NULL"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="order_notes",
                original_value="null",
                old_operator="IS NULL",
                new_operator="=",
                refined_values=["NO_NOTES"],
                changed_filter=True,
                reasoning="Replace NULL check with a default string"
            ) 
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "order_notes = 'NO_NOTES'" in refined
    assert "IS NULL" not in refined

def test_transform_arbitrary_operators():
    sql = "SELECT * FROM orders WHERE amount > 100"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="amount",
                original_value="100",
                old_operator=">",
                new_operator=">=",
                refined_values=["150"],
                changed_filter=True,
                reasoning="Change operator from GT to GTE and adjust threshold"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "amount >= '150'" in refined


def test_transform_inequality_to_eq():
    sql = "SELECT * FROM orders WHERE risk_score < 50"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="risk_score",
                original_value="50",
                old_operator="<",
                new_operator="=",
                refined_values=["LOW_RISK"],
                changed_filter=True,
                reasoning="Convert numeric threshold to exact category match"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "risk_score = 'LOW_RISK'" in refined
    assert "<" not in refined


def test_transform_in_to_inequality():
    sql = "SELECT * FROM orders WHERE priority IN ('1', '2', '3')"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="priority",
                original_value="1",
                old_operator="IN",
                new_operator="<=",
                refined_values=["3"],
                changed_filter=True,
                reasoning="Collapse IN list into a cleaner <= threshold"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "priority IN ('3', '2', '3')" in refined

def test_transform_flip_inequality_direction():
    sql = "SELECT * FROM orders WHERE start_date >= '2024-01-01'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="start_date",
                original_value="2024-01-01",
                old_operator=">=",
                new_operator="<",
                refined_values=["2024-01-01"],
                changed_filter=True,
                reasoning="Flip logic direction based on user intent"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "start_date < '2024-01-01'" in refined
    assert ">=" not in refined


def test_transform_neq_to_eq():
    sql = "SELECT * FROM orders WHERE status != 'failed'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="status",
                original_value="failed",
                old_operator="!=",
                new_operator="=",
                refined_values=["SUCCESS"],
                changed_filter=True,
                reasoning="Translate negative filter to positive exact match"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "status = 'SUCCESS'" in refined
    assert "!=" not in refined
    assert "<>" not in refined


def test_transform_operator_mismatch_safety():
    sql = "SELECT * FROM orders WHERE amount > 100"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="orders", 
                column="amount",
                original_value="100",
                old_operator="=",
                new_operator="<",
                refined_values=["50"],
                changed_filter=True,
                reasoning="Plan hallucinated the original operator"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    assert "amount > 100" in refined
    assert "amount < 50" not in refined


def test_transform_monster_complex_query():
    sql = """
    SELECT o.order_id, c.name 
    FROM dataverse.orders o
    JOIN dataverse.customers c ON o.customer_id = c.id
    WHERE o.status = 'act' 
      AND c.status IN ('unverified', 'new')
      AND o.amount > 1000
      AND (o.region LIKE 'na%' OR c.region = 'north_america')
      AND o.start_date >= '2024-01-01'
      AND o.start_date <= '2024-12-31'
    """
    
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.orders", 
                column="status",
                original_value="act",
                old_operator="=",
                new_operator="=",
                refined_values=["ACTIVE"],
                changed_filter=True,
                reasoning="Standardize order status"
            ),
            FilterTransformation(table="dataverse.customers", 
                column="status",
                original_value="unverified",
                old_operator="IN",
                new_operator="=",
                refined_values=["PENDING_VERIFICATION"],
                changed_filter=True,
                reasoning="Standardize customer status"
            ),
            FilterTransformation(table="dataverse.orders", 
                column="amount",
                original_value="1000",
                old_operator=">",
                new_operator=">=",
                refined_values=["5000"],
                changed_filter=True,
                reasoning="Increase minimum threshold and include exact bound"
            ),
            FilterTransformation(table="dataverse.orders", 
                column="region",
                original_value="na%",
                old_operator="LIKE",
                new_operator="IN",
                refined_values=["US", "CA"],
                changed_filter=True,
                reasoning="Expand North America wildcard to specific country list"
            ),
            FilterTransformation(table="dataverse.orders", 
                column="start_date",
                original_value="2024-01-01",
                old_operator=">=",
                new_operator=">=",
                refined_values=["2025-01-01"],
                changed_filter=True,
                reasoning="Shift the start date forward by a year"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    
    assert "status = 'ACTIVE'" in refined
    assert "status IN ('PENDING_VERIFICATION', 'new')" in refined
    assert "unverified" not in refined
    assert "amount >= '5000'" in refined
    assert "1000" not in refined
    assert "region IN ('US', 'CA')" in refined
    assert "na%" not in refined
    assert "region = 'north_america'" in refined
    assert "start_date >= '2025-01-01'" in refined
    assert "start_date <= '2024-12-31'" in refined

def test_transform_joined_table_qualification():
    sql = "SELECT * FROM db.users u JOIN db.admins a ON u.id = a.id WHERE u.status = 'active' AND a.status = 'active'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(
                column="status",
                table="db.admins",
                original_value="active",
                old_operator="=",
                new_operator="=",
                refined_values=["SUPER_ACTIVE"],
                changed_filter=True,
                reasoning="Transform admin status only"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    
    # Verify ONLY the admin status changed
    assert "a.status = 'SUPER_ACTIVE'" in refined
    assert "u.status = 'active'" in refined



def test_transform_preserve_numeric_string_literal():
    sql = "SELECT * FROM dataverse.users WHERE zip_code = '444'"
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="dataverse.users", 
                column="zip_code",
                original_value="444",
                old_operator="=",
                new_operator="=",
                refined_values=["00444"],
                changed_filter=True,
                reasoning="Preserve leading zeros in string literals"
            )
        ]
    )
    
    refined = SQLTransformer.apply(sql, plan)
    # The output MUST be a quoted string, not the number 444
    assert "zip_code = '00444'" in refined
    assert "zip_code = 444" not in refined

def test_transform_real_world_car_registrations():
    sql = """
    SELECT COUNT(DISTINCT id)
    FROM registered_cars
    WHERE car_type LIKE '%italian%'
      AND (place LIKE '%17%' OR place LIKE '%52%' OR place LIKE '%444%')
      AND manufacturer = 'sonic'
    GROUP BY place
    """
    
    plan = TransformationPlan(
        enrichment_details=[
            FilterTransformation(table="registered_cars", 
                column="car_type",
                original_value="italian",
                old_operator="LIKE",
                new_operator="LIKE",
                refined_values=["italian"],
                changed_filter=False,
                reasoning="LIKE '%italian%' already captures all relevant Italian car types."
            ),
            FilterTransformation(table="registered_cars", 
                column="place",
                original_value="52",
                old_operator="LIKE",
                new_operator="=",
                refined_values=["st 52"],
                changed_filter=True,
                reasoning="LIKE '%52%' catches irrelevant values. 'st 52' is the only relevant store."
            ),
            FilterTransformation(table="registered_cars", 
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
    
    refined = SQLTransformer.apply(sql, plan)
    
    assert "car_type LIKE '%italian%'" in refined
    assert "place LIKE '%17%'" in refined
    assert "place LIKE '%444%'" in refined
    assert "place = 'st 52'" in refined
    assert "'%52%'" not in refined
    assert "manufacturer IN ('sonic blue', 'sonic black')" in refined
    assert "= 'sonic'" not in refined
    assert "SELECT COUNT(DISTINCT id)" in refined
    assert "GROUP BY place" in refined

import pytest
from agent.services.filter_extractor import FilterExtractor

def test_extract_simple():
    sql = "SELECT * FROM dataverse.orders WHERE order_status = 'F'"
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "order_status": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 1
    f = filters[0]
    assert f.source_table == "dataverse.orders"
    assert f.source_column == "order_status"
    assert f.operator == "="
    assert f.value == "F"
    assert f.is_unnest is False
    assert f.match_type == "exact"

def test_extract_cte_alias():
    sql = """
    WITH cte AS (
        SELECT order_status AS status, order_notes 
        FROM dataverse.orders
    ) 
    SELECT * FROM cte 
    WHERE status LIKE 'active%'
    """
    schema = {
        "dataverse.orders": {
            "order_status": "string",
            "order_notes": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 1
    f = filters[0]
    assert f.source_table == "dataverse.orders"
    assert f.source_column == "order_status"
    assert f.operator == "LIKE"
    assert f.value == "active%"
    assert f.is_unnest is False
    assert f.match_type == "prefix"

def test_extract_unnest():
    sql = """
    SELECT * 
    FROM dataverse.orders 
    CROSS JOIN UNNEST(orders.order_notes) AS t (note) 
    WHERE note = 'urgent'
    """
    schema = {
        "dataverse.orders": {
            "order_notes": "array<string>"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 1
    f = filters[0]
    assert f.source_table == "dataverse.orders"
    assert f.source_column == "order_notes"
    assert f.operator == "="
    assert f.value == "urgent"
    assert f.is_unnest is True
    assert f.match_type == "exact"

def test_extract_between_and_in():
    sql = "SELECT * FROM dataverse.orders WHERE order_id BETWEEN 10 AND 20 AND order_status IN ('F', 'O')"
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "order_status": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 2
    
    f_between = next(x for x in filters if x.operator == "BETWEEN")
    assert f_between.value == [10, 20]
    assert f_between.match_type == "range"
    
    f_in = next(x for x in filters if x.operator == "IN")
    assert f_in.value == ["F", "O"]
    assert f_in.match_type == "in_list"


def test_extract_join():
    sql = """
    SELECT o.order_id, c.customer_name 
    FROM dataverse.orders o
    JOIN dataverse.customers c ON o.customer_id = c.id
    WHERE o.order_status = 'F' AND c.region = 'US'
    """
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "customer_id": "int",
            "order_status": "string"
        },
        "dataverse.customers": {
            "id": "int",
            "customer_name": "string",
            "region": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 2
    
    f_order = next(x for x in filters if x.source_table == "dataverse.orders")
    assert f_order.source_column == "order_status"
    assert f_order.operator == "="
    assert f_order.value == "F"
    
    f_customer = next(x for x in filters if x.source_table == "dataverse.customers")
    assert f_customer.source_column == "region"
    assert f_customer.operator == "="
    assert f_customer.value == "US"


def test_extract_is_null():
    sql = "SELECT * FROM dataverse.orders WHERE order_notes IS NULL"
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "order_notes": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 1
    f = filters[0]
    assert f.source_table == "dataverse.orders"
    assert f.source_column == "order_notes"
    assert f.operator.upper() == "IS NULL"
    assert f.value is None


def test_extract_inequality():
    sql = "SELECT * FROM dataverse.orders WHERE total_amount >= 150.50"
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "total_amount": "float"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 1
    f = filters[0]
    assert f.source_table == "dataverse.orders"
    assert f.source_column == "total_amount"
    assert f.operator == ">="
    assert f.value == 150.50
    assert f.match_type in ["range", "inequality"]


def test_extract_no_filters(caplog):
    sql = "SELECT order_id, order_status FROM dataverse.orders LIMIT 100"
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "order_status": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert isinstance(filters, list)
    assert len(filters) == 0
    assert not any(r.levelname == 'ERROR' for r in caplog.records)


def test_extract_ignore_column_to_column(caplog):
    sql = """
    SELECT * FROM dataverse.orders o
    JOIN dataverse.customers c ON o.customer_id = c.id
    WHERE o.order_status = c.status_preference
    """
    schema = {
        "dataverse.orders": {
            "order_id": "int",
            "customer_id": "int",
            "order_status": "string"
        },
        "dataverse.customers": {
            "id": "int",
            "status_preference": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert isinstance(filters, list)
    assert len(filters) == 0
    assert not any(r.levelname == 'ERROR' for r in caplog.records)


def test_extract_nested_and_or():
    sql = """
    SELECT * FROM dataverse.orders 
    WHERE (order_status = 'F' OR order_status = 'P') 
      AND total_amount > 1000
    """
    schema = {
        "dataverse.orders": {
            "order_status": "string",
            "total_amount": "float"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 3
    
    statuses = [f.value for f in filters if f.source_column == "order_status"]
    assert "F" in statuses
    assert "P" in statuses
    
    amount_filter = next(f for f in filters if f.source_column == "total_amount")
    assert amount_filter.operator == ">"
    assert amount_filter.value == 1000

def test_extract_missing_schema():
    sql = "SELECT * FROM dataverse.unknown_table WHERE mystery_column = 'X'"
    schema = {
        "dataverse.orders": {"order_id": "int"}
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 1
    f = filters[0]
    assert f.source_table == "dataverse.unknown_table"
    assert f.source_column == "mystery_column"
    assert f.operator == "="
    assert f.value == "X"


def test_extract_monster_nested_query():
    sql = """
    WITH active_customers AS (
        SELECT id AS cust_id, region, status
        FROM dataverse.customers 
        WHERE status = 'ACTIVE'
    ),
    orders_with_tags AS (
        SELECT o.order_id, o.customer_id, o.amount, tag
        FROM dataverse.orders o
        CROSS JOIN UNNEST(o.tags) AS t(tag)
        WHERE o.amount BETWEEN 100 AND 5000
    )
    SELECT owt.order_id, ac.region, owt.tag, d.delivery_status
    FROM orders_with_tags owt
    JOIN active_customers ac ON owt.customer_id = ac.cust_id
    LEFT JOIN dataverse.deliveries d ON owt.order_id = d.order_id
    WHERE (owt.amount > 1000 OR ac.region IN ('US', 'CA'))
      AND (owt.tag LIKE 'urgent%' OR (d.delivery_status = 'DELAYED' AND d.courier != 'DHL'))
    """
    
    schema = {
        "dataverse.customers": {
            "id": "int",
            "region": "string",
            "status": "string"
        },
        "dataverse.orders": {
            "order_id": "int",
            "customer_id": "int",
            "amount": "float",
            "tags": "array<string>"
        },
        "dataverse.deliveries": {
            "delivery_id": "int",
            "order_id": "int",
            "delivery_status": "string",
            "courier": "string"
        }
    }

    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 7
    
    f_status = next(x for x in filters if x.source_column == "status" and x.operator == "=")
    assert f_status.source_table == "dataverse.customers"
    assert f_status.value == "ACTIVE"
    
    f_amount_between = next(x for x in filters if x.operator == "BETWEEN")
    assert f_amount_between.source_table == "dataverse.orders"
    assert f_amount_between.source_column == "amount"
    assert f_amount_between.value == [100, 5000]
    
    f_amount_gt = next(x for x in filters if x.operator == ">")
    assert f_amount_gt.source_table == "dataverse.orders"
    assert f_amount_gt.source_column == "amount"
    assert f_amount_gt.value == 1000
    
    f_region = next(x for x in filters if x.source_column == "region")
    assert f_region.source_table == "dataverse.customers"
    assert f_region.operator == "IN"
    assert f_region.value == ["US", "CA"]
    
    f_tag = next(x for x in filters if x.operator == "LIKE")
    assert f_tag.source_table == "dataverse.orders"
    assert f_tag.source_column == "tags"
    assert f_tag.value == "urgent%"
    assert f_tag.is_unnest is True
    
    f_delivery = next(x for x in filters if x.source_column == "delivery_status")
    assert f_delivery.source_table == "dataverse.deliveries"
    assert f_delivery.operator == "="
    assert f_delivery.value == "DELAYED"
    
    f_courier = next(x for x in filters if x.source_column == "courier")
    assert f_courier.source_table == "dataverse.deliveries"
    assert f_courier.operator == "!="
    assert f_courier.value == "DHL"


def test_extract_real_world_car_registrations():
    sql = """
    SELECT COUNT(DISTINCT id)
    FROM registered_cars
    WHERE car_type LIKE '%italian%'
      AND (place LIKE '%17%' OR place LIKE '%52%' OR place LIKE '%444%')
      AND manufacturer = 'sonic'
    GROUP BY place
    """
    
    schema = {
        "registered_cars": {
            "id": "int",
            "car_type": "string",
            "place": "string",
            "manufacturer": "string"
        }
    }
    
    filters = FilterExtractor.extract(sql, schema)
    assert len(filters) == 5
    
    f_car = next(x for x in filters if x.source_column == "car_type")
    assert f_car.operator == "LIKE"
    assert f_car.value == "%italian%"
    assert f_car.match_type == "substring"
    
    places = [x for x in filters if x.source_column == "place"]
    assert len(places) == 3
    assert all(p.operator == "LIKE" for p in places)
    assert all(p.match_type == "substring" for p in places)
    
    place_values = [p.value for p in places]
    assert "%17%" in place_values
    assert "%52%" in place_values
    assert "%444%" in place_values
    
    f_manuf = next(x for x in filters if x.source_column == "manufacturer")
    assert f_manuf.operator == "="
    assert f_manuf.value == "sonic"
    assert f_manuf.match_type == "exact"

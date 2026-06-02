import urllib.request
import json
from sqlmodel import Session, select

from core.db.engine import create_db_and_tables, engine
from core.models.models import (
    DifficultyLevel,
    EnrichmentVersion,
    EvalResult,
    EvalRun,
    EvalStatus,
    GoldenQuestion,
    SecurityUser,
    Table,
    TableStatus,
    UserScope,
)

from app.config import settings

def get_embedding(text: str) -> list[float]:
    url = settings.EMBEDDER_URL
    data = json.dumps({"model": settings.EMBEDDER_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode())["embedding"]
    except Exception as e:
        print(f"Error getting embedding: {e}")
        return [0.0] * 768

def seed():
    # Run migrations to ensure all schemas and tables exist
    create_db_and_tables()

    with Session(engine) as session:
        # Check if already seeded
        if session.exec(select(Table)).first():
            return

        # 0. Security Users (Admins)
        admin1 = SecurityUser(
            email="admin@company.com",
            name="System Admin",
            is_active=True,
            is_admin=True,
        )
        session.add(admin1)

        # 1. Scopes
        scope1 = UserScope(user_id="user-1", name="Finance Tables", is_active=True)
        scope2 = UserScope(user_id="user-1", name="Marketing Tables", is_active=False)
        session.add_all([scope1, scope2])

        # 2. Tables — all backed by the minio (Iceberg) catalog
        # simple_retail schema
        t1 = Table(
            name="orders",
            schema_name="simple_retail",
            status=TableStatus.production,
            oasis_source_id="1",
            catalog="minio",
            service="local_trino",
            owner_id="user-1",
        )
        # complex_retail schema
        t2 = Table(
            name="customers",
            schema_name="complex_retail",
            status=TableStatus.production,
            oasis_source_id="2",
            catalog="minio",
            service="local_trino",
            owner_id="user-1",
        )
        t3 = Table(
            name="order_items",
            schema_name="complex_retail",
            status=TableStatus.sandbox,
            oasis_source_id="3",
            catalog="minio",
            service="local_trino",
            owner_id="user-2",
        )
        t4 = Table(
            name="products",
            schema_name="complex_retail",
            status=TableStatus.production,
            oasis_source_id="4",
            catalog="minio",
            service="local_trino",
            owner_id="user-1",
        )
        t5 = Table(
            name="orders",
            schema_name="complex_retail",
            status=TableStatus.production,
            oasis_source_id="5",
            catalog="minio",
            service="local_trino",
            owner_id="user-1",
        )

        session.add_all([t1, t2, t3, t4, t5])
        session.flush()

        # 3. Enrichment
        e1 = EnrichmentVersion(
            table_id=t1.id,
            version=1,
            data={
                "table_description": (
                    "Flat orders table for the simple_retail schema. "
                    "Ideal for single-table queries."
                ),
                "columns": [
                    {"name": "order_id", "description": "Unique order identifier", "dataType": "VARCHAR"},
                    {"name": "customer_name", "description": "Full name of the customer", "dataType": "VARCHAR"},
                    {"name": "customer_email", "description": "Customer email address", "dataType": "VARCHAR"},
                    {"name": "product_name", "description": "Name of the product ordered", "dataType": "VARCHAR"},
                    {"name": "quantity", "description": "Number of units ordered", "dataType": "INT"},
                    {"name": "unit_price", "description": "Price per unit (USD)", "dataType": "DOUBLE"},
                    {"name": "total_amount", "description": "Total order value (USD)", "dataType": "DOUBLE"},
                    {"name": "status", "description": "Order status", "dataType": "VARCHAR"},
                    {"name": "order_date", "description": "Date the order was placed", "dataType": "DATE"},
                ],
            },
        )
        e2 = EnrichmentVersion(
            table_id=t2.id,
            version=1,
            data={
                "table_description": "Customer master table in the complex_retail schema.",
                "columns": [
                    {"name": "customer_id", "description": "Unique customer ID", "dataType": "VARCHAR"},
                    {"name": "first_name", "description": "Customer first name", "dataType": "VARCHAR"},
                    {"name": "last_name", "description": "Customer last name", "dataType": "VARCHAR"},
                    {"name": "email", "description": "Customer email", "dataType": "VARCHAR"},
                    {"name": "country", "description": "Country", "dataType": "VARCHAR"},
                    {"name": "city", "description": "City", "dataType": "VARCHAR"},
                    {"name": "created_at", "description": "Account creation timestamp", "dataType": "TIMESTAMP"},
                ],
            },
        )
        e3 = EnrichmentVersion(
            table_id=t4.id,
            version=1,
            data={
                "table_description": "Products master table in the complex_retail schema.",
                "columns": [
                    {"name": "product_id", "description": "Unique product ID", "dataType": "VARCHAR"},
                    {"name": "name", "description": "Product name", "dataType": "VARCHAR"},
                    {"name": "category", "description": "Category", "dataType": "VARCHAR"},
                    {"name": "subcategory", "description": "Sub-category", "dataType": "VARCHAR"},
                    {"name": "price", "description": "List price (USD)", "dataType": "DOUBLE"},
                    {"name": "stock_quantity", "description": "Units in stock", "dataType": "INT"},
                ]
            }
        )
        e4 = EnrichmentVersion(
            table_id=t5.id,
            version=1,
            data={
                "table_description": "Orders master table in the complex_retail schema.",
                "columns": [
                    {"name": "order_id", "description": "Unique order ID", "dataType": "VARCHAR"},
                    {"name": "customer_id", "description": "FK to customers.customer_id", "dataType": "VARCHAR"},
                    {"name": "order_date", "description": "Date placed", "dataType": "DATE"},
                    {"name": "status", "description": "Order status", "dataType": "VARCHAR"},
                    {"name": "total_amount", "description": "Total value (USD)", "dataType": "DOUBLE"},
                    {"name": "shipping_address", "description": "Delivery address", "dataType": "VARCHAR"},
                ]
            }
        )
        e5 = EnrichmentVersion(
            table_id=t3.id,
            version=1,
            data={
                "table_description": "Order items details table in complex_retail schema.",
                "columns": [
                    {"name": "item_id", "description": "Unique item ID", "dataType": "VARCHAR"},
                    {"name": "order_id", "description": "FK to orders.order_id", "dataType": "VARCHAR"},
                    {"name": "product_id", "description": "FK to products.product_id", "dataType": "VARCHAR"},
                    {"name": "quantity", "description": "Quantity ordered", "dataType": "INT"},
                    {"name": "unit_price", "description": "Unit price (USD)", "dataType": "DOUBLE"},
                    {"name": "discount_pct", "description": "Discount percent", "dataType": "DOUBLE"},
                ]
            }
        )
        session.add_all([e1, e2, e3, e4, e5])
        session.flush()

        # Generate and save embeddings for each table
        for table, enrichment in [(t1, e1), (t2, e2), (t3, e5), (t4, e3), (t5, e4)]:
            desc = enrichment.data.get("table_description", "")
            cols = ", ".join([c["name"] for c in enrichment.data.get("columns", [])])
            embed_text = f"Table: {table.schema_name}.{table.name}. Description: {desc}. Columns: {cols}"
            table.embedding = get_embedding(embed_text)
            session.add(table)

        # 4. Golden Questions
        # Table 1: simple_retail.orders
        q1 = GoldenQuestion(
            table_id=t1.id,
            question="How many orders were placed in total?",
            expected_sql="SELECT count(*) FROM minio.simple_retail.orders;",
            difficulty=DifficultyLevel.simple,
        )
        q2 = GoldenQuestion(
            table_id=t1.id,
            question="What is the total revenue across all orders?",
            expected_sql="SELECT sum(total_amount) FROM minio.simple_retail.orders;",
            difficulty=DifficultyLevel.simple,
        )
        q3 = GoldenQuestion(
            table_id=t1.id,
            question="What is the average order value?",
            expected_sql="SELECT avg(total_amount) FROM minio.simple_retail.orders;",
            difficulty=DifficultyLevel.simple,
        )

        # Table 2: complex_retail.customers
        qc1 = GoldenQuestion(
            table_id=t2.id,
            question="How many customers are registered in the system?",
            expected_sql="SELECT count(*) FROM minio.complex_retail.customers;",
            difficulty=DifficultyLevel.simple,
        )
        qc2 = GoldenQuestion(
            table_id=t2.id,
            question="List all customers from Israel.",
            expected_sql="SELECT first_name, last_name FROM minio.complex_retail.customers WHERE country = 'Israel';",
            difficulty=DifficultyLevel.medium,
        )
        qc3 = GoldenQuestion(
            table_id=t2.id,
            question="Which cities have registered customers?",
            expected_sql="SELECT DISTINCT city FROM minio.complex_retail.customers ORDER BY city;",
            difficulty=DifficultyLevel.simple,
        )

        # Table 3: complex_retail.order_items
        ql1 = GoldenQuestion(
            table_id=t3.id,
            question="How many line items have been ordered in total?",
            expected_sql="SELECT count(*) FROM minio.complex_retail.order_items;",
            difficulty=DifficultyLevel.simple,
        )
        ql2 = GoldenQuestion(
            table_id=t3.id,
            question="What is the average quantity per line item?",
            expected_sql="SELECT avg(quantity) FROM minio.complex_retail.order_items;",
            difficulty=DifficultyLevel.simple,
        )
        ql3 = GoldenQuestion(
            table_id=t3.id,
            question="What is the total quantity ordered per product?",
            expected_sql="SELECT product_id, sum(quantity) as total_qty FROM minio.complex_retail.order_items GROUP BY product_id ORDER BY total_qty DESC;",
            difficulty=DifficultyLevel.medium,
        )

        session.add_all([q1, q2, q3, qc1, qc2, qc3, ql1, ql2, ql3])
        session.flush()

        # 5. Eval Runs
        run1 = EvalRun(table_id=t1.id, score=1.0, status=EvalStatus.completed)
        session.add(run1)
        session.flush()

        # 6. Eval Results
        res1 = EvalResult(run_id=run1.id, question_id=q1.id, score=1.0, status="pass")
        res2 = EvalResult(run_id=run1.id, question_id=q2.id, score=1.0, status="pass")
        res3 = EvalResult(run_id=run1.id, question_id=q3.id, score=1.0, status="pass")
        session.add_all([res1, res2, res3])

        session.commit()


if __name__ == "__main__":
    seed()

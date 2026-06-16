"""
infra_init.py — Idempotent infrastructure initialization.

Runs inside the backend container at startup (before uvicorn) to ensure
the entire data layer is self-healing after `docker compose down/up`:

  1. MinIO  — create 'warehouse' bucket if missing
  2. Trino  — create minio catalog schemas & tables (IF NOT EXISTS)
  3. Trino  — seed sample rows (only when table is empty)
  4. OpenMetadata — register local_trino service / database / schemas / tables

All steps are fully idempotent. Running this multiple times is safe.
"""

import base64
import logging
import time
from typing import Any

import trino
import trino.dbapi
from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

import os
_MINIO_HOST = os.getenv("MINIO_HOST", "localhost:9000")
_MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
_MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "password123")
_WAREHOUSE_BUCKET = "warehouse"

_TRINO_HOST = os.getenv("TRINO_HOST", "localhost")
_TRINO_PORT = int(os.getenv("TRINO_PORT", "8080"))
_TRINO_USER = os.getenv("TRINO_USER", "trino")


_OM_URL = os.getenv("OPENMETADATA_URL", "http://localhost:8585")
_OM_SERVICE_NAME = "local_trino"

_TRINO_READY_RETRIES = 20
_TRINO_READY_INTERVAL = 5  # seconds between retries


# ─────────────────────────────────────────────────────────────────────────────
# DDL — Schemas, Tables, Seed data
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMAS = [
    {
        "name": "simple_retail",
        "sql": (
            "CREATE SCHEMA IF NOT EXISTS minio.simple_retail "
            "WITH (location = 's3://warehouse/simple_retail/')"
        ),
    },
    {
        "name": "complex_retail",
        "sql": (
            "CREATE SCHEMA IF NOT EXISTS minio.complex_retail "
            "WITH (location = 's3://warehouse/complex_retail/')"
        ),
    },
]

_TABLES: list[dict[str, Any]] = [
    # ── simple_retail ──────────────────────────────────────────────────────
    {
        "fqn": "minio.simple_retail.orders",
        "schema": "simple_retail",
        "name": "orders",
        "create_sql": """CREATE TABLE IF NOT EXISTS minio.simple_retail.orders (
  order_id       VARCHAR,
  customer_name  VARCHAR,
  customer_email VARCHAR,
  product_name   VARCHAR,
  quantity       INTEGER,
  unit_price     DOUBLE,
  total_amount   DOUBLE,
  status         VARCHAR,
  order_date     DATE
) WITH (format = 'PARQUET', location = 's3://warehouse/simple_retail/orders/')""",
        "count_sql": "SELECT count(*) FROM minio.simple_retail.orders",
        "seed_sql": """INSERT INTO minio.simple_retail.orders VALUES
  ('ORD-001','Alice Cohen','alice@example.com','Laptop',1,1200.0,1200.0,'delivered',DATE '2024-01-10'),
  ('ORD-002','Bob Levi','bob@example.com','Mouse',2,25.0,50.0,'shipped',DATE '2024-01-12'),
  ('ORD-003','Carol Mizrahi','carol@example.com','Keyboard',1,75.0,75.0,'pending',DATE '2024-01-14'),
  ('ORD-004','Dan Shapiro','dan@example.com','Monitor',2,350.0,700.0,'delivered',DATE '2024-01-15'),
  ('ORD-005','Eve Katz','eve@example.com','Headphones',1,90.0,90.0,'cancelled',DATE '2024-01-16'),
  ('ORD-006','Frank Green','frank@example.com','Laptop',1,1200.0,1200.0,'delivered',DATE '2024-01-18'),
  ('ORD-007','Grace Hopper','grace@example.com','Smartphone',1,800.0,800.0,'shipped',DATE '2024-01-20'),
  ('ORD-008','Henry Ford','henry@example.com','Tablet',2,400.0,800.0,'delivered',DATE '2024-01-22'),
  ('ORD-009','Ivy League','ivy@example.com','Charger',3,30.0,90.0,'delivered',DATE '2024-01-25'),
  ('ORD-010','Jack Sparrow','jack@example.com','Smart Watch',1,250.0,250.0,'pending',DATE '2024-01-28'),
  ('ORD-011','Kate Middleton','kate@example.com','Headphones',1,90.0,90.0,'delivered',DATE '2024-02-01'),
  ('ORD-012','Leo Tolstoy','leo@example.com','Mouse',2,25.0,50.0,'delivered',DATE '2024-02-03'),
  ('ORD-013','Mary Shelley','mary@example.com','Keyboard',1,75.0,75.0,'shipped',DATE '2024-02-05'),
  ('ORD-014','Nathan Drake','nathan@example.com','Monitor',1,350.0,350.0,'cancelled',DATE '2024-02-08'),
  ('ORD-015','Olivia Wilde','olivia@example.com','Office Chair',1,200.0,200.0,'delivered',DATE '2024-02-10'),
  ('ORD-016','Peter Parker','peter@example.com','Desk Lamp',2,45.0,90.0,'delivered',DATE '2024-02-12'),
  ('ORD-017','Quentin Tarantino','quentin@example.com','Laptop',1,1200.0,1200.0,'pending',DATE '2024-02-15'),
  ('ORD-018','Rachel Green','rachel@example.com','Smartphone',1,800.0,800.0,'delivered',DATE '2024-02-18'),
  ('ORD-019','Steve Jobs','steve@example.com','Tablet',1,400.0,400.0,'delivered',DATE '2024-02-20'),
  ('ORD-020','Tina Turner','tina@example.com','Smart Watch',2,250.0,500.0,'shipped',DATE '2024-02-22'),
  ('ORD-021','Uma Thurman','uma@example.com','Charger',1,30.0,30.0,'delivered',DATE '2024-02-25'),
  ('ORD-022','Victor Hugo','victor@example.com','Headphones',2,90.0,180.0,'delivered',DATE '2024-03-01'),
  ('ORD-023','Wendy Darling','wendy@example.com','Mouse',4,25.0,100.0,'pending',DATE '2024-03-03'),
  ('ORD-024','Xavier Charles','xavier@example.com','Keyboard',1,75.0,75.0,'delivered',DATE '2024-03-05'),
  ('ORD-025','Yasmine Bleeth','yasmine@example.com','Monitor',2,350.0,700.0,'delivered',DATE '2024-03-08'),
  ('ORD-026','Zachary Taylor','zach@example.com','Office Chair',1,200.0,200.0,'delivered',DATE '2024-03-10'),
  ('ORD-027','Alice Cohen','alice@example.com','Desk Lamp',1,45.0,45.0,'delivered',DATE '2024-03-12'),
  ('ORD-028','Bob Levi','bob@example.com','Laptop',1,1200.0,1200.0,'shipped',DATE '2024-03-15'),
  ('ORD-029','Carol Mizrahi','carol@example.com','Smartphone',1,800.0,800.0,'delivered',DATE '2024-03-18'),
  ('ORD-030','Dan Shapiro','dan@example.com','Tablet',1,400.0,400.0,'delivered',DATE '2024-03-20')""",
    },
    # ── complex_retail ─────────────────────────────────────────────────────
    {
        "fqn": "minio.complex_retail.customers",
        "schema": "complex_retail",
        "name": "customers",
        "create_sql": """CREATE TABLE IF NOT EXISTS minio.complex_retail.customers (
  customer_id VARCHAR,
  first_name  VARCHAR,
  last_name   VARCHAR,
  email       VARCHAR,
  country     VARCHAR,
  city        VARCHAR,
  created_at  TIMESTAMP
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/customers/')""",
        "count_sql": "SELECT count(*) FROM minio.complex_retail.customers",
        "seed_sql": """INSERT INTO minio.complex_retail.customers VALUES
  ('C01','Alice','Cohen','alice@example.com','Israel','Tel Aviv',TIMESTAMP '2023-06-01 10:00:00'),
  ('C02','Bob','Levi','bob@example.com','Israel','Jerusalem',TIMESTAMP '2023-06-05 11:00:00'),
  ('C03','Carol','Mizrahi','carol@example.com','Israel','Haifa',TIMESTAMP '2023-07-01 09:00:00'),
  ('C04','Dan','Shapiro','dan@example.com','Israel','Tel Aviv',TIMESTAMP '2023-07-15 14:30:00'),
  ('C05','Eve','Katz','eve@example.com','Israel','Eilat',TIMESTAMP '2023-08-01 16:00:00'),
  ('C06','Frank','Green','frank@example.com','USA','New York',TIMESTAMP '2023-08-10 08:15:00'),
  ('C07','Grace','Hopper','grace@example.com','USA','San Francisco',TIMESTAMP '2023-08-20 12:45:00'),
  ('C08','Henry','Ford','henry@example.com','USA','Detroit',TIMESTAMP '2023-09-01 10:30:00'),
  ('C09','Ivy','League','ivy@example.com','UK','London',TIMESTAMP '2023-09-12 11:00:00'),
  ('C10','Jack','Sparrow','jack@example.com','UK','Bristol',TIMESTAMP '2023-09-25 15:20:00'),
  ('C11','Kate','Middleton','kate@example.com','UK','London',TIMESTAMP '2023-10-01 09:00:00'),
  ('C12','Leo','Tolstoy','leo@example.com','Russia','Moscow',TIMESTAMP '2023-10-10 14:00:00'),
  ('C13','Mary','Shelley','mary@example.com','UK','London',TIMESTAMP '2023-10-22 17:30:00'),
  ('C14','Nathan','Drake','nathan@example.com','USA','Boston',TIMESTAMP '2023-11-05 11:15:00'),
  ('C15','Olivia','Wilde','olivia@example.com','USA','Los Angeles',TIMESTAMP '2023-11-12 13:00:00'),
  ('C16','Peter','Parker','peter@example.com','USA','New York',TIMESTAMP '2023-11-18 16:45:00'),
  ('C17','Quentin','Tarantino','quentin@example.com','USA','Los Angeles',TIMESTAMP '2023-11-28 10:00:00'),
  ('C18','Rachel','Green','rachel@example.com','USA','New York',TIMESTAMP '2023-12-05 12:00:00'),
  ('C19','Steve','Jobs','steve@example.com','USA','San Jose',TIMESTAMP '2023-12-12 09:30:00'),
  ('C20','Tina','Turner','tina@example.com','France','Paris',TIMESTAMP '2023-12-18 14:00:00'),
  ('C21','Uma','Thurman','uma@example.com','France','Nice',TIMESTAMP '2023-12-25 11:30:00'),
  ('C22','Victor','Hugo','victor@example.com','France','Paris',TIMESTAMP '2024-01-02 10:00:00'),
  ('C23','Wendy','Darling','wendy@example.com','Canada','Toronto',TIMESTAMP '2024-01-10 15:45:00'),
  ('C24','Xavier','Charles','xavier@example.com','Canada','Vancouver',TIMESTAMP '2024-01-15 09:00:00'),
  ('C25','Yasmine','Bleeth','yasmine@example.com','USA','Miami',TIMESTAMP '2024-01-22 13:15:00')""",
    },
    {
        "fqn": "minio.complex_retail.products",
        "schema": "complex_retail",
        "name": "products",
        "create_sql": """CREATE TABLE IF NOT EXISTS minio.complex_retail.products (
  product_id     VARCHAR,
  name           VARCHAR,
  category       VARCHAR,
  subcategory    VARCHAR,
  price          DOUBLE,
  stock_quantity INTEGER
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/products/')""",
        "count_sql": "SELECT count(*) FROM minio.complex_retail.products",
        "seed_sql": """INSERT INTO minio.complex_retail.products VALUES
  ('P01','Laptop','Electronics','Computers',1200.0,50),
  ('P02','Mouse','Electronics','Peripherals',25.0,200),
  ('P03','Keyboard','Electronics','Peripherals',75.0,150),
  ('P04','Monitor','Electronics','Displays',350.0,80),
  ('P05','Smartphone','Electronics','Phones',800.0,100),
  ('P06','Tablet','Electronics','Computers',400.0,120),
  ('P07','Smart Watch','Electronics','Wearables',250.0,90),
  ('P08','Headphones','Electronics','Audio',90.0,150),
  ('P09','Charger','Electronics','Accessories',30.0,300),
  ('P10','Desk Lamp','Furniture','Lighting',45.0,60),
  ('P11','Office Chair','Furniture','Seating',200.0,40),
  ('P12','Standing Desk','Furniture','Tables',600.0,25),
  ('P13','Notebook','Office Supplies','Paper',5.0,500),
  ('P14','Gel Pens Pack','Office Supplies','Writing',12.0,400),
  ('P15','Backpack','Office Supplies','Bags',80.0,100)""",
    },
    {
        "fqn": "minio.complex_retail.orders",
        "schema": "complex_retail",
        "name": "orders",
        "create_sql": """CREATE TABLE IF NOT EXISTS minio.complex_retail.orders (
  order_id         VARCHAR,
  customer_id      VARCHAR,
  order_date       DATE,
  status           VARCHAR,
  total_amount     DOUBLE,
  shipping_address VARCHAR
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/orders/')""",
        "count_sql": "SELECT count(*) FROM minio.complex_retail.orders",
        "seed_sql": """INSERT INTO minio.complex_retail.orders VALUES
  ('O01','C01',DATE '2024-01-10','delivered',120.0,'Tel Aviv, 1 Main St'),
  ('O02','C01',DATE '2024-01-11','delivered',80.0,'Tel Aviv, 1 Main St'),
  ('O03','C01',DATE '2024-01-12','delivered',150.0,'Tel Aviv, 1 Main St'),
  ('O04','C01',DATE '2024-01-13','delivered',200.0,'Tel Aviv, 1 Main St'),
  ('O05','C01',DATE '2024-01-14','delivered',95.0,'Tel Aviv, 1 Main St'),
  ('O06','C01',DATE '2024-01-15','delivered',300.0,'Tel Aviv, 1 Main St'),
  ('O07','C01',DATE '2024-01-16','delivered',110.0,'Tel Aviv, 1 Main St'),
  ('O08','C01',DATE '2024-01-17','delivered',45.0,'Tel Aviv, 1 Main St'),
  ('O09','C01',DATE '2024-01-18','delivered',160.0,'Tel Aviv, 1 Main St'),
  ('O10','C01',DATE '2024-01-19','delivered',220.0,'Tel Aviv, 1 Main St'),
  ('O11','C02',DATE '2024-02-01','delivered',150.0,'Jerusalem, 5 King St'),
  ('O12','C02',DATE '2024-02-02','delivered',70.0,'Jerusalem, 5 King St'),
  ('O13','C02',DATE '2024-02-03','delivered',120.0,'Jerusalem, 5 King St'),
  ('O14','C02',DATE '2024-02-04','delivered',85.0,'Jerusalem, 5 King St'),
  ('O15','C02',DATE '2024-02-05','delivered',90.0,'Jerusalem, 5 King St'),
  ('O16','C02',DATE '2024-02-06','delivered',210.0,'Jerusalem, 5 King St'),
  ('O17','C03',DATE '2024-02-10','pending',350.0,'Haifa, 3 Port Rd'),
  ('O18','C03',DATE '2024-02-11','delivered',100.0,'Haifa, 3 Port Rd'),
  ('O19','C04',DATE '2024-02-12','delivered',1600.0,'Tel Aviv, 10 Rothschild Blvd'),
  ('O20','C04',DATE '2024-02-13','delivered',400.0,'Tel Aviv, 10 Rothschild Blvd'),
  ('O21','C06',DATE '2024-02-14','delivered',1280.0,'New York, 5th Ave 100'),
  ('O22','C06',DATE '2024-02-15','delivered',50.0,'New York, 5th Ave 100'),
  ('O23','C07',DATE '2024-02-16','shipped',800.0,'San Francisco, Market St 200'),
  ('O24','C07',DATE '2024-02-17','delivered',90.0,'San Francisco, Market St 200'),
  ('O25','C08',DATE '2024-02-18','delivered',800.0,'Detroit, Woodward Ave 300'),
  ('O26','C08',DATE '2024-02-19','delivered',120.0,'Detroit, Woodward Ave 300'),
  ('O27','C09',DATE '2024-02-20','delivered',90.0,'London, Baker St 221B'),
  ('O28','C11',DATE '2024-02-21','delivered',90.0,'London, 10 Downing St'),
  ('O29','C12',DATE '2024-02-22','delivered',50.0,'Moscow, Red Square 1'),
  ('O30','C13',DATE '2024-02-23','shipped',75.0,'London, Abbey Rd 3'),
  ('O31','C14',DATE '2024-02-24','cancelled',350.0,'Boston, Beacon St 45'),
  ('O32','C16',DATE '2024-02-25','delivered',90.0,'New York, Broadway 1000'),
  ('O33','C17',DATE '2024-02-26','pending',1200.0,'Los Angeles, Hollywood Blvd 700'),
  ('O34','C18',DATE '2024-02-27','delivered',800.0,'New York, Central Park West 50'),
  ('O35','C19',DATE '2024-02-28','delivered',400.0,'San Jose, Infinite Loop 1'),
  ('O36','C21',DATE '2024-03-01','delivered',30.0,'Nice, Promenade des Anglais 5'),
  ('O37','C22',DATE '2024-03-02','delivered',180.0,'Paris, Rue de Rivoli 20'),
  ('O38','C23',DATE '2024-03-03','pending',100.0,'Toronto, Yonge St 100'),
  ('O39','C24',DATE '2024-03-04','delivered',75.0,'Vancouver, Georgia St 50'),
  ('O40','C09',DATE '2024-03-05','delivered',60.0,'London, Baker St 221B')""",
    },
    {
        "fqn": "minio.complex_retail.order_items",
        "schema": "complex_retail",
        "name": "order_items",
        "create_sql": """CREATE TABLE IF NOT EXISTS minio.complex_retail.order_items (
  item_id      VARCHAR,
  order_id     VARCHAR,
  product_id   VARCHAR,
  quantity     INTEGER,
  unit_price   DOUBLE,
  discount_pct DOUBLE
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/order_items/')""",
        "count_sql": "SELECT count(*) FROM minio.complex_retail.order_items",
        "seed_sql": """INSERT INTO minio.complex_retail.order_items VALUES
  ('I01','O01','P01',1,1200.0,0.0),
  ('I02','O01','P02',2,25.0,0.0),
  ('I03','O02','P02',2,25.0,0.0),
  ('I04','O03','P04',1,350.0,0.0),
  ('I05','O04','P05',2,800.0,0.0),
  ('I06','O05','P07',1,250.0,0.0),
  ('I07','O06','P01',1,1200.0,0.0),
  ('I08','O06','P15',1,80.0,0.0),
  ('I09','O07','P05',1,800.0,0.0),
  ('I10','O08','P06',2,400.0,0.0),
  ('I11','O09','P09',3,30.0,0.0),
  ('I12','O10','P07',1,250.0,0.0),
  ('I13','O11','P08',1,90.0,0.0),
  ('I14','O12','P02',2,25.0,0.0),
  ('I15','O13','P03',1,75.0,0.0),
  ('I16','O14','P04',1,350.0,0.0),
  ('I17','O15','P11',1,200.0,0.0),
  ('I18','O16','P10',2,45.0,0.0),
  ('I19','O17','P01',1,1200.0,0.0),
  ('I20','O18','P05',1,800.0,0.0),
  ('I21','O19','P06',1,400.0,0.0),
  ('I22','O20','P07',2,250.0,0.0),
  ('I23','O21','P09',1,30.0,0.0),
  ('I24','O22','P08',2,90.0,0.0),
  ('I25','O23','P02',4,25.0,0.0),
  ('I26','O24','P03',1,75.0,0.0),
  ('I27','O25','P04',2,350.0,0.0)""",
    },
]

# ── OpenMetadata table column definitions (for API registration) ───────────────
_OM_TABLE_COLUMNS: dict[str, list[dict]] = {
    "simple_retail.orders": [
        {
            "name": "order_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Unique order identifier",
        },
        {
            "name": "customer_name",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Full name of the customer",
        },
        {
            "name": "customer_email",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Customer email address",
        },
        {
            "name": "product_name",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Name of the product ordered",
        },
        {
            "name": "quantity",
            "dataType": "INT",
            "description": "Number of units ordered",
        },
        {
            "name": "unit_price",
            "dataType": "DOUBLE",
            "description": "Price per unit (USD)",
        },
        {
            "name": "total_amount",
            "dataType": "DOUBLE",
            "description": "Total order value (USD)",
        },
        {
            "name": "status",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Order status",
        },
        {
            "name": "order_date",
            "dataType": "DATE",
            "description": "Date the order was placed",
        },
    ],
    "complex_retail.customers": [
        {
            "name": "customer_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Unique customer ID",
        },
        {
            "name": "first_name",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Customer first name",
        },
        {
            "name": "last_name",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Customer last name",
        },
        {
            "name": "email",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Customer email",
        },
        {
            "name": "country",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Country",
        },
        {
            "name": "city",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "City",
        },
        {
            "name": "created_at",
            "dataType": "TIMESTAMP",
            "description": "Account creation timestamp",
        },
    ],
    "complex_retail.products": [
        {
            "name": "product_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Unique product ID",
        },
        {
            "name": "name",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Product name",
        },
        {
            "name": "category",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Category",
        },
        {
            "name": "subcategory",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Sub-category",
        },
        {"name": "price", "dataType": "DOUBLE", "description": "List price (USD)"},
        {"name": "stock_quantity", "dataType": "INT", "description": "Units in stock"},
    ],
    "complex_retail.orders": [
        {
            "name": "order_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Unique order ID",
        },
        {
            "name": "customer_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "FK → customers.customer_id",
        },
        {"name": "order_date", "dataType": "DATE", "description": "Date placed"},
        {
            "name": "status",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Order status",
        },
        {
            "name": "total_amount",
            "dataType": "DOUBLE",
            "description": "Total value (USD)",
        },
        {
            "name": "shipping_address",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Delivery address",
        },
    ],
    "complex_retail.order_items": [
        {
            "name": "item_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "Unique line item ID",
        },
        {
            "name": "order_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "FK → orders.order_id",
        },
        {
            "name": "product_id",
            "dataType": "VARCHAR",
            "dataLength": 255,
            "description": "FK → products.product_id",
        },
        {"name": "quantity", "dataType": "INT", "description": "Units ordered"},
        {
            "name": "unit_price",
            "dataType": "DOUBLE",
            "description": "Price at time of order (USD)",
        },
        {
            "name": "discount_pct",
            "dataType": "DOUBLE",
            "description": "Discount applied (0.0-1.0)",
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — MinIO bucket
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_minio_bucket() -> None:
    """Create the 'warehouse' S3 bucket in MinIO if it doesn't exist."""

    logger.info("[InfraInit] Checking MinIO bucket '%s' …", _WAREHOUSE_BUCKET)
    client = Minio(
        _MINIO_HOST,
        access_key=_MINIO_ACCESS_KEY,
        secret_key=_MINIO_SECRET_KEY,
        secure=False,
    )
    try:
        if client.bucket_exists(_WAREHOUSE_BUCKET):
            logger.info(
                "[InfraInit] MinIO bucket '%s' already exists — OK", _WAREHOUSE_BUCKET
            )
        else:
            client.make_bucket(_WAREHOUSE_BUCKET)
            logger.info("[InfraInit] MinIO bucket '%s' created ✓", _WAREHOUSE_BUCKET)
    except S3Error as exc:
        logger.error("[InfraInit] MinIO bucket creation failed: %s", exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Trino connectivity + DDL
# ─────────────────────────────────────────────────────────────────────────────


def _trino_conn():
    """Open a raw Trino DBAPI connection (no catalog/schema defaults)."""
    return trino.dbapi.connect(
        host=_TRINO_HOST,
        port=_TRINO_PORT,
        user=_TRINO_USER,
        http_scheme="http",
        request_timeout=30,
    )


def _trino_exec(sql: str, *, ignore_errors: bool = False) -> list:
    """Execute SQL via Trino DBAPI. Returns rows. Logs and optionally re-raises errors."""
    conn = _trino_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        return rows
    except Exception as exc:
        if ignore_errors:
            logger.debug(
                "[InfraInit] Trino exec (ignored error): %s — %s", sql[:120], exc
            )
            return []
        raise
    finally:
        conn.close()


def _ensure_iceberg_tables() -> None:
    """
    Ensure the Postgres tables required by the Iceberg JDBC catalog exist.
    If they are missing, Trino fails to initialize the catalog.
    """

    import psycopg2

    logger.info("[InfraInit] Ensuring Iceberg JDBC catalog tables exist in Postgres …")

    # We use psycopg2 directly since it's already installed via pyproject.toml
    conn_str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/text2sql")
    try:
        with psycopg2.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS iceberg_tables (
                        catalog_name VARCHAR(255) NOT NULL,
                        table_namespace VARCHAR(255) NOT NULL,
                        table_name VARCHAR(255) NOT NULL,
                        metadata_location VARCHAR(1000),
                        previous_metadata_location VARCHAR(1000),
                        PRIMARY KEY (catalog_name, table_namespace, table_name)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS iceberg_namespace_properties (
                        catalog_name VARCHAR(255) NOT NULL,
                        namespace VARCHAR(255) NOT NULL,
                        property_key VARCHAR(255),
                        property_value VARCHAR(1000),
                        PRIMARY KEY (catalog_name, namespace, property_key)
                    );
                """)
            conn.commit()
        logger.info("[InfraInit] Iceberg JDBC catalog tables ensured ✓")
    except Exception as exc:
        logger.error("[InfraInit] Failed to create Iceberg JDBC tables: %s", exc)
        raise


def _wait_for_trino() -> None:
    """
    Poll until Trino's minio catalog is responsive.

    The Iceberg JDBC catalog auto-creates its backing tables in Postgres
    on first query. We issue a benign `SHOW SCHEMAS FROM minio` to trigger
    that initialization and confirm the catalog is healthy.
    """
    logger.info("[InfraInit] Waiting for Trino minio catalog to become ready …")
    for attempt in range(1, _TRINO_READY_RETRIES + 1):
        try:
            rows = _trino_exec("SHOW SCHEMAS FROM minio", ignore_errors=True)
            if rows is not None:
                logger.info(
                    "[InfraInit] Trino minio catalog ready after %d attempt(s) ✓",
                    attempt,
                )
                return
        except Exception as exc:
            logger.debug(
                "[InfraInit] Trino not ready (attempt %d/%d): %s",
                attempt,
                _TRINO_READY_RETRIES,
                exc,
            )
        time.sleep(_TRINO_READY_INTERVAL)

    # Final attempt — raise on failure
    _trino_exec("SHOW SCHEMAS FROM minio")
    logger.info("[InfraInit] Trino minio catalog is ready ✓")


def _ensure_trino_schemas() -> None:
    """Create all required Trino schemas (IF NOT EXISTS — idempotent)."""
    for schema in _SCHEMAS:
        try:
            _trino_exec(schema["sql"])
            logger.info("[InfraInit] Trino schema '%s' ensured ✓", schema["name"])
        except Exception as exc:
            err = str(exc).lower()
            if "already exists" in err:
                logger.info(
                    "[InfraInit] Trino schema '%s' already exists — OK", schema["name"]
                )
            else:
                logger.error(
                    "[InfraInit] Failed to create schema '%s': %s", schema["name"], exc
                )
                raise


def _ensure_trino_tables() -> None:
    """Create all required Trino tables (IF NOT EXISTS — idempotent)."""
    for table in _TABLES:
        try:
            _trino_exec(table["create_sql"])
            logger.info("[InfraInit] Trino table '%s' ensured ✓", table["fqn"])
        except Exception as exc:
            err = str(exc).lower()
            if "already exists" in err:
                logger.info(
                    "[InfraInit] Trino table '%s' already exists — OK", table["fqn"]
                )
            else:
                logger.error(
                    "[InfraInit] Failed to create table '%s': %s", table["fqn"], exc
                )
                raise


def _seed_trino_data() -> None:
    """
    Seed sample rows into tables, clearing any existing data first to ensure
    updated seeds are cleanly applied.
    """
    for table in _TABLES:
        try:
            # Clear existing data to apply updated seeds cleanly
            try:
                _trino_exec(f"DELETE FROM {table['fqn']}")
            except Exception as e:
                # If DELETE is not supported (e.g. some Iceberg configs require specific formats), we ignore and fall back to count checks
                logger.debug("DELETE on %s failed: %s", table["fqn"], e)

            _trino_exec(table["seed_sql"])
            logger.info("[InfraInit] Seeded sample data into '%s' ✓", table["fqn"])
        except Exception as exc:
            logger.warning(
                "[InfraInit] Could not seed '%s' (non-fatal): %s", table["fqn"], exc
            )


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — OpenMetadata registration
# ─────────────────────────────────────────────────────────────────────────────


def _om_login() -> str:
    """Log in to OpenMetadata and return an access token."""
    import httpx

    b64_password = base64.b64encode(b"admin").decode()
    try:
        r = httpx.post(
            f"{_OM_URL}/api/v1/users/login",
            json={"email": "admin@open-metadata.org", "password": b64_password},
            timeout=15.0,
        )
        r.raise_for_status()
        token = r.json().get("accessToken") or r.json().get("token", "")
        if not token:
            raise ValueError(f"No token in login response: {r.json()}")
        logger.info("[InfraInit] OpenMetadata login successful ✓")
        return token
    except Exception as exc:
        logger.error("[InfraInit] OpenMetadata login failed: %s", exc)
        raise


def _om_get(path: str, token: str) -> tuple[str, dict]:
    import httpx

    try:
        r = httpx.get(
            f"{_OM_URL}/api/v1/{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        return str(r.status_code), r.json()
    except Exception as exc:
        return "ERROR", {"error": str(exc)}


def _om_post(path: str, body: dict, token: str) -> tuple[str, dict]:
    import httpx

    try:
        r = httpx.post(
            f"{_OM_URL}/api/v1/{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        return str(r.status_code), r.json()
    except Exception as exc:
        return "ERROR", {"error": str(exc)}


def _ensure_openmetadata_registration() -> None:
    """
    Idempotently register the full hierarchy in OpenMetadata:
      DatabaseService (local_trino)
        └─ Database (minio)
             ├─ Schema: simple_retail
             │    └─ Table: orders
             └─ Schema: complex_retail
                  ├─ Table: customers
                  ├─ Table: products
                  ├─ Table: orders
                  └─ Table: order_items
    """
    try:
        token = _om_login()
    except Exception:
        logger.warning("[InfraInit] Skipping OpenMetadata registration (login failed)")
        return

    # 1. DatabaseService
    svc_id = _ensure_om_service(token)
    if not svc_id:
        return

    # 2. Database (the catalog)
    db_fqn = f"{_OM_SERVICE_NAME}.minio"
    db_id = _ensure_om_database(token, db_fqn, svc_id)
    if not db_id:
        return

    # 3. Schemas + Tables
    schema_ids: dict[str, str] = {}
    for schema_def in _SCHEMAS:
        schema_name = schema_def["name"]
        schema_fqn = f"{db_fqn}.{schema_name}"
        schema_id = _ensure_om_schema(token, schema_fqn, schema_name, db_fqn)
        if schema_id:
            schema_ids[schema_name] = schema_id

    for table in _TABLES:
        schema_name = table["schema"]
        schema_fqn = f"{db_fqn}.{schema_name}"
        table_fqn = f"{schema_fqn}.{table['name']}"
        col_key = f"{schema_name}.{table['name']}"
        columns = _OM_TABLE_COLUMNS.get(col_key, [])
        _ensure_om_table(token, table_fqn, table["name"], schema_fqn, columns)


def _ensure_om_service(token: str) -> str | None:
    status, data = _om_get(f"services/databaseServices/name/{_OM_SERVICE_NAME}", token)
    if status == "200":
        logger.info("[InfraInit] OM service '%s' already exists — OK", _OM_SERVICE_NAME)
        return data["id"]

    status, data = _om_post(
        "services/databaseServices",
        {
            "name": _OM_SERVICE_NAME,
            "displayName": "Local Trino",
            "description": "Local Trino cluster with MinIO/Iceberg storage",
            "serviceType": "Trino",
            "connection": {
                "config": {
                    "type": "Trino",
                    "hostPort": "trino:8080",
                    "username": "trino",
                    "catalog": "minio",
                }
            },
        },
        token,
    )

    if status in ("200", "201"):
        logger.info("[InfraInit] OM service '%s' created ✓", _OM_SERVICE_NAME)
        return data["id"]

    logger.error("[InfraInit] Failed to create OM service (HTTP %s): %s", status, data)
    return None


def _ensure_om_database(token: str, db_fqn: str, svc_id: str) -> str | None:
    status, data = _om_get(f"databases/name/{db_fqn}", token)
    if status == "200":
        logger.info("[InfraInit] OM database '%s' already exists — OK", db_fqn)
        return data["id"]

    status, data = _om_post(
        "databases",
        {
            "name": "minio",
            "displayName": "minio",
            "service": _OM_SERVICE_NAME,
        },
        token,
    )

    if status in ("200", "201"):
        logger.info("[InfraInit] OM database 'minio' created ✓")
        return data["id"]

    logger.error("[InfraInit] Failed to create OM database (HTTP %s): %s", status, data)
    return None


def _ensure_om_schema(
    token: str, schema_fqn: str, schema_name: str, db_fqn: str
) -> str | None:
    status, data = _om_get(f"databaseSchemas/name/{schema_fqn}", token)
    if status == "200":
        logger.info("[InfraInit] OM schema '%s' already exists — OK", schema_fqn)
        return data["id"]

    status, data = _om_post(
        "databaseSchemas",
        {
            "name": schema_name,
            "displayName": schema_name.replace("_", " ").title(),
            "database": db_fqn,
        },
        token,
    )

    if status in ("200", "201"):
        logger.info("[InfraInit] OM schema '%s' created ✓", schema_fqn)
        return data["id"]

    logger.error(
        "[InfraInit] Failed to create OM schema '%s' (HTTP %s): %s",
        schema_fqn,
        status,
        data,
    )
    return None


def _ensure_om_table(
    token: str,
    table_fqn: str,
    table_name: str,
    schema_fqn: str,
    columns: list[dict],
) -> None:
    status, _ = _om_get(f"tables/name/{table_fqn}", token)
    if status == "200":
        logger.info("[InfraInit] OM table '%s' already exists — OK", table_fqn)
        return

    status, data = _om_post(
        "tables",
        {
            "name": table_name,
            "displayName": table_name.replace("_", " ").title(),
            "tableType": "Regular",
            "databaseSchema": schema_fqn,
            "columns": columns,
        },
        token,
    )

    if status in ("200", "201"):
        logger.info("[InfraInit] OM table '%s' registered ✓", table_fqn)
    else:
        logger.error(
            "[InfraInit] Failed to register OM table '%s' (HTTP %s): %s",
            table_fqn,
            status,
            data,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def init_infrastructure() -> None:
    """
    Idempotent full infrastructure initialization.
    Call once at application startup before serving traffic.
    """
    logger.info("[InfraInit] ═══════════════════════════════════════")
    logger.info("[InfraInit] Starting infrastructure initialization")
    logger.info("[InfraInit] ═══════════════════════════════════════")

    try:
        _ensure_minio_bucket()
    except Exception as exc:
        logger.error("[InfraInit] MinIO setup failed — aborting: %s", exc)
        raise

    try:
        _ensure_iceberg_tables()
        _wait_for_trino()
        _ensure_trino_schemas()
        _ensure_trino_tables()
        _seed_trino_data()
    except Exception as exc:
        logger.error("[InfraInit] Trino setup failed — aborting: %s", exc)
        raise

    try:
        _ensure_openmetadata_registration()
    except Exception as exc:
        # OM registration failure is non-fatal — backend can still serve traffic
        logger.warning(
            "[InfraInit] OpenMetadata registration failed (non-fatal): %s", exc
        )

    logger.info("[InfraInit] ═══════════════════════════════════════")
    logger.info("[InfraInit] Infrastructure initialization complete ✓")
    logger.info("[InfraInit] ═══════════════════════════════════════")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_infrastructure()

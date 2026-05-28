#!/usr/bin/env python3
"""
setup_demo_schemas.sh-style script that uses docker exec to do everything
without needing direct host access to Docker's internal network.

Run from the backend/ directory:
    python setup_demo_schemas.py
"""

import subprocess
import sys

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def run(cmd: list, capture=True, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        print(f"  ✗ Command failed: {' '.join(cmd)}")
        print(f"    stdout: {r.stdout.strip()[:400]}")
        print(f"    stderr: {r.stderr.strip()[:400]}")
        sys.exit(1)
    return r


def find_container(keyword: str) -> str:
    """Find first running container whose name contains keyword."""
    r = run(["docker", "ps", "--format", "{{.Names}}"])
    matches = [n for n in r.stdout.strip().splitlines() if keyword.lower() in n.lower()]
    if not matches:
        print(f"  ✗ No running container matching '{keyword}'. Is Docker running?")
        sys.exit(1)
    return matches[0]


def exec_in(container: str, cmd: str, check=True) -> str:
    """Run a shell command inside a container."""
    r = subprocess.run(
        ["docker", "exec", container, "sh", "-c", cmd],
        capture_output=True, text=True
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"exec failed in {container}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


_TRINO_BIN_CACHE: dict = {}

def _find_trino_bin(container: str) -> str:
    """Auto-detect the trino CLI binary path inside the container."""
    if container in _TRINO_BIN_CACHE:
        return _TRINO_BIN_CACHE[container]
    # Try common locations
    candidates = [
        "/usr/bin/trino",
        "/usr/lib/trino/bin/trino",
        "/opt/trino/bin/trino",
    ]
    for path in candidates:
        r = subprocess.run(
            ["docker", "exec", container, "test", "-f", path],
            capture_output=True
        )
        if r.returncode == 0:
            _TRINO_BIN_CACHE[container] = path
            print(f"  ✓ Trino CLI found at {path}")
            return path
    # Last resort: ask the shell
    r = subprocess.run(
        ["docker", "exec", container, "sh", "-c", "which trino 2>/dev/null || find /usr -name trino -type f 2>/dev/null | head -1"],
        capture_output=True, text=True
    )
    path = r.stdout.strip()
    if path:
        _TRINO_BIN_CACHE[container] = path
        print(f"  ✓ Trino CLI found at {path}")
        return path
    raise RuntimeError("Cannot find trino CLI binary in container. Checked: " + ", ".join(candidates))


def exec_trino(container: str, sql: str, catalog: str = "minio") -> str:
    """Run a Trino SQL statement via the CLI inside the trino container."""
    trino_bin = _find_trino_bin(container)
    safe_sql = sql.strip().replace("'", "'\\''")
    cmd = f"{trino_bin} --server http://localhost:8080 --catalog {catalog} --execute '{safe_sql}'"
    r = subprocess.run(
        ["docker", "exec", container, "sh", "-c", cmd],
        capture_output=True, text=True
    )
    combined = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        if "already exists" in combined.lower():
            return "ALREADY_EXISTS"
        raise RuntimeError(combined[:600])
    return r.stdout.strip()


def exec_curl(container: str, method: str, url: str, data: str = "", extra_headers: list = None) -> tuple:
    """Run curl inside a container, return (status_code, body)."""
    headers = ["-H", "Content-Type: application/json"]
    if extra_headers:
        for h in extra_headers:
            headers += ["-H", h]

    curl_parts = ["curl", "-s", "-o", "/tmp/om_resp.txt", "-w", "%{http_code}",
                  "-X", method] + headers
    if data:
        curl_parts += ["-d", data.replace("'", "'\\''")]
    curl_parts.append(url)

    cmd = " ".join(f"'{p}'" if " " in p else p for p in curl_parts)
    r = subprocess.run(["docker", "exec", container, "sh", "-c", cmd],
                       capture_output=True, text=True)
    status = r.stdout.strip()
    body_r = subprocess.run(["docker", "exec", container, "cat", "/tmp/om_resp.txt"],
                             capture_output=True, text=True)
    return status, body_r.stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — MinIO bucket
# ─────────────────────────────────────────────────────────────────────────────

def create_minio_bucket(container: str):
    print(f"\n[1/3] Creating MinIO 'warehouse' bucket (container: {container}) …")
    cmds = [
        "mc alias set local http://localhost:9000 admin password123 --api S3v4 2>&1",
        "mc mb --ignore-existing local/warehouse 2>&1",
    ]
    for cmd in cmds:
        out = exec_in(container, cmd, check=False)
        print(f"  → {out[:120]}")
    print("  ✓ MinIO bucket ready")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Trino schemas and tables
# ─────────────────────────────────────────────────────────────────────────────

SIMPLE_DDL = [
    ("CREATE SCHEMA simple_retail",
     "CREATE SCHEMA IF NOT EXISTS minio.simple_retail WITH (location = 's3://warehouse/simple_retail/')"),
    ("CREATE TABLE simple_retail.orders",
     """CREATE TABLE IF NOT EXISTS minio.simple_retail.orders (
  order_id       VARCHAR,
  customer_name  VARCHAR,
  customer_email VARCHAR,
  product_name   VARCHAR,
  quantity       INTEGER,
  unit_price     DOUBLE,
  total_amount   DOUBLE,
  status         VARCHAR,
  order_date     DATE
) WITH (format = 'PARQUET', location = 's3://warehouse/simple_retail/orders/')"""),
    ("SEED simple_retail.orders",
     """INSERT INTO minio.simple_retail.orders VALUES
  ('ORD-001','Alice Cohen','alice@example.com','Laptop',1,1200.0,1200.0,'delivered',DATE '2024-01-10'),
  ('ORD-002','Bob Levi','bob@example.com','Mouse',2,25.0,50.0,'shipped',DATE '2024-01-12'),
  ('ORD-003','Carol Mizrahi','carol@example.com','Keyboard',1,75.0,75.0,'pending',DATE '2024-01-14'),
  ('ORD-004','Dan Shapiro','dan@example.com','Monitor',2,350.0,700.0,'delivered',DATE '2024-01-15'),
  ('ORD-005','Eve Katz','eve@example.com','Headphones',1,90.0,90.0,'cancelled',DATE '2024-01-16')"""),
]

COMPLEX_DDL = [
    ("CREATE SCHEMA complex_retail",
     "CREATE SCHEMA IF NOT EXISTS minio.complex_retail WITH (location = 's3://warehouse/complex_retail/')"),
    ("CREATE TABLE complex_retail.customers",
     """CREATE TABLE IF NOT EXISTS minio.complex_retail.customers (
  customer_id VARCHAR, first_name VARCHAR, last_name VARCHAR,
  email VARCHAR, country VARCHAR, city VARCHAR, created_at TIMESTAMP
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/customers/')"""),
    ("CREATE TABLE complex_retail.products",
     """CREATE TABLE IF NOT EXISTS minio.complex_retail.products (
  product_id VARCHAR, name VARCHAR, category VARCHAR,
  subcategory VARCHAR, price DOUBLE, stock_quantity INTEGER
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/products/')"""),
    ("CREATE TABLE complex_retail.orders",
     """CREATE TABLE IF NOT EXISTS minio.complex_retail.orders (
  order_id VARCHAR, customer_id VARCHAR, order_date DATE,
  status VARCHAR, total_amount DOUBLE, shipping_address VARCHAR
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/orders/')"""),
    ("CREATE TABLE complex_retail.order_items",
     """CREATE TABLE IF NOT EXISTS minio.complex_retail.order_items (
  item_id VARCHAR, order_id VARCHAR, product_id VARCHAR,
  quantity INTEGER, unit_price DOUBLE, discount_pct DOUBLE
) WITH (format = 'PARQUET', location = 's3://warehouse/complex_retail/order_items/')"""),
    ("SEED complex_retail.customers",
     """INSERT INTO minio.complex_retail.customers VALUES
  ('C01','Alice','Cohen','alice@example.com','Israel','Tel Aviv',TIMESTAMP '2023-06-01 10:00:00'),
  ('C02','Bob','Levi','bob@example.com','Israel','Jerusalem',TIMESTAMP '2023-06-05 11:00:00'),
  ('C03','Carol','Mizrahi','carol@example.com','Israel','Haifa',TIMESTAMP '2023-07-01 09:00:00')"""),
    ("SEED complex_retail.products",
     """INSERT INTO minio.complex_retail.products VALUES
  ('P01','Laptop','Electronics','Computers',1200.0,50),
  ('P02','Mouse','Electronics','Peripherals',25.0,200),
  ('P03','Keyboard','Electronics','Peripherals',75.0,150),
  ('P04','Monitor','Electronics','Displays',350.0,80)"""),
    ("SEED complex_retail.orders",
     """INSERT INTO minio.complex_retail.orders VALUES
  ('O01','C01',DATE '2024-01-10','delivered',1250.0,'Tel Aviv, 1 Main St'),
  ('O02','C02',DATE '2024-01-12','shipped',50.0,'Jerusalem, 5 King St'),
  ('O03','C03',DATE '2024-01-14','pending',350.0,'Haifa, 3 Port Rd')"""),
    ("SEED complex_retail.order_items",
     """INSERT INTO minio.complex_retail.order_items VALUES
  ('I01','O01','P01',1,1200.0,0.0),
  ('I02','O01','P02',2,25.0,0.0),
  ('I03','O02','P02',2,25.0,0.0),
  ('I04','O03','P04',1,350.0,0.0)"""),
]


def create_trino_schemas(container: str):
    print(f"\n[2/3] Creating Trino schemas & tables (container: {container}) …")

    for label, sql in SIMPLE_DDL + COMPLEX_DDL:
        try:
            result = exec_trino(container, sql)
            if result == "ALREADY_EXISTS":
                print(f"  ↺ {label} (already exists)")
            else:
                print(f"  ✓ {label}")
        except RuntimeError as e:
            err = str(e)
            if "already exists" in err.lower():
                print(f"  ↺ {label} (already exists)")
            else:
                print(f"  ✗ {label}: {err[:200]}")
                sys.exit(1)

    print("  ✓ Trino schemas and tables ready")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — OpenMetadata registration (via curl inside OM container)
# ─────────────────────────────────────────────────────────────────────────────

OM_BASE = "http://localhost:8585/api/v1"
OM_SERVICE_NAME = "local_trino"

SIMPLE_TABLES = [
    {
        "name": "orders",
        "schema": "simple_retail",
        "description": "Flat orders table — ideal for single-table queries",
        "columns": [
            {"name": "order_id",       "dataType": "VARCHAR", "dataLength": 255, "description": "Unique order identifier"},
            {"name": "customer_name",  "dataType": "VARCHAR", "dataLength": 255, "description": "Full name of the customer"},
            {"name": "customer_email", "dataType": "VARCHAR", "dataLength": 255, "description": "Customer email address"},
            {"name": "product_name",   "dataType": "VARCHAR", "dataLength": 255, "description": "Name of the product ordered"},
            {"name": "quantity",       "dataType": "INT",                        "description": "Number of units ordered"},
            {"name": "unit_price",     "dataType": "DOUBLE",                     "description": "Price per unit (USD)"},
            {"name": "total_amount",   "dataType": "DOUBLE",                     "description": "Total order value (USD)"},
            {"name": "status",         "dataType": "VARCHAR", "dataLength": 255, "description": "Order status"},
            {"name": "order_date",     "dataType": "DATE",                       "description": "Date the order was placed"},
        ],
    }
]

COMPLEX_TABLES = [
    {
        "name": "customers",
        "schema": "complex_retail",
        "description": "Customer master table",
        "columns": [
            {"name": "customer_id", "dataType": "VARCHAR",   "dataLength": 255, "description": "Unique customer ID"},
            {"name": "first_name",  "dataType": "VARCHAR",   "dataLength": 255, "description": "Customer first name"},
            {"name": "last_name",   "dataType": "VARCHAR",   "dataLength": 255, "description": "Customer last name"},
            {"name": "email",       "dataType": "VARCHAR",   "dataLength": 255, "description": "Customer email"},
            {"name": "country",     "dataType": "VARCHAR",   "dataLength": 255, "description": "Country"},
            {"name": "city",        "dataType": "VARCHAR",   "dataLength": 255, "description": "City"},
            {"name": "created_at",  "dataType": "TIMESTAMP",                    "description": "Account creation timestamp"},
        ],
    },
    {
        "name": "products",
        "schema": "complex_retail",
        "description": "Product catalogue",
        "columns": [
            {"name": "product_id",     "dataType": "VARCHAR", "dataLength": 255, "description": "Unique product ID"},
            {"name": "name",           "dataType": "VARCHAR", "dataLength": 255, "description": "Product name"},
            {"name": "category",       "dataType": "VARCHAR", "dataLength": 255, "description": "Category"},
            {"name": "subcategory",    "dataType": "VARCHAR", "dataLength": 255, "description": "Sub-category"},
            {"name": "price",          "dataType": "DOUBLE",                     "description": "List price (USD)"},
            {"name": "stock_quantity", "dataType": "INT",                        "description": "Units in stock"},
        ],
    },
    {
        "name": "orders",
        "schema": "complex_retail",
        "description": "Orders header — join to order_items for line details",
        "columns": [
            {"name": "order_id",         "dataType": "VARCHAR", "dataLength": 255, "description": "Unique order ID"},
            {"name": "customer_id",      "dataType": "VARCHAR", "dataLength": 255, "description": "FK → customers.customer_id"},
            {"name": "order_date",       "dataType": "DATE",                       "description": "Date placed"},
            {"name": "status",           "dataType": "VARCHAR", "dataLength": 255, "description": "Order status"},
            {"name": "total_amount",     "dataType": "DOUBLE",                     "description": "Total value (USD)"},
            {"name": "shipping_address", "dataType": "VARCHAR", "dataLength": 255, "description": "Delivery address"},
        ],
    },
    {
        "name": "order_items",
        "schema": "complex_retail",
        "description": "Order line items — join to orders and products",
        "columns": [
            {"name": "item_id",      "dataType": "VARCHAR", "dataLength": 255, "description": "Unique line item ID"},
            {"name": "order_id",     "dataType": "VARCHAR", "dataLength": 255, "description": "FK → orders.order_id"},
            {"name": "product_id",   "dataType": "VARCHAR", "dataLength": 255, "description": "FK → products.product_id"},
            {"name": "quantity",     "dataType": "INT",                        "description": "Units ordered"},
            {"name": "unit_price",   "dataType": "DOUBLE",                     "description": "Price at time of order (USD)"},
            {"name": "discount_pct", "dataType": "DOUBLE",                     "description": "Discount applied (0.0–1.0)"},
        ],
    },
]


import httpx


def om_upsert(method: str, path: str, body: dict, token: str) -> tuple:
    url = f"http://localhost:8585/api/v1/{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    fn = httpx.put if method == "PUT" else httpx.post
    try:
        r = fn(url, json=body, headers=headers, timeout=10.0)
        return str(r.status_code), r.json()
    except Exception as e:
        return "ERROR", {"error": str(e)}

def om_get(path: str, token: str) -> tuple:
    url = f"http://localhost:8585/api/v1/{path}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = httpx.get(url, headers=headers, timeout=10.0)
        return str(r.status_code), r.json()
    except Exception as e:
        return "ERROR", {"error": str(e)}

def register_in_openmetadata(container: str):
    print("\n[3/3] Registering in OpenMetadata via localhost:8585 API …")

    # Login
    import base64
    try:
        b64_password = base64.b64encode(b"admin").decode("utf-8")
        r = httpx.post("http://localhost:8585/api/v1/users/login",
                       json={"email": "admin@open-metadata.org", "password": b64_password},
                       timeout=10.0)
        status = str(r.status_code)
        body_json = r.json()
    except Exception as e:
        print(f"  ✗ OpenMetadata login failed (Network Error): {e}")
        print("  Make sure OpenMetadata is running and accessible at http://localhost:8585")
        sys.exit(1)

    if status not in ("200", "201"):
        print(f"  ✗ OpenMetadata login failed (HTTP {status}): {body_json}")
        sys.exit(1)

    token = body_json.get("accessToken") or body_json.get("token", "")
    if not token:
        print("  ✗ Could not parse token from login response.")
        sys.exit(1)

    print("  ✓ Logged in to OpenMetadata")

    # Create DatabaseService
    st, svc = om_get(f"services/databaseServices/name/{OM_SERVICE_NAME}", token)
    if st == "200":
        svc_id = svc["id"]
        print(f"  ↺ DatabaseService '{OM_SERVICE_NAME}' already exists")
    else:
        st, svc = om_upsert("POST", "services/databaseServices", {
            "name": OM_SERVICE_NAME,
            "displayName": "Trino Local (Demo)",
            "description": "Local Trino cluster — demo schemas",
            "serviceType": "Trino",
            "connection": {
                "config": {
                    "type": "Trino",
                    "hostPort": "trino:8080",
                    "username": "trino",
                    "catalog": "minio",
                }
            },
        }, token)
        if st not in ("200", "201"):
            print(f"  ✗ Failed to create service (HTTP {st}): {svc}")
            sys.exit(1)
        svc_id = svc["id"]
        print(f"  ✓ DatabaseService '{OM_SERVICE_NAME}' created")

    # Create Database (minio catalog)
    db_fqn = f"{OM_SERVICE_NAME}.minio"
    st, db = om_get(f"databases/name/{db_fqn}", token)
    if st == "200":
        db_id = db["id"]
        print("  ↺ Database 'minio' already exists")
    else:
        st, db = om_upsert("POST", "databases", {
            "name": "minio",
            "displayName": "minio",
            "service": OM_SERVICE_NAME,
        }, token)
        if st not in ("200", "201"):
            print(f"  ✗ Failed to create database (HTTP {st}): {db}")
            sys.exit(1)
        db_id = db["id"]
        print("  ✓ Database 'minio' created")

    # Register all tables
    all_tables = SIMPLE_TABLES + COMPLEX_TABLES
    schema_ids = {}  # cache schema_id by schema name

    for tbl in all_tables:
        schema = tbl["schema"]

        # Create schema if not cached
        if schema not in schema_ids:
            schema_fqn = f"{OM_SERVICE_NAME}.minio.{schema}"
            st, sc = om_get(f"databaseSchemas/name/{schema_fqn}", token)
            if st == "200":
                schema_ids[schema] = sc["id"]
                print(f"  ↺ Schema '{schema_fqn}' already exists")
            else:
                st, sc = om_upsert("POST", "databaseSchemas", {
                    "name": schema,
                    "displayName": schema.replace("_", " ").title(),
                    "database": db_fqn,
                }, token)
                if st not in ("200", "201"):
                    print(f"  ✗ Failed to create schema '{schema}' (HTTP {st}): {sc}")
                    sys.exit(1)
                schema_ids[schema] = sc["id"]
                print(f"  ✓ Schema '{schema_fqn}' created")

        # Create table
        schema_fqn = f"{OM_SERVICE_NAME}.minio.{schema}"
        tbl_fqn = f"{schema_fqn}.{tbl['name']}"
        st, _ = om_get(f"tables/name/{tbl_fqn}", token)
        if st == "200":
            print(f"  ↺ Table '{tbl_fqn}' already exists")
            continue

        st, result = om_upsert("POST", "tables", {
            "name": tbl["name"],
            "displayName": tbl["name"].replace("_", " ").title(),
            "description": tbl["description"],
            "tableType": "Regular",
            "databaseSchema": schema_fqn,
            "columns": tbl["columns"],
        }, token)
        if st not in ("200", "201"):
            print(f"  ✗ Failed to create table '{tbl_fqn}' (HTTP {st}): {result}")
        else:
            print(f"  ✓ Table '{tbl_fqn}' registered")

    print("  ✓ OpenMetadata registration complete")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("═" * 62)
    print("  Demo Schema Setup")
    print("═" * 62)

    # Auto-detect containers
    minio_container = find_container("minio")
    trino_container = find_container("trino")
    om_container    = find_container("openmetadata_server")

    print(f"  MinIO    → {minio_container}")
    print(f"  Trino    → {trino_container}")
    print(f"  OpenMeta → {om_container}")

    create_minio_bucket(minio_container)
    create_trino_schemas(trino_container)
    register_in_openmetadata(om_container)

    print("\n" + "═" * 62)
    print("  Done! 🎉  Use these as oasis_source_id in the app:")
    print("  ─────────────────────────────────────────────────")
    fqns = [
        "trino_local.minio.simple_retail.orders",
        "trino_local.minio.complex_retail.customers",
        "trino_local.minio.complex_retail.products",
        "trino_local.minio.complex_retail.orders",
        "trino_local.minio.complex_retail.order_items",
    ]
    for f in fqns:
        print(f"    {f}")
    print("═" * 62)

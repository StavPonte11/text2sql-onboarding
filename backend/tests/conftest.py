import urllib.parse

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlmodel import Session, SQLModel, create_engine

import core.db.engine
from app.core.auth import get_current_user
from core.models.models import SecurityUser
from app.config import settings
from core.db.engine import get_session
from app.main import app as fastapi_app

# Parse the database URL from settings
parsed = urllib.parse.urlparse(settings.DATABASE_URL)
# Construct the text2sql_test PostgreSQL URL
test_db_url = f"{parsed.scheme}://{parsed.netloc}/text2sql_test"


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Programmatically creates the postgres test database before testing, and drops it afterwards."""
    # Connect to the default 'postgres' database to issue CREATE/DROP DATABASE
    conn = psycopg2.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        database="postgres",
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    # Clean up previous run database if it wasn't dropped properly
    try:
        cursor.execute("DROP DATABASE IF EXISTS text2sql_test")
    except Exception:
        pass

    cursor.execute("CREATE DATABASE text2sql_test")
    cursor.close()
    conn.close()

    yield

    # Clean up and drop the database
    # Connect to 'postgres' again
    conn = psycopg2.connect(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        database="postgres",
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    try:
        # Terminate active connections before dropping database
        cursor.execute("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = 'text2sql_test'
              AND pid <> pg_backend_pid();
        """)
        cursor.execute("DROP DATABASE IF EXISTS text2sql_test")
    except Exception:
        pass
    cursor.close()
    conn.close()


@pytest.fixture(scope="session")
def test_engine(setup_test_db):
    """Initializes all SQLModel tables on the clean PostgreSQL test database."""
    engine = create_engine(test_db_url, echo=False)

    # Create required PostgreSQL schemas
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS security"))
        conn.commit()

    # Create all tables programmatically
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(scope="session", autouse=True)
def patch_global_engine(test_engine):
    """Globally monkeypatches the core.db.engine.engine to isolate all routers to the test database."""
    original_engine = core.db.engine.engine
    core.db.engine.engine = test_engine
    yield
    core.db.engine.engine = original_engine


@pytest.fixture
def db_session(test_engine):
    """Provides isolated transaction scope database sessions for unit tests."""
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def client(test_engine):
    """FastAPI TestClient with isolated session dependency overrides."""

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    

    def override_get_current_user():
        return SecurityUser(
            id="test-user-id",
            email="test-user@example.com",
            name="Test User",
            is_active=True,
            is_admin=True,
        )

    fastapi_app.dependency_overrides[get_session] = override_get_session
    fastapi_app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


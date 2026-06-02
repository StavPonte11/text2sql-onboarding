import os

from alembic.config import Config
from sqlmodel import Session

from alembic import command
from python_core_utils import get_engine

engine = get_engine()


def create_db_and_tables(alembic_ini_path: str = "alembic.ini"):
    """Run Alembic migrations to update the database schema."""
    if not os.path.exists(alembic_ini_path):
        # Fallback to backend/alembic.ini for testing
        alembic_ini_path = os.path.join(os.getcwd(), "backend", "alembic.ini")
        if not os.path.exists(alembic_ini_path):
            alembic_ini_path = os.path.join(os.getcwd(), "alembic.ini")
    
    alembic_cfg = Config(alembic_ini_path)
    command.upgrade(alembic_cfg, "head")


def get_session():
    with Session(engine) as session:
        yield session

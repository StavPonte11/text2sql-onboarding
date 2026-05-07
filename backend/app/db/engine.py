from sqlmodel import SQLModel, create_engine, Session
from app.config import settings

connect_args = {"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
engine = create_engine(settings.DATABASE_URL, echo=False, connect_args=connect_args)


def create_db_and_tables():
    """Run Alembic migrations to update the database schema."""
    from alembic.config import Config
    from alembic import command
    import os

    # Path to alembic.ini
    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "../../alembic.ini"))
    # Run the migrations
    command.upgrade(alembic_cfg, "head")


def get_session():
    with Session(engine) as session:
        yield session

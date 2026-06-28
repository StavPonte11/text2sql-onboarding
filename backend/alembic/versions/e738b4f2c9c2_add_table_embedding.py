"""Add Table embedding column

Revision ID: e738b4f2c9c2
Revises: d35412e44b34
Create Date: 2026-06-01 17:58:00.000000

"""

from collections.abc import Sequence

import pgvector
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e738b4f2c9c2"
down_revision: str | None = "d35412e44b34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable vector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Add embedding column to tables table
    op.add_column("tables", sa.Column("embedding", pgvector.sqlalchemy.Vector(768), nullable=True))


def downgrade() -> None:
    op.drop_column("tables", "embedding")

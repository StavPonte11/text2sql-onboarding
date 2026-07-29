"""add is_partial to table_profiles

Revision ID: a123e456b789
Revises: f9a3d1c8e205
Create Date: 2026-07-05 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a123e456b789"
down_revision: Union[str, None] = "f9a3d1c8e205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_partial column to table_profiles table
    op.add_column(
        "table_profiles",
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default="false")
    )


def downgrade() -> None:
    op.drop_column("table_profiles", "is_partial")

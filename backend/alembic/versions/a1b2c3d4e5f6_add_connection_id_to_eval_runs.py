"""Add connection_id to eval_runs

Revision ID: a1b2c3d4e5f6
Revises: f9a3d1c8e205
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd3d006362f40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'eval_runs',
        sa.Column('connection_id', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('eval_runs', 'connection_id')

"""Let the user name an evaluation run

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-11-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS name VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS name")

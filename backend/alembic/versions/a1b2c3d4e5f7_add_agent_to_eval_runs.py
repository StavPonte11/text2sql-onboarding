"""Remember which Text-to-SQL agent an evaluation run used

Revision ID: a1b2c3d4e5f7
Revises: f8a9b0c1d2e3
Create Date: 2026-11-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS agent VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS agent")

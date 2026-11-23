"""Remember which LLM model an evaluation run used

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS agent_model VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS agent_model")

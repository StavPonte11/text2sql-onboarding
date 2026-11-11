"""Remember which questions a tag-filtered evaluation run covers

Revision ID: d6e7f8a9b0c1
Revises: c4d5e6f7a8b9
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS question_tags JSON")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS question_ids JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS question_ids")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS question_tags")

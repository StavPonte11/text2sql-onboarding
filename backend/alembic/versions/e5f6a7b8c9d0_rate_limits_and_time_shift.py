"""A run's time shift days, its evaluation job, and its rate-limited questions

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c0
Create Date: 2026-11-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS time_shift JSON")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS eval_job_id VARCHAR")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS rate_limited JSON")
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS rerun_question_ids JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS rerun_question_ids")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS rate_limited")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS eval_job_id")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS time_shift")

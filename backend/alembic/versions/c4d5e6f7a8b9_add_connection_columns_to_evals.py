"""Add connection columns that evaluations by connection need

eval_runs.connection_name and golden_questions.connection_id were added to the
models without a migration, and runs/questions on a connection have no table,
so table_id must allow NULL. Written with IF NOT EXISTS because some databases
already got these by hand.

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-10-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS connection_name VARCHAR")
    op.execute("ALTER TABLE eval_runs ALTER COLUMN table_id DROP NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_eval_runs_connection_id ON eval_runs (connection_id)")

    op.execute("ALTER TABLE golden_questions ADD COLUMN IF NOT EXISTS connection_id INTEGER")
    op.execute("ALTER TABLE golden_questions ALTER COLUMN table_id DROP NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_golden_questions_connection_id "
        "ON golden_questions (connection_id)"
    )


def downgrade() -> None:
    op.drop_index('ix_golden_questions_connection_id', table_name='golden_questions')
    op.drop_column('golden_questions', 'connection_id')
    op.drop_index('ix_eval_runs_connection_id', table_name='eval_runs')
    op.drop_column('eval_runs', 'connection_name')

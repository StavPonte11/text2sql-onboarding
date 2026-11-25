"""Keep benchmark (BIRD) results: they aren't golden questions

eval_results.question_id no longer references golden_questions, and a
benchmark result keeps its own question and SQL. Deleting a golden question
deletes its results in code instead of by cascade.

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b9
Create Date: 2026-11-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c0'
down_revision: Union[str, None] = 'c3d4e5f6a7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eval_results DROP CONSTRAINT IF EXISTS eval_results_question_id_fkey"
    )
    op.execute("ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS question VARCHAR")
    op.execute("ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS expected_sql VARCHAR")
    op.execute("ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS generated_sql VARCHAR")


def downgrade() -> None:
    # Benchmark results have no golden question to point at
    op.execute(
        "DELETE FROM eval_results WHERE question_id NOT IN (SELECT id FROM golden_questions)"
    )
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS generated_sql")
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS expected_sql")
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS question")
    op.execute(
        "ALTER TABLE eval_results ADD CONSTRAINT eval_results_question_id_fkey "
        "FOREIGN KEY (question_id) REFERENCES golden_questions (id) "
        "ON DELETE CASCADE ON UPDATE CASCADE"
    )

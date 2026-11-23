"""Save each evaluation run's final score on the run

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-11-23 00:00:00.000000

Adds eval_runs.final_score and eval_runs.score_breakdown and fills them for
runs that already finished, from their saved per-question results: time shift
when the question has one, else contains accuracy, unscored questions as 0.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b9"
down_revision: Union[str, None] = "b2c3d4e5f6a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _question_score(scores, legacy_score):
    if scores is None:
        return legacy_score, "contains_accuracy"
    for key in ("time_shift", "contains_accuracy"):
        if scores.get(key) is not None:
            return float(scores[key]), key
    return None, None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS final_score DOUBLE PRECISION"
    )
    op.execute("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS score_breakdown JSON")

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT r.run_id, r.scores, r.score FROM eval_results r "
            "JOIN eval_runs e ON e.id = r.run_id "
            "WHERE e.status = 'completed' AND e.final_score IS NULL"
        )
    ).all()
    by_run: dict[str, list] = {}
    for run_id, scores, score in rows:
        if isinstance(scores, str):
            scores = json.loads(scores)
        by_run.setdefault(run_id, []).append(_question_score(scores, score))

    for run_id, question_scores in by_run.items():
        parts = {"time_shift": [], "contains_accuracy": []}
        unscored = 0
        for score, source in question_scores:
            if source is None or score is None:
                unscored += 1
            else:
                parts[source].append(score)
        scored = parts["time_shift"] + parts["contains_accuracy"]
        total = len(scored) + unscored
        breakdown = {
            "questions": total,
            **{
                key: {
                    "count": len(values),
                    "average": round(sum(values) / len(values), 4) if values else None,
                }
                for key, values in parts.items()
            },
            "unscored": unscored,
        }
        conn.execute(
            sa.text(
                "UPDATE eval_runs SET final_score = :score, score_breakdown = CAST(:breakdown AS JSON) "
                "WHERE id = :id"
            ),
            {
                "score": round(sum(scored) / total, 4) if total else None,
                "breakdown": json.dumps(breakdown),
                "id": run_id,
            },
        )


def downgrade() -> None:
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS score_breakdown")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS final_score")

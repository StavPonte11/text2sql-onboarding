"""
The score shown for evaluation questions and runs.

A question's score is its time shift score when it has one (the result held
up when the dates moved), otherwise its contains accuracy. A question neither
could judge (the agent failed, timed out or gave no SQL) scores 0.

A run's final score is the average over all its questions, computed once when
the run finishes and saved on the run (EvalRun.final_score), with a breakdown
of how many were scored each way. A failed run scores 0. That one number is
shown everywhere and decides is_publishable and the regression alerts.
"""

from typing import Any

from core.models.models import EvalResult, EvalRun, EvalStatus

TIME_SHIFT = "time_shift"
CONTAINS = "contains_accuracy"


def score_from_scores(
    scores: dict | None, legacy_score: float | None = None
) -> tuple[float | None, str | None]:
    """(score, which metric it came from); (None, None) when neither could judge it.

    `scores` is None only for results saved before per-metric scores were kept:
    their score (`legacy_score`) is contains accuracy.
    """
    if scores is None:
        return legacy_score, CONTAINS
    if scores.get(TIME_SHIFT) is not None:
        return float(scores[TIME_SHIFT]), TIME_SHIFT
    if scores.get(CONTAINS) is not None:
        return float(scores[CONTAINS]), CONTAINS
    return None, None


def question_score(result: EvalResult) -> tuple[float, str | None]:
    """(score, which metric it came from); (0.0, None) when neither could judge it."""
    score, source = score_from_scores(result.scores, result.score)
    return (0.0 if score is None else score), source


def run_score(
    question_scores: list[tuple[float | None, str | None]],
) -> tuple[float | None, dict[str, Any]]:
    """The average over every question (unscored ones count as 0) and its breakdown.

    `question_scores` holds (score, source) per question, as score_from_scores
    gives them. The score is None when there are no questions.
    """
    by_source: dict[str, list[float]] = {TIME_SHIFT: [], CONTAINS: []}
    unscored = 0
    for score, source in question_scores:
        if source is None or score is None:
            unscored += 1
        else:
            by_source[source].append(score)

    def part(values: list[float]) -> dict[str, Any]:
        return {
            "count": len(values),
            "average": round(sum(values) / len(values), 4) if values else None,
        }

    scored = by_source[TIME_SHIFT] + by_source[CONTAINS]
    total = len(scored) + unscored
    breakdown = {
        "questions": total,
        TIME_SHIFT: part(by_source[TIME_SHIFT]),
        CONTAINS: part(by_source[CONTAINS]),
        "unscored": unscored,
    }
    return (round(sum(scored) / total, 4) if total else None), breakdown


def results_score(results: list[EvalResult]) -> tuple[float | None, dict[str, Any]]:
    """run_score over saved results (used while a run is still going)."""
    return run_score([score_from_scores(r.scores, r.score) for r in results])


def cases_score(cases: list) -> tuple[float | None, dict[str, Any]]:
    """run_score over the evaluation service's cases (DTOs or dicts) of a finished run."""

    def _scores(case) -> dict:
        scores = (
            case.get("scores")
            if isinstance(case, dict)
            else getattr(case, "scores", None)
        )
        return dict(scores or {})

    return run_score([score_from_scores(_scores(c)) for c in cases or []])


def final_run_score(run: EvalRun) -> float | None:
    """The run's final score: 0 when it failed, the saved score once it finished.

    None while it is still running (or when it was cancelled before finishing).
    """
    if run.status == EvalStatus.failed:
        return 0.0
    if run.final_score is not None:
        return run.final_score
    if run.status == EvalStatus.completed:
        # finished before final scores were saved and not backfilled
        return run.score
    return None

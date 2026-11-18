"""
The score shown for evaluation questions and runs.

A question's score is its time shift score when it has one (the result held
up when the dates moved), otherwise its contains accuracy. A run's score is
the average over its scored questions, with a breakdown of how many were
scored each way.
"""

from typing import Any

from core.models.models import EvalResult

TIME_SHIFT = "time_shift"
CONTAINS = "contains_accuracy"


def question_score(result: EvalResult) -> tuple[float | None, str | None]:
    """(score, which metric it came from); (None, None) when neither could judge it."""
    scores = result.scores
    if scores is None:
        # saved before per-metric scores were kept: the score is contains accuracy
        return result.score, CONTAINS
    if scores.get(TIME_SHIFT) is not None:
        return float(scores[TIME_SHIFT]), TIME_SHIFT
    if scores.get(CONTAINS) is not None:
        return float(scores[CONTAINS]), CONTAINS
    return None, None


def run_score(results: list[EvalResult]) -> tuple[float | None, dict[str, Any]]:
    """The run's score (None before any question is scored) and its breakdown."""
    by_source: dict[str, list[float]] = {TIME_SHIFT: [], CONTAINS: []}
    unscored = 0
    for result in results:
        score, source = question_score(result)
        if source is None:
            unscored += 1
        else:
            by_source[source].append(score)

    def part(values: list[float]) -> dict[str, Any]:
        return {
            "count": len(values),
            "average": round(sum(values) / len(values), 4) if values else None,
        }

    scored = by_source[TIME_SHIFT] + by_source[CONTAINS]
    breakdown = {
        "questions": len(scored),
        TIME_SHIFT: part(by_source[TIME_SHIFT]),
        CONTAINS: part(by_source[CONTAINS]),
        "unscored": unscored,
    }
    return (round(sum(scored) / len(scored), 4) if scored else None), breakdown

"""
The time shift's days for a run.

A run can pick its own instead of the evaluation service's defaults
(1, 7, 14, 30 and 60 days back). Each is either a number of days back
("-7") or a date ("2023-10-07"), meaning the days back to that date — counted
when the evaluation job starts, so a rerun of the run shifts to the same dates.
"""

from datetime import date

# The evaluation service's own (TIME_SHIFT_OFFSETS_DAYS)
DEFAULT_TIME_SHIFT = ["-1", "-7", "-14", "-30", "-60"]
MAX_TIME_SHIFTS = 20
# The evaluation service accepts offsets up to 100 years
MAX_DAYS = 36500


def normalize_time_shift(values: list[str] | None) -> list[str] | None:
    """
    The run's time shift entries, checked and deduplicated, or None for the
    defaults (nothing chosen, or exactly the defaults).

    Raises ValueError for anything that is neither a whole number of days nor
    a date (YYYY-MM-DD), for 0 days or today's date (shifts nothing), and for
    more than MAX_TIME_SHIFTS entries.
    """
    if not values:
        return None
    entries: list[str] = []
    for raw in values:
        value = (raw or "").strip()
        if not value:
            continue
        try:
            day = date.fromisoformat(value)
        except ValueError:
            day = None
        if day is not None:
            entry = day.isoformat()
            if day == date.today():
                raise ValueError(f"{entry} is today: it would shift nothing")
        else:
            try:
                days = int(value)
            except ValueError as exc:
                raise ValueError(
                    f"{value!r} is neither a number of days nor a date (YYYY-MM-DD)"
                ) from exc
            if days == 0:
                raise ValueError("0 days would shift nothing")
            if abs(days) > MAX_DAYS:
                raise ValueError(f"{days} days is more than {MAX_DAYS}")
            entry = str(days)
        if entry not in entries:
            entries.append(entry)
    if not entries:
        return None
    if len(entries) > MAX_TIME_SHIFTS:
        raise ValueError(f"At most {MAX_TIME_SHIFTS} time shift days or dates")
    if sorted(entries) == sorted(DEFAULT_TIME_SHIFT):
        return None
    return entries


def time_shift_offsets(
    entries: list[str] | None, today: date | None = None
) -> list[int] | None:
    """The offsets in days to send to the evaluation service (None = its defaults)."""
    if not entries:
        return None
    today = today or date.today()
    offsets: list[int] = []
    for entry in entries:
        if "-" in entry[1:]:  # a date (a negative number has its '-' first)
            offset = (date.fromisoformat(entry) - today).days
        else:
            offset = int(entry)
        offset = max(-MAX_DAYS, min(MAX_DAYS, offset))
        if offset != 0 and offset not in offsets:
            offsets.append(offset)
    return offsets or None

from __future__ import annotations

import re
from datetime import date, timedelta

from parsers.rzgmu_pdf import LOCATION_START_RE

DATE_GROUP_RE = re.compile(
    r"(\d{1,2}(?:\s*,\s*\d{1,2})*)\s*/\s*(\d{1,2})",
    re.IGNORECASE,
)
DATE_RANGE_RE = re.compile(
    r"(\d{1,2})\s*/\s*(\d{1,2})\s*-\s*(\d{1,2})\s*/\s*(\d{1,2})",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(r",?\s*кр\.\s*\d{1,2}/\d{1,2}", re.IGNORECASE)


def monday_of(value: date) -> date:
    return value - timedelta(days=value.weekday())


def week_label(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    month = week_end.month if week_start.month != week_end.month else week_start.month
    return f"{week_start.day}-{week_end.day}/{month:02d}"


def shift_week(week_start: date, delta_weeks: int) -> date:
    return week_start + timedelta(weeks=delta_weeks)


def _infer_year(month: int, reference: date) -> int:
    if month >= 9:
        return reference.year if reference.month >= 9 else reference.year - 1
    return reference.year + 1 if reference.month >= 9 else reference.year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _dates_part(extra: str) -> str:
    text = (extra or "").strip()
    if not text:
        return ""
    match = LOCATION_START_RE.search(text)
    if match:
        start = match.start()
        if text[start] in " ,;":
            start += 1
        text = text[: match.start()].strip(" ,;")
    return CONTROL_RE.sub("", text).strip(" ,;")


def parse_extra_dates(extra: str, *, reference: date) -> tuple[list[date], tuple[date, date] | None]:
    """Return explicit dates and optional weekly date range from lesson extra."""
    part = _dates_part(extra)
    if not part:
        return [], None

    dates: list[date] = []
    range_bounds: tuple[date, date] | None = None

    range_match = DATE_RANGE_RE.search(part)
    if range_match:
        d1, m1, d2, m2 = (int(range_match.group(i)) for i in range(1, 5))
        y1, y2 = _infer_year(m1, reference), _infer_year(m2, reference)
        start = _safe_date(y1, m1, d1)
        end = _safe_date(y2, m2, d2)
        if start and end:
            range_bounds = (start, end if end >= start else end)

    for match in DATE_GROUP_RE.finditer(part):
        days_raw, month_raw = match.group(1), match.group(2)
        month = int(month_raw)
        year = _infer_year(month, reference)
        for token in days_raw.split(","):
            token = token.strip()
            if not token.isdigit():
                continue
            parsed = _safe_date(year, month, int(token))
            if parsed:
                dates.append(parsed)

    return sorted(set(dates)), range_bounds


def has_scheduled_dates(extra: str) -> bool:
    part = _dates_part(extra)
    if not part:
        return False
    return bool(DATE_GROUP_RE.search(part) or DATE_RANGE_RE.search(part))


def lesson_visible_on_week(
    lesson: dict,
    day_index: int,
    week_start: date,
) -> bool:
    extra = lesson.get("extra", "")
    if not has_scheduled_dates(extra):
        return True

    week_end = week_start + timedelta(days=6)
    lesson_day = week_start + timedelta(days=day_index)
    if not (week_start <= lesson_day <= week_end):
        return False

    explicit, date_range = parse_extra_dates(extra, reference=week_start)

    if explicit:
        return any(week_start <= item <= week_end for item in explicit)

    if date_range:
        start, end = date_range
        return start <= lesson_day <= end

    return True


def filter_weekly_schedule(schedule: dict, week_start: date) -> dict:
    filtered: dict = {}
    for key, value in schedule.items():
        if key.startswith("__"):
            filtered[key] = value
            continue
        if not str(key).isdigit():
            filtered[key] = value
            continue
        day_index = int(key)
        lessons = value if isinstance(value, list) else []
        filtered[key] = [
            lesson
            for lesson in lessons
            if isinstance(lesson, dict)
            and lesson_visible_on_week(lesson, day_index, week_start)
        ]
    filtered.setdefault("__week__", {})
    filtered["__week__"] = {
        **filtered.get("__week__", {}),
        "calendar_start": week_start.isoformat(),
        "calendar_label": week_label(week_start),
    }
    return filtered

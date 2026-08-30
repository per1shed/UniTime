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
    return f"{week_start.day:02d}.{week_start.month:02d}-{week_end.day:02d}.{week_end.month:02d}"


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
        return any(
            item.weekday() == day_index and week_start <= item <= week_end
            for item in explicit
        )

    if date_range:
        start, end = date_range
        return start <= lesson_day <= end

    return True


def _slot_key(lesson: dict) -> tuple[str, str]:
    return lesson.get("start", ""), lesson.get("end", "")


def filter_day_lessons(
    lessons: list[dict],
    day_index: int,
    week_start: date,
) -> list[dict]:
    """Keep only lessons relevant for the selected week.

    If a time slot contains date-specific lectures, show only those that
    fall on this week — hide other dated subjects and undated placeholders.
    """
    slots: dict[tuple[str, str], list[dict]] = {}
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        slots.setdefault(_slot_key(lesson), []).append(lesson)

    filtered: list[dict] = []
    for slot_lessons in slots.values():
        has_dated = any(has_scheduled_dates(lesson.get("extra", "")) for lesson in slot_lessons)
        if has_dated:
            for lesson in slot_lessons:
                extra = lesson.get("extra", "")
                if not has_scheduled_dates(extra):
                    continue
                if lesson_visible_on_week(lesson, day_index, week_start):
                    filtered.append(lesson)
            continue

        for lesson in slot_lessons:
            if lesson_visible_on_week(lesson, day_index, week_start):
                filtered.append(lesson)

    filtered.sort(key=lambda item: item.get("start", ""))
    return filtered


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
        filtered[key] = filter_day_lessons(lessons, day_index, week_start)
    filtered.setdefault("__week__", {})
    filtered["__week__"] = {
        **filtered.get("__week__", {}),
        "calendar_start": week_start.isoformat(),
        "calendar_label": week_label(week_start),
    }
    return filtered

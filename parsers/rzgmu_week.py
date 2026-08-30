from __future__ import annotations

from datetime import date, datetime, timedelta


NUMERATOR = "numerator"
DENOMINATOR = "denominator"

WEEK_TYPE_LABELS = {
    NUMERATOR: "Числитель",
    DENOMINATOR: "Знаменатель",
}


def is_rzgmu_source(pdf_path: str) -> bool:
    return pdf_path.startswith("/upload/schedule/")


def week_type_for_date(value: date) -> str:
    year = value.year if value.month >= 9 else value.year - 1
    semester_start = date(year, 9, 1)
    week_number = max(0, (value - semester_start).days // 7)
    return NUMERATOR if week_number % 2 == 0 else DENOMINATOR


def current_rzgmu_week_type(now: datetime | None = None) -> str:
    moment = now or datetime.now()
    return week_type_for_date(moment.date())


def week_type_label(week_type: str | None) -> str | None:
    if not week_type:
        return None
    return WEEK_TYPE_LABELS.get(week_type)


def calendar_key_for_date(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return f"{value.month}-{value.day}"
    return f"{value.month}-{value.day}"


def _has_scheduled_dates(extra: str) -> bool:
    from parsers.rzgmu_dates import has_scheduled_dates

    return has_scheduled_dates(extra)


def _merge_day_lessons(
    primary: list,
    other: list,
) -> list:
    """Keep undated lessons from the selected week type; add dated lessons from both."""
    merged: list = []
    seen: set[tuple] = set()

    def _key(lesson: dict) -> tuple:
        return (
            lesson.get("start", ""),
            lesson.get("end", ""),
            lesson.get("subject", ""),
            lesson.get("extra", ""),
        )

    for lesson in primary:
        if not isinstance(lesson, dict):
            continue
        key = _key(lesson)
        if key in seen:
            continue
        seen.add(key)
        merged.append(lesson)

    for lesson in other:
        if not isinstance(lesson, dict):
            continue
        if not _has_scheduled_dates(lesson.get("extra", "")):
            continue
        key = _key(lesson)
        if key in seen:
            continue
        seen.add(key)
        merged.append(lesson)

    merged.sort(key=lambda item: item.get("start", ""))
    return merged


def resolve_rzgmu_schedule(
    schedule: dict | None,
    *,
    week_type: str | None = None,
    week_start: date | None = None,
    now: datetime | None = None,
) -> dict:
    if not schedule:
        return {}

    moment = now or datetime.now()
    resolved = dict(schedule)
    meta = schedule.get("__meta__", {})

    if week_type:
        selected_week = week_type
    elif week_start is not None:
        selected_week = week_type_for_date(week_start)
    else:
        selected_week = current_rzgmu_week_type(moment)

    if meta.get("has_week_types") or schedule.get("__numerator__") or schedule.get("__denominator__"):
        primary_key = f"__{selected_week}__"
        other_key = f"__{DENOMINATOR if selected_week == NUMERATOR else NUMERATOR}__"
        primary_data = schedule.get(primary_key, {}) or {}
        other_data = schedule.get(other_key, {}) or {}

        for day in range(7):
            day_key = str(day)
            primary_lessons = primary_data.get(day_key, []) or []
            other_lessons = other_data.get(day_key, []) or []
            # Also include dated top-level lessons if present.
            top_lessons = schedule.get(day_key, []) or []
            dated_top = [
                lesson
                for lesson in top_lessons
                if isinstance(lesson, dict) and _has_scheduled_dates(lesson.get("extra", ""))
            ]
            resolved[day_key] = _merge_day_lessons(
                list(primary_lessons),
                list(other_lessons) + dated_top,
            )

        resolved.setdefault("__week__", {})
        resolved["__week__"] = {
            "type": selected_week,
            "type_label": week_type_label(selected_week) or "",
        }

    if schedule.get("__calendar__") and not any(str(day) in schedule for day in range(7)):
        if week_start is not None:
            for offset in range(7):
                day = week_start + timedelta(days=offset)
                day_key = calendar_key_for_date(day)
                resolved[str(offset)] = schedule.get("__calendar__", {}).get(day_key, [])
        else:
            today_key = calendar_key_for_date(moment)
            tomorrow = moment + timedelta(days=1)
            tomorrow_key = calendar_key_for_date(tomorrow)
            resolved[str(moment.weekday())] = schedule.get("__calendar__", {}).get(today_key, [])
            resolved[str(tomorrow.weekday())] = schedule.get("__calendar__", {}).get(tomorrow_key, [])

    return resolved

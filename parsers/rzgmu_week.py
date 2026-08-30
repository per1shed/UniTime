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


def calendar_key_for_date(date: datetime) -> str:
    return f"{date.month}-{date.day}"


def resolve_rzgmu_schedule(
    schedule: dict | None,
    *,
    week_type: str | None = None,
    now: datetime | None = None,
) -> dict:
    if not schedule:
        return {}

    moment = now or datetime.now()
    resolved = dict(schedule)
    meta = schedule.get("__meta__", {})
    selected_week = week_type or current_rzgmu_week_type(moment)

    if meta.get("has_week_types") or schedule.get("__numerator__") or schedule.get("__denominator__"):
        week_data = schedule.get(
            "__numerator__" if selected_week == NUMERATOR else "__denominator__",
            {},
        )
        for day_key, lessons in week_data.items():
            if str(day_key).isdigit():
                resolved[str(day_key)] = lessons
        resolved.setdefault("__week__", {})
        resolved["__week__"] = {
            "type": selected_week,
            "type_label": week_type_label(selected_week) or "",
        }

    if schedule.get("__calendar__") and not any(str(day) in schedule for day in range(7)):
        today_key = calendar_key_for_date(moment)
        tomorrow = moment + timedelta(days=1)
        tomorrow_key = calendar_key_for_date(tomorrow)
        resolved[str(moment.weekday())] = schedule.get("__calendar__", {}).get(today_key, [])
        resolved[str(tomorrow.weekday())] = schedule.get("__calendar__", {}).get(tomorrow_key, [])

    return resolved

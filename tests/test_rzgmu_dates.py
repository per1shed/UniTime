from datetime import date

from parsers.rzgmu_dates import (
    filter_weekly_schedule,
    lesson_visible_on_week,
    parse_extra_dates,
    week_label,
)


def test_parse_explicit_dates():
    ref = date(2025, 9, 1)
    dates, date_range = parse_extra_dates("15,29/09", reference=ref)
    assert dates == [date(2025, 9, 15), date(2025, 9, 29)]
    assert date_range is None

    dates, _ = parse_extra_dates("8,22/09", reference=ref)
    assert dates == [date(2025, 9, 8), date(2025, 9, 22)]


def test_parse_multiple_date_groups():
    ref = date(2025, 11, 1)
    dates, _ = parse_extra_dates("17,24/11; 1/12", reference=ref)
    assert dates == [date(2025, 11, 17), date(2025, 11, 24), date(2025, 12, 1)]


def test_lesson_visible_by_week_dates():
    ref = date(2025, 9, 1)
    anatomy = {"subject": "Анатомия", "extra": "15,29/09"}
    military = {"subject": "Основы военной подготовки", "extra": "8,22/09"}

    week_sep_15 = date(2025, 9, 15)
    assert lesson_visible_on_week(anatomy, 1, week_sep_15) is True
    assert lesson_visible_on_week(military, 1, week_sep_15) is False

    week_sep_8 = date(2025, 9, 8)
    assert lesson_visible_on_week(military, 1, week_sep_8) is True
    assert lesson_visible_on_week(anatomy, 1, week_sep_8) is False

    week_sep_22 = date(2025, 9, 22)
    assert lesson_visible_on_week(military, 1, week_sep_22) is True
    assert lesson_visible_on_week(anatomy, 1, week_sep_22) is False


def test_lesson_without_dates_always_visible():
    lesson = {"subject": "Физика", "extra": "ауд. 101"}
    week = date(2025, 9, 15)
    assert lesson_visible_on_week(lesson, 1, week) is True


def test_filter_weekly_schedule_sets_label():
    schedule = {
        "1": [
            {"subject": "Анатомия", "extra": "15,29/09", "start": "10.00", "end": "11.40"},
            {"subject": "Основы военной подготовки", "extra": "8,22/09", "start": "10.00", "end": "11.40"},
        ],
    }
    week = date(2025, 9, 15)
    filtered = filter_weekly_schedule(schedule, week)
    assert len(filtered["1"]) == 1
    assert filtered["1"][0]["subject"] == "Анатомия"
    assert filtered["__week__"]["calendar_label"] == week_label(week)

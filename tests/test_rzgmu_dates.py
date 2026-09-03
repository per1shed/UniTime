from datetime import date

from parsers.rzgmu_dates import (
    filter_day_lessons,
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
    anatomy = {"subject": "Анатомия", "extra": "15,29/09"}
    military = {"subject": "Основы военной подготовки", "extra": "8,22/09"}

    # 2026-09-14 is Monday; 15.09 and 08.09 are Tuesdays.
    week_sep_15 = date(2026, 9, 14)
    assert lesson_visible_on_week(anatomy, 1, week_sep_15) is True
    assert lesson_visible_on_week(military, 1, week_sep_15) is False

    week_sep_8 = date(2026, 9, 7)
    assert lesson_visible_on_week(military, 1, week_sep_8) is True
    assert lesson_visible_on_week(anatomy, 1, week_sep_8) is False

    week_sep_22 = date(2026, 9, 21)
    assert lesson_visible_on_week(military, 1, week_sep_22) is True
    assert lesson_visible_on_week(anatomy, 1, week_sep_22) is False


def test_lesson_without_dates_always_visible():
    lesson = {"subject": "Физика", "extra": "ауд. 101"}
    week = date(2026, 9, 14)
    assert lesson_visible_on_week(lesson, 1, week) is True


def test_filter_weekly_schedule_sets_label():
    schedule = {
        "1": [
            {"subject": "Анатомия", "extra": "15,29/09", "start": "10.00", "end": "11.40"},
            {"subject": "Основы военной подготовки", "extra": "8,22/09", "start": "10.00", "end": "11.40"},
            {"subject": "Биохимия", "extra": "17,24/11; 1/12", "start": "10.00", "end": "11.40"},
        ],
    }
    week = date(2026, 9, 14)
    filtered = filter_weekly_schedule(schedule, week)
    assert len(filtered["1"]) == 1
    assert filtered["1"][0]["subject"] == "Анатомия"
    assert filtered["__week__"]["calendar_label"] == week_label(week)


def test_dated_slot_hides_other_subjects_and_undated():
    lessons = [
        {"subject": "Анатомия", "extra": "15,29/09", "start": "10.00", "end": "11.40"},
        {"subject": "Основы военной подготовки", "extra": "8,22/09", "start": "10.00", "end": "11.40"},
        {"subject": "Биохимия", "extra": "17,24/11; 1/12", "start": "10.00", "end": "11.40"},
        {"subject": "Физика", "extra": "ауд. 101", "start": "10.00", "end": "11.40"},
    ]
    week = date(2026, 9, 14)
    filtered = filter_day_lessons(lessons, 1, week)
    assert [item["subject"] for item in filtered] == ["Анатомия"]


def test_undated_slot_still_shows_regular_lessons():
    lessons = [
        {"subject": "Физика", "extra": "ауд. 101", "start": "12.00", "end": "13.30"},
        {"subject": "Химия", "extra": "ауд. 202", "start": "12.00", "end": "13.30"},
    ]
    week = date(2026, 9, 14)
    filtered = filter_day_lessons(lessons, 1, week)
    assert len(filtered) == 2


def test_august_week_uses_september_same_year():
    ref = date(2026, 8, 31)
    dates, _ = parse_extra_dates("2,23/09", reference=ref)
    assert dates == [date(2026, 9, 2), date(2026, 9, 23)]


def test_date_range_is_not_two_explicit_days():
    ref = date(2026, 9, 7)
    dates, date_range = parse_extra_dates(
        "8/09-10/11 Медико-профилактический корпус",
        reference=ref,
    )
    assert dates == []
    assert date_range == (date(2026, 9, 8), date(2026, 11, 10))
    lesson = {"subject": "Основы российской государственности", "extra": "8/09-10/11"}
    assert lesson_visible_on_week(lesson, 1, date(2026, 9, 7)) is True
    assert lesson_visible_on_week(lesson, 1, date(2026, 9, 14)) is True
    assert lesson_visible_on_week(lesson, 1, date(2026, 8, 31)) is False


def test_control_date_excluded_from_range():
    lesson = {
        "subject": "лек Основы российской государственности",
        "extra": "14/10-23/12, кр. 4/11",
    }
    assert lesson_visible_on_week(lesson, 2, date(2026, 11, 2)) is False
    assert lesson_visible_on_week(lesson, 2, date(2026, 10, 12)) is True

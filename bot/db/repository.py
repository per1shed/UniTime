from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot import emoji as e
from bot.db.models import (
    NotificationLog,
    ScheduleCache,
    ScheduleSource,
    Specialty,
    University,
    User,
    UserSubscription,
)
from parsers.rzgmu_week import (
    calendar_key_for_date,
    is_rzgmu_source,
    resolve_rzgmu_schedule,
    week_type_label,
)


@dataclass
class Lesson:
    start: str
    end: str
    subject: str
    extra: str = ""


DAY_NAMES = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]


async def ensure_universities(session: AsyncSession) -> list[University]:
    await remove_university_by_code(session, "rsu")
    await remove_university_by_code(session, "rsreu")

    configs = (
        {
            "code": "rzgmu",
            "name": "РязГМУ",
            "base_url": "https://www.rzgmu.ru",
            "schedule_page_path": "/students/student/schedule",
        },
    )
    universities: list[University] = []
    for config in configs:
        stmt = select(University).where(University.code == config["code"])
        result = await session.execute(stmt)
        uni = result.scalar_one_or_none()
        if uni:
            uni.name = config["name"]
            uni.base_url = config["base_url"]
            uni.schedule_page_path = config["schedule_page_path"]
        else:
            uni = University(**config)
            session.add(uni)
            await session.flush()
        universities.append(uni)
    return universities


async def remove_university_by_code(session: AsyncSession, code: str) -> None:
    stmt = select(University).where(University.code == code)
    university = (await session.execute(stmt)).scalar_one_or_none()
    if not university:
        return

    specialty_ids = list(
        (
            await session.execute(
                select(Specialty.id).where(Specialty.university_id == university.id)
            )
        ).scalars().all()
    )
    if not specialty_ids:
        await session.delete(university)
        return

    source_ids = list(
        (
            await session.execute(
                select(ScheduleSource.id).where(ScheduleSource.specialty_id.in_(specialty_ids))
            )
        ).scalars().all()
    )
    if source_ids:
        await session.execute(
            delete(UserSubscription).where(UserSubscription.source_id.in_(source_ids))
        )
        await session.execute(delete(ScheduleCache).where(ScheduleCache.source_id.in_(source_ids)))
        await session.execute(delete(ScheduleSource).where(ScheduleSource.id.in_(source_ids)))

    await session.execute(delete(Specialty).where(Specialty.id.in_(specialty_ids)))
    await session.delete(university)


async def ensure_university(session: AsyncSession) -> University:
    universities = await ensure_universities(session)
    return universities[0]


async def get_university_by_id(session: AsyncSession, university_id: int) -> University | None:
    return await session.get(University, university_id)


def parse_rsreu_ref(pdf_path: str) -> tuple[int, int] | None:
    if not pdf_path.startswith("rsreu:"):
        return None
    parts = pdf_path.split(":")
    if len(parts) != 3:
        return None
    return int(parts[1]), int(parts[2])


def is_rsreu_source(pdf_path: str) -> bool:
    return pdf_path.startswith("rsreu:")


def resolve_schedule_for_view(
    schedule: dict | None,
    pdf_path: str,
    *,
    week_type: str | None = None,
    now: datetime | None = None,
    week_start: date | None = None,
) -> dict:
    if not schedule:
        return {}
    from parsers.rzgmu_dates import filter_weekly_schedule, monday_of, week_label

    moment = now or datetime.now()
    selected_week_start = week_start or monday_of(moment.date())

    if is_rzgmu_source(pdf_path):
        resolved = resolve_rzgmu_schedule(
            schedule,
            week_type=week_type,
            week_start=selected_week_start,
            now=now,
        )
        if schedule_is_calendar_format(resolved) and not any(
            isinstance(resolved.get(str(day)), list) and resolved.get(str(day))
            for day in range(7)
        ):
            # Calendar-only PDFs still get a week stamp for the switcher.
            from parsers.rzgmu_dates import week_label as _week_label

            resolved = dict(resolved)
            resolved.setdefault("__week__", {})
            resolved["__week__"] = {
                **resolved.get("__week__", {}),
                "calendar_start": selected_week_start.isoformat(),
                "calendar_label": _week_label(selected_week_start),
            }
            return resolved
        return filter_weekly_schedule(resolved, selected_week_start)

    if is_rsreu_source(pdf_path):
        from parsers.rsreu_html import schedule_from_weeks_cache
        from parsers.rzgmu_dates import monday_of as _monday_of

        selected = schedule_from_weeks_cache(
            schedule,
            week_start,
            today=moment.date(),
        ) or schedule
        result = {key: value for key, value in selected.items() if key != "__weeks__"}
        week_meta = dict(result.get("__week__", {}))
        cached_week_date = week_meta.get("date")
        if cached_week_date:
            try:
                label_start = _monday_of(date.fromisoformat(str(cached_week_date)))
            except ValueError:
                label_start = week_start or selected_week_start
        elif week_meta.get("calendar_start"):
            try:
                label_start = date.fromisoformat(str(week_meta["calendar_start"]))
            except ValueError:
                label_start = week_start or selected_week_start
        else:
            label_start = week_start or selected_week_start
        week_meta["calendar_start"] = label_start.isoformat()
        week_meta["calendar_label"] = week_label(label_start)
        result["__week__"] = week_meta
        return result

    return schedule


def schedule_has_week_types(schedule: dict | None) -> bool:
    if not schedule:
        return False
    return bool(schedule.get("__meta__", {}).get("has_week_types"))


def schedule_is_calendar_format(schedule: dict | None) -> bool:
    if not schedule:
        return False
    return schedule.get("__meta__", {}).get("format") == "calendar"


MONTH_DISPLAY = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def format_calendar_schedule(schedule: dict) -> str:
    calendar = schedule.get("__calendar__", {})
    if not calendar:
        cyclic = schedule.get("__cyclic__", [])
        if cyclic:
            lines = ["<b>Расписание по календарю</b>"]
            for item in cyclic:
                line = f"• {item['subject']}"
                if item.get("extra"):
                    line += f" — <i>{item['extra']}</i>"
                if item.get("start"):
                    line += f" ({item['start']}-{item['end']})"
                lines.append(line)
            return "\n".join(lines)
        return "Расписание пусто"

    lines = ["<b>Расписание по календарю</b>"]
    current_month = None
    for key in sorted(calendar.keys(), key=lambda item: (int(item.split("-")[0]), int(item.split("-")[1]))):
        month_num, day_num = (int(part) for part in key.split("-"))
        if month_num != current_month:
            current_month = month_num
            lines.append(f"\n<b>{MONTH_DISPLAY.get(month_num, str(month_num))}</b>")
        for item in calendar[key]:
            line = f"• {day_num} — {item['subject']}"
            if item.get("start"):
                line += f" <b>{item['start']}-{item['end']}</b>"
            if item.get("extra"):
                line += f" — <i>{item['extra']}</i>"
            lines.append(line)
    return "\n".join(lines)


def is_portal_source(pdf_path: str) -> bool:
    return is_rsreu_source(pdf_path)


def display_group_name(source: ScheduleSource, group_number: int) -> str:
    if is_portal_source(source.pdf_path):
        return source.variant_name
    return str(group_number)


async def upsert_specialty(
    session: AsyncSession, university_id: int, code: str, name: str
) -> Specialty:
    stmt = select(Specialty).where(
        Specialty.university_id == university_id, Specialty.code == code
    )
    result = await session.execute(stmt)
    specialty = result.scalar_one_or_none()
    if specialty:
        specialty.name = name
        return specialty

    specialty = Specialty(university_id=university_id, code=code, name=name)
    session.add(specialty)
    await session.flush()
    return specialty


async def upsert_schedule_source(
    session: AsyncSession,
    specialty_id: int,
    course_number: int,
    variant_name: str,
    pdf_path: str,
) -> ScheduleSource:
    stmt = select(ScheduleSource).where(
        ScheduleSource.specialty_id == specialty_id,
        ScheduleSource.pdf_path == pdf_path,
    )
    result = await session.execute(stmt)
    source = result.scalar_one_or_none()
    if source:
        source.variant_name = variant_name
        source.course_number = course_number
        return source

    source = ScheduleSource(
        specialty_id=specialty_id,
        course_number=course_number,
        variant_name=variant_name,
        pdf_path=pdf_path,
    )
    session.add(source)
    await session.flush()
    return source


async def dedupe_schedule_sources(session: AsyncSession, university_id: int) -> None:
    stmt = (
        select(ScheduleSource.id, ScheduleSource.pdf_path)
        .join(Specialty)
        .where(Specialty.university_id == university_id)
        .order_by(ScheduleSource.pdf_path, ScheduleSource.id)
    )
    duplicate_ids: list[int] = []
    seen_paths: set[str] = set()
    for source_id, pdf_path in (await session.execute(stmt)).all():
        if pdf_path in seen_paths:
            duplicate_ids.append(source_id)
        else:
            seen_paths.add(pdf_path)

    if not duplicate_ids:
        return

    await session.execute(
        delete(ScheduleCache).where(ScheduleCache.source_id.in_(duplicate_ids))
    )
    await session.execute(
        delete(ScheduleSource).where(ScheduleSource.id.in_(duplicate_ids))
    )


async def save_schedule_cache(
    session: AsyncSession, source_id: int, group_schedules: dict[int, dict]
) -> None:
    for group_number, schedule_data in group_schedules.items():
        stmt = insert(ScheduleCache).values(
            source_id=source_id,
            group_number=group_number,
            schedule_data=schedule_data,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "group_number"],
            set_={"schedule_data": schedule_data, "parsed_at": datetime.now(timezone.utc)},
        )
        await session.execute(stmt)


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None, first_name: str | None
) -> User:
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        user.username = username
        user.first_name = first_name
        return user

    user = User(telegram_id=telegram_id, username=username, first_name=first_name)
    session.add(user)
    await session.flush()
    return user


async def set_user_subscription(
    session: AsyncSession, user_id: int, source_id: int, group_number: int
) -> UserSubscription:
    stmt = select(UserSubscription).where(UserSubscription.user_id == user_id)
    result = await session.execute(stmt)
    sub = result.scalar_one_or_none()
    if sub:
        sub.source_id = source_id
        sub.group_number = group_number
        sub.notifications_enabled = True
        return sub

    sub = UserSubscription(user_id=user_id, source_id=source_id, group_number=group_number)
    session.add(sub)
    await session.flush()
    return sub


async def get_user_subscription(session: AsyncSession, telegram_id: int) -> UserSubscription | None:
    stmt = (
        select(UserSubscription)
        .join(User)
        .where(User.telegram_id == telegram_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_group_schedule(
    session: AsyncSession, source_id: int, group_number: int
) -> dict | None:
    stmt = select(ScheduleCache).where(
        ScheduleCache.source_id == source_id,
        ScheduleCache.group_number == group_number,
    )
    result = await session.execute(stmt)
    cache = result.scalar_one_or_none()
    return cache.schedule_data if cache else None


async def get_available_groups(session: AsyncSession, source_id: int) -> list[int]:
    stmt = (
        select(ScheduleCache.group_number)
        .where(ScheduleCache.source_id == source_id)
        .order_by(ScheduleCache.group_number)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def notification_was_sent(session: AsyncSession, user_id: int, key: str) -> bool:
    stmt = select(NotificationLog.id).where(
        NotificationLog.user_id == user_id,
        NotificationLog.notification_key == key,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def mark_notification_sent(session: AsyncSession, user_id: int, key: str) -> None:
    stmt = insert(NotificationLog).values(user_id=user_id, notification_key=key)
    stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "notification_key"])
    await session.execute(stmt)


def parse_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


SUBGROUP_SUFFIX_RE = re.compile(
    r",\s*(?:п\.?\s*г\.?|п/г|подгр\.?)\s*\d+",
    re.IGNORECASE,
)
AUDITORIUM_RE = re.compile(r",\s*ауд\.", re.IGNORECASE)


def _normalize_subject(subject: str) -> str:
    return SUBGROUP_SUFFIX_RE.sub("", subject).strip().rstrip(",")


def _split_extra(extra: str) -> tuple[str, str]:
    if not extra:
        return "", ""
    match = AUDITORIUM_RE.search(extra)
    if not match:
        return extra.strip(), ""
    teacher = extra[: match.start()].strip().rstrip(",")
    location = extra[match.start() + 1 :].strip()
    return teacher, location


def _merge_extra_parts(parts: list[str]) -> str:
    teachers: list[str] = []
    locations: list[str] = []
    for extra in parts:
        teacher, location = _split_extra(extra)
        if teacher and teacher not in teachers:
            teachers.append(teacher)
        if location and location not in locations:
            locations.append(location)

    if not teachers and not locations:
        return ""

    line = " / ".join(teachers)
    if locations:
        location = locations[0] if len(locations) == 1 else " / ".join(locations)
        line = f"{line}, {location}" if line else location
    return line


def merge_subgroup_lessons(lessons: list[Lesson]) -> list[Lesson]:
    grouped: dict[tuple[str, str, str], list[Lesson]] = {}
    for lesson in lessons:
        key = (lesson.start, lesson.end, _normalize_subject(lesson.subject))
        grouped.setdefault(key, []).append(lesson)

    merged: list[Lesson] = []
    for (start, end, subject), group in grouped.items():
        if len(group) == 1:
            lesson = group[0]
            merged.append(
                Lesson(
                    start=lesson.start,
                    end=lesson.end,
                    subject=_normalize_subject(lesson.subject),
                    extra=lesson.extra,
                )
            )
            continue

        merged.append(
            Lesson(
                start=start,
                end=end,
                subject=subject,
                extra=_merge_extra_parts([lesson.extra for lesson in group]),
            )
        )

    merged.sort(key=lambda item: item.start)
    return merged


def _schedule_timezone(timezone: str | None) -> ZoneInfo:
    return ZoneInfo(timezone or "Europe/Moscow")


def _lesson_is_ongoing(
    day_index: int,
    start: str,
    end: str,
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> bool:
    if not start or not end:
        return False

    moment = now or datetime.now(_schedule_timezone(timezone))
    if moment.weekday() != day_index:
        return False

    start_h, start_m = parse_time(start)
    end_h, end_m = parse_time(end)
    now_minutes = moment.hour * 60 + moment.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    return start_minutes <= now_minutes < end_minutes


def lessons_from_day_data(day_data: list[dict]) -> list[Lesson]:
    lessons: list[Lesson] = []
    type_prefixes = ("лек", "Лек.", "Лаб.", "Упр.", "Практ.", "Сем.", "Конс.", "Зач.", "Экз.")
    lecture_prefix_re = re.compile(r"^(лек\.|лекции|лек)\s+", re.IGNORECASE)
    for item in day_data:
        subject = str(item.get("subject", "") or "")
        lecture_match = lecture_prefix_re.match(subject.strip())
        if lecture_match:
            subject = f"лек {subject.strip()[lecture_match.end():]}".strip()
        lesson_type = str(item.get("type", "") or "").strip()
        if lesson_type and not any(subject.startswith(prefix) for prefix in type_prefixes):
            if lesson_type.lower().startswith("лек"):
                lesson_type = "лек"
            subject = f"{lesson_type} {subject}".strip()
        lessons.append(
            Lesson(
                start=item["start"],
                end=item["end"],
                subject=subject,
                extra=item.get("extra", ""),
            )
        )
    return merge_subgroup_lessons(lessons)


def _lesson_time_range(lesson: Lesson) -> str:
    if lesson.start and lesson.end:
        return f"{lesson.start}-{lesson.end}"
    return ""


def _lesson_blockquote_body(lesson: Lesson, *, ongoing: bool = False) -> str:
    subject = lesson.subject
    if ongoing:
        subject = f"{subject} (сейчас идёт)"
    if lesson.extra:
        return f"{subject}\n{lesson.extra}"
    return subject


def format_lesson_highlight(lesson: Lesson, *, ongoing: bool = False) -> str:
    lines = []
    time_range = _lesson_time_range(lesson)
    if time_range:
        lines.append(f"<b>{time_range}</b>")
    lines.append(f"<blockquote>{_lesson_blockquote_body(lesson, ongoing=ongoing)}</blockquote>")
    return "\n".join(lines)


def format_lessons_list(lessons: list[Lesson]) -> str:
    if not lessons:
        return "Занятий нет"

    lines = []
    for lesson in sorted(lessons, key=lambda item: item.start):
        lines.append(format_lesson_highlight(lesson))
    return "\n".join(lines)


def format_schedule_header(
    source: ScheduleSource,
    group_number: int,
    *,
    week_type_label: str | None = None,
) -> str:
    university = source.specialty.university
    group_label = display_group_name(source, group_number)
    course_part = f" · {source.course_number} курс" if source.course_number else ""
    line = f"{source.specialty.name}{course_part} · группа {group_label}"
    if week_type_label and (is_rsreu_source(source.pdf_path) or is_rzgmu_source(source.pdf_path)):
        line += f" · <i>{week_type_label}</i>"
    return f"<b>{university.name}</b>\n{line}"


def format_day_schedule_body(
    day_index: int,
    lessons: list[Lesson],
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    lines = [f"<b>{DAY_NAMES[day_index]}</b>"]
    if not lessons:
        lines.append("<blockquote>Занятий нет</blockquote>")
        return "\n".join(lines)

    sorted_lessons = sorted(lessons, key=lambda item: item.start)
    for lesson in sorted_lessons:
        ongoing = _lesson_is_ongoing(
            day_index,
            lesson.start,
            lesson.end,
            timezone=timezone,
            now=now,
        )
        lines.append(format_lesson_highlight(lesson, ongoing=ongoing))
    return "\n".join(lines)


def format_day_schedule_message(
    header: str,
    day_index: int,
    lessons: list[Lesson],
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    return f"{header}\n\n{format_day_schedule_body(day_index, lessons, timezone, now)}"


def format_week_schedule_message(
    header: str,
    schedule: dict,
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    return f"{header}\n\n{format_week_schedule(schedule, timezone, now)}"


def format_day_schedule(
    day_index: int,
    lessons: list[Lesson],
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    return format_day_schedule_body(day_index, lessons, timezone, now)


def format_week_schedule(
    schedule: dict,
    timezone: str | None = None,
    now: datetime | None = None,
) -> str:
    # Prefer the already-resolved Mon–Sun view (used by the week switcher).
    # Only dump the raw calendar listing when day slots were not filled.
    has_resolved_days = any(
        isinstance(schedule.get(str(day)), list) and schedule.get(str(day))
        for day in range(7)
    )
    if has_resolved_days:
        parts = []
        for day_index in range(7):
            day_key = str(day_index)
            lessons = lessons_from_day_data(schedule.get(day_key, []))
            if lessons:
                parts.append(format_day_schedule(day_index, lessons, timezone, now))
        if parts:
            return "\n\n".join(parts)

    if schedule.get("__calendar__") or schedule.get("__meta__", {}).get("format") == "calendar":
        return format_calendar_schedule(schedule)

    cyclic = schedule.get("__cyclic__", [])
    if cyclic and not any(str(day) in schedule for day in range(7)):
        return format_calendar_schedule(schedule)

    return "Расписание пусто"


def notification_key(kind: str, date: datetime, extra: str) -> str:
    return f"{kind}:{date.date().isoformat()}:{extra}"


def current_month_start(tz_name: str) -> datetime:
    now = datetime.now(ZoneInfo(tz_name))
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def touch_user_activity(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    user = await get_or_create_user(session, telegram_id, username, first_name)
    user.last_callback_at = datetime.now(timezone.utc)


async def count_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


async def count_new_users_since(session: AsyncSession, since: datetime) -> int:
    result = await session.execute(
        select(func.count()).select_from(User).where(User.created_at >= since)
    )
    return int(result.scalar_one())


async def count_active_users_since(session: AsyncSession, since: datetime) -> int:
    result = await session.execute(
        select(func.count()).select_from(User).where(User.last_callback_at >= since)
    )
    return int(result.scalar_one())


def _user_search_filter(query: str):
    raw = query.strip().lstrip("@")
    if not raw:
        return None
    like = f"%{raw}%"
    filters = [
        User.username.ilike(like),
        User.first_name.ilike(like),
    ]
    if raw.isdigit():
        filters.append(User.telegram_id == int(raw))
    return or_(*filters)


async def list_users_page(
    session: AsyncSession,
    *,
    offset: int,
    limit: int,
    query: str | None = None,
) -> tuple[list[User], int]:
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)
    search = _user_search_filter(query) if query else None
    if search is not None:
        stmt = stmt.where(search)
        count_stmt = count_stmt.where(search)
    total = int((await session.execute(count_stmt)).scalar_one())
    result = await session.execute(
        stmt.order_by(User.created_at.desc(), User.id.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all()), total

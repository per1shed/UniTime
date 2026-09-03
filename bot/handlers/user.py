import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot import emoji as e
from bot.config import get_settings
from bot.messages import entry_text, loading_text, main_menu_text, step_text, user_nick
from bot.db.models import ScheduleSource, Specialty, University, User
from bot.db.repository import (
    ensure_universities,
    format_schedule_header,
    format_week_schedule_message,
    get_available_groups,
    get_or_create_user,
    get_university_by_id,
    get_user_subscription,
    is_portal_source,
    is_rsreu_source,
    is_rzgmu_source,
    parse_rsreu_ref,
    resolve_schedule_for_view,
    set_user_subscription,
)
from bot.utils.course import effective_course_number
from bot.keyboards.inline import (
    courses_keyboard,
    schedule_nav_keyboard,
    faculties_for_course_keyboard,
    groups_keyboard,
    main_menu_keyboard,
    notifications_keyboard,
    rsreu_groups_keyboard,
    specialties_keyboard,
    university_courses_keyboard,
    variants_keyboard,
)
from parsers.rzgmu_dates import monday_of, shift_week
from parsers.rzgmu_faculties import RZGMU_FACULTIES, faculty_for_code
from parsers.rzgmu_week import week_type_for_date
from bot.services.keyboard_tracker import (
    bind_keyboard,
    get_keyboard_tracker,
    hydrate_tracker,
    get_tracker_session_factory,
)
from bot.services.sync import ScheduleSyncService
from bot.states.flow import FlowStorage

logger = logging.getLogger(__name__)

router = Router()


def _nick(user) -> str | None:
    return user_nick(user.first_name, user.username)


def _schedule_header(
    source: ScheduleSource,
    group_number: int,
    schedule: dict | None = None,
) -> str:
    week_label = None
    if schedule:
        week_meta = schedule.get("__week__", {})
        calendar = week_meta.get("calendar_label")
        type_label = week_meta.get("type_label")
        if calendar and type_label:
            week_label = f"{calendar} · {type_label}"
        else:
            week_label = calendar or type_label
        week_label = _with_current_week_mark(week_label, week_meta.get("calendar_start"))
    return format_schedule_header(source, group_number, week_type_label=week_label)


def _parse_week_nav_callback(data: str) -> tuple[str, int, int, date, bool]:
    parts = data.split(":")
    include_back = len(parts) > 4 and parts[4] == "back"
    return parts[0], int(parts[1]), int(parts[2]), date.fromisoformat(parts[3]), include_back


def _is_current_week(week_start: date | str | None) -> bool:
    if not week_start:
        return False
    if isinstance(week_start, str):
        try:
            week_start = date.fromisoformat(week_start)
        except ValueError:
            return False
    today = datetime.now(ZoneInfo(get_settings().timezone)).date()
    return monday_of(week_start) == monday_of(today)


def _with_current_week_mark(label: str | None, week_start: date | str | None) -> str | None:
    if not label:
        return label
    if _is_current_week(week_start) and "(сейчас)" not in label:
        return f"{label} (сейчас)"
    return label


def _calendar_week_from_schedule(schedule: dict | None) -> tuple[str | None, str | None]:
    if not schedule:
        return None, None
    week_meta = schedule.get("__week__", {})
    return week_meta.get("calendar_start"), week_meta.get("calendar_label")


def _show_week_nav(source: ScheduleSource, schedule: dict | None) -> bool:
    if is_rsreu_source(source.pdf_path):
        return True
    return is_rzgmu_source(source.pdf_path)


def _week_type_for_view(source: ScheduleSource, week_start: date | None) -> str | None:
    if week_start and is_rzgmu_source(source.pdf_path):
        return week_type_for_date(week_start)
    return None


async def _load_view_schedule(
    session: AsyncSession,
    sync_service: ScheduleSyncService,
    source: ScheduleSource,
    source_id: int,
    group_number: int,
    *,
    week_start: date | None = None,
) -> dict | None:
    week_type = _week_type_for_view(source, week_start)
    if is_rsreu_source(source.pdf_path):
        schedule = await sync_service.load_rsreu_schedule(
            session,
            source_id,
            group_number,
            week_start=week_start,
        )
    else:
        from bot.db.repository import get_group_schedule

        schedule = await get_group_schedule(session, source_id, group_number)
    if not schedule:
        return None
    return resolve_schedule_for_view(
        schedule,
        source.pdf_path,
        week_type=week_type,
        week_start=week_start,
    )


def _schedule_nav_for(
    source: ScheduleSource,
    source_id: int,
    group_number: int,
    schedule: dict | None = None,
    *,
    specialty_id: int | None = None,
    include_back: bool = False,
):
    if is_rzgmu_source(source.pdf_path):
        include_back = False
    week_start, week_label_text = _calendar_week_from_schedule(schedule)
    week_label_text = _with_current_week_mark(week_label_text, week_start)
    return schedule_nav_keyboard(
        source_id,
        group_number,
        include_back=include_back,
        back_callback=f"back:groups:{source_id}:{specialty_id}" if specialty_id else None,
        show_week_nav=_show_week_nav(source, schedule),
        week_start=week_start,
        week_label_text=week_label_text,
    )


async def _render_schedule_view(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
    source_id: int,
    group_number: int,
    *,
    week_start: date | None = None,
    include_back: bool = False,
    specialty_id: int | None = None,
) -> None:
    async with session_factory() as session:
        source = await _load_schedule_source(session, source_id)
        if not source:
            await callback.answer("Расписание не найдено.", show_alert=True)
            return
        schedule = await _load_view_schedule(
            session,
            sync_service,
            source,
            source_id,
            group_number,
            week_start=week_start,
        )
        if not schedule:
            await callback.answer("Расписание не найдено.", show_alert=True)
            return

    tz = get_settings().timezone
    header = _schedule_header(source, group_number, schedule)
    text = format_week_schedule_message(header, schedule, tz)
    keyboard = _schedule_nav_for(
        source,
        source_id,
        group_number,
        schedule,
        specialty_id=specialty_id or source.specialty_id,
        include_back=include_back,
    )
    await _edit_or_answer(callback, text, keyboard)


async def _load_schedule_source(session: AsyncSession, source_id: int) -> ScheduleSource | None:
    stmt = (
        select(ScheduleSource)
        .options(selectinload(ScheduleSource.specialty).selectinload(Specialty.university))
        .where(ScheduleSource.id == source_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _edit_or_answer(callback: CallbackQuery, text: str, reply_markup) -> None:
    if not callback.message:
        await callback.answer(text, show_alert=True)
        return

    tracker = get_keyboard_tracker()
    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    await hydrate_tracker(tracker, get_tracker_session_factory(), chat_id)
    if tracker.latest_id(chat_id) is None:
        tracker.remember_existing(chat_id, message_id)

    if tracker.is_latest(chat_id, message_id):
        await tracker.clear_old(callback.bot, chat_id, except_message_id=message_id)
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        await bind_keyboard(chat_id, message_id, text=text, reply_markup=reply_markup)
        return

    await tracker.clear_old(callback.bot, chat_id)
    await tracker.strip_message(callback.bot, chat_id, message_id)
    sent = await callback.bot.send_message(chat_id, text, reply_markup=reply_markup)
    await bind_keyboard(chat_id, sent.message_id, text=text, reply_markup=reply_markup)


async def _send_with_keyboard(message: Message, text: str, reply_markup) -> None:
    tracker = get_keyboard_tracker()
    chat_id = message.chat.id
    await hydrate_tracker(tracker, get_tracker_session_factory(), chat_id)
    await tracker.clear_old(message.bot, chat_id)
    sent = await message.answer(text, reply_markup=reply_markup)
    await bind_keyboard(chat_id, sent.message_id, text=text, reply_markup=reply_markup)


async def _load_universities(session: AsyncSession) -> list[University]:
    stmt = select(University).order_by(University.name)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _load_faculty_specialties(
    session: AsyncSession, university_id: int, faculty_key: str
) -> list[Specialty]:
    faculty = next((item for item in RZGMU_FACULTIES if item.key == faculty_key), None)
    if not faculty:
        return []
    stmt = (
        select(Specialty)
        .where(
            Specialty.university_id == university_id,
            Specialty.code.in_(faculty.specialty_codes),
        )
        .order_by(Specialty.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _filter_sources_by_course(
    sources: list[ScheduleSource], course_number: int, university_code: str
) -> list[ScheduleSource]:
    return [
        source
        for source in sources
        if effective_course_number(
            source.course_number, source.variant_name, university_code
        )
        == course_number
    ]


def _pick_schedule_source(sources: list[ScheduleSource]) -> ScheduleSource:
    for source in sources:
        name = source.variant_name.lower()
        if "занят" in name and "лек" not in name:
            return source
    for source in sources:
        if "занят" in source.variant_name.lower():
            return source
    return sources[0]


async def _load_university_courses(
    session: AsyncSession, university_id: int, university_code: str
) -> list[int]:
    stmt = (
        select(ScheduleSource.course_number, ScheduleSource.variant_name)
        .join(Specialty)
        .where(Specialty.university_id == university_id)
    )
    courses: set[int] = set()
    for course_number, variant_name in (await session.execute(stmt)).all():
        effective = effective_course_number(
            course_number, variant_name, university_code
        )
        if effective:
            courses.add(effective)
    return sorted(courses)


async def _load_faculties_for_course(
    session: AsyncSession,
    university_id: int,
    university_code: str,
    course_number: int,
) -> list:
    available = []
    for faculty in RZGMU_FACULTIES:
        stmt = select(Specialty.id).where(
            Specialty.university_id == university_id,
            Specialty.code.in_(faculty.specialty_codes),
        )
        specialty_ids = list((await session.execute(stmt)).scalars().all())
        if not specialty_ids:
            continue

        source_stmt = select(ScheduleSource).where(
            ScheduleSource.specialty_id.in_(specialty_ids)
        )
        sources = list((await session.execute(source_stmt)).scalars().all())
        if _filter_sources_by_course(sources, course_number, university_code):
            available.append(faculty)
    return available


async def _show_courses_for_university(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    university_id: int,
) -> None:
    async with session_factory() as session:
        university = await get_university_by_id(session, university_id)
        code = university.code if university else "rzgmu"
        course_numbers = await _load_university_courses(session, university_id, code)

    if not course_numbers:
        await callback.answer("Курсы не найдены. Идёт синхронизация...", show_alert=True)
        return

    context = f"<b>{university.name}</b>" if university else None
    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите курс", context),
        university_courses_keyboard(university_id, course_numbers),
    )


async def _show_faculties_for_course(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    university_id: int,
    course_number: int,
) -> None:
    async with session_factory() as session:
        university = await get_university_by_id(session, university_id)
        code = university.code if university else "rzgmu"
        faculties = await _load_faculties_for_course(
            session, university_id, code, course_number
        )

    if not faculties:
        await callback.answer("Факультеты для этого курса не найдены.", show_alert=True)
        return

    context = f"<b>{course_number} курс</b>"
    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите факультет", context),
        faculties_for_course_keyboard(university_id, course_number, faculties),
    )


async def _show_rzgmu_groups_for_faculty_course(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
    university_id: int,
    faculty_key: str,
    course_number: int,
) -> None:
    async with session_factory() as session:
        specialties = await _load_faculty_specialties(session, university_id, faculty_key)
        if not specialties:
            await callback.answer("Направления не найдены.", show_alert=True)
            return

        specialty_ids = [item.id for item in specialties]
        stmt = select(ScheduleSource).where(ScheduleSource.specialty_id.in_(specialty_ids))
        sources = _filter_sources_by_course(
            list((await session.execute(stmt)).scalars().all()),
            course_number,
            "rzgmu",
        )

    if not sources:
        await callback.answer("Группы для этого курса не найдены.", show_alert=True)
        return

    source = _pick_schedule_source(sources)
    back_callback = f"back:fac:{university_id}:{course_number}"
    await _show_groups(
        callback,
        session_factory,
        sync_service,
        source.id,
        source.specialty_id,
        back_callback=back_callback,
    )


async def _show_portal_groups_for_course(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    university_id: int,
    specialty_id: int,
    course_number: int,
    page: int = 0,
    back_callback: str | None = None,
    title: str | None = None,
) -> None:
    async with session_factory() as session:
        specialty = await session.get(Specialty, specialty_id)
        university = (
            await get_university_by_id(session, specialty.university_id)
            if specialty
            else None
        )
        stmt = (
            select(ScheduleSource)
            .where(ScheduleSource.specialty_id == specialty_id)
            .order_by(ScheduleSource.variant_name)
        )
        uni_code = university.code if university else "rsreu"
        sources = _filter_sources_by_course(
            list((await session.execute(stmt)).scalars().all()),
            course_number,
            uni_code,
        )

    if not sources:
        await callback.answer("Группы не найдены. Идёт синхронизация...", show_alert=True)
        return

    display_title = title or (specialty.name if specialty else None)
    context_parts = [f"<b>{course_number} курс</b>"]
    if display_title and display_title != "Группы":
        context_parts.append(f"<b>{display_title}</b>")
    context = "\n".join(context_parts)

    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите группу", context),
        rsreu_groups_keyboard(
            university_id,
            specialty_id,
            sources,
            page=page,
            back_callback=back_callback or f"back:fac:{university_id}:{course_number}",
        ),
    )


async def _portal_back_callback(
    session: AsyncSession,
    university_id: int,
) -> str:
    return f"back:fac:{university_id}"


async def _show_portal_groups(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    university_id: int,
    specialty_id: int,
    page: int = 0,
    back_callback: str | None = None,
    title: str | None = None,
) -> None:
    async with session_factory() as session:
        stmt = (
            select(ScheduleSource)
            .where(ScheduleSource.specialty_id == specialty_id)
            .order_by(ScheduleSource.variant_name)
        )
        sources = list((await session.execute(stmt)).scalars().all())
        specialty = await session.get(Specialty, specialty_id)
        if back_callback is None:
            back_callback = await _portal_back_callback(session, university_id)

    if not sources:
        await callback.answer("Группы не найдены. Идёт синхронизация...", show_alert=True)
        return

    display_title = title or (specialty.name if specialty else None)
    context = f"<b>{display_title}</b>" if display_title and display_title != "Группы" else None
    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите группу", context),
        rsreu_groups_keyboard(
            university_id,
            specialty_id,
            sources,
            page=page,
            back_callback=back_callback,
        ),
    )


async def _show_specialties(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    university_id: int,
    faculty_key: str,
) -> None:
    async with session_factory() as session:
        specialties = await _load_faculty_specialties(session, university_id, faculty_key)
    if not specialties:
        await callback.answer("Направления не найдены.", show_alert=True)
        return
    faculty = next(item for item in RZGMU_FACULTIES if item.key == faculty_key)
    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите направление", f"<b>{faculty.name}</b>"),
        specialties_keyboard(specialties, university_id, faculty_key),
    )


async def _build_entry(
    session: AsyncSession,
    has_subscription: bool,
    nick: str | None,
    *,
    pick_schedule: bool = False,
) -> tuple[str, object]:
    if has_subscription and not pick_schedule:
        return main_menu_text(nick), main_menu_keyboard()

    universities = await ensure_universities(session)
    await session.flush()
    university = next((item for item in universities if item.code == "rzgmu"), None)
    if not university:
        if has_subscription:
            return loading_text(nick), main_menu_keyboard()
        return loading_text(nick), main_menu_keyboard()

    course_numbers = await _load_university_courses(session, university.id, "rzgmu")
    if not course_numbers:
        if has_subscription:
            return loading_text(nick), main_menu_keyboard()
        return loading_text(nick), main_menu_keyboard()

    return entry_text(nick), university_courses_keyboard(university.id, course_numbers)


async def _send_entry(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage | None = None,
    *,
    reset_flow: bool = False,
    ensure_user: bool = False,
    pick_schedule: bool = False,
) -> None:
    if reset_flow and flow_storage is not None:
        flow_storage.reset(message.from_user.id)

    async with session_factory() as session:
        if ensure_user:
            await get_or_create_user(
                session,
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name,
            )
        sub = await get_user_subscription(session, message.from_user.id)
        text, keyboard = await _build_entry(
            session,
            sub is not None,
            _nick(message.from_user),
            pick_schedule=pick_schedule,
        )
        await session.commit()

    await _send_with_keyboard(message, text, keyboard)


@router.message(CommandStart())
async def cmd_start(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    await _send_entry(message, session_factory, ensure_user=True)


@router.message(Command("change"))
async def cmd_change(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    await _send_entry(message, session_factory, flow_storage, reset_flow=True, pick_schedule=True)


@router.message(
    (F.text & ~F.text.startswith("/"))
    | F.photo
    | F.sticker
    | F.voice
    | F.video
    | F.document
    | F.audio
    | F.video_note
    | F.animation
    | F.contact
    | F.location
)
async def on_user_message(message: Message) -> None:
    return


@router.callback_query(F.data == "menu:main")
async def on_main_menu(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    flow_storage.reset(callback.from_user.id)
    async with session_factory() as session:
        sub = await get_user_subscription(session, callback.from_user.id)
        text, keyboard = await _build_entry(session, sub is not None, _nick(callback.from_user))
        await session.commit()
    await _edit_or_answer(callback, text, keyboard)
    await callback.answer()


@router.callback_query(F.data == "menu:change")
async def on_change_schedule(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    flow_storage.reset(callback.from_user.id)
    async with session_factory() as session:
        sub = await get_user_subscription(session, callback.from_user.id)
        text, keyboard = await _build_entry(
            session,
            sub is not None,
            _nick(callback.from_user),
            pick_schedule=True,
        )
        await session.commit()
    await _edit_or_answer(callback, text, keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("uni:"))
async def on_university_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    university_id = int(callback.data.split(":")[1])
    flow_storage.get(callback.from_user.id).university_id = university_id
    await _show_courses_for_university(callback, session_factory, university_id)
    await callback.answer()


@router.callback_query(F.data.startswith("ucrs:"))
async def on_university_course_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    _, university_id_str, course_str = callback.data.split(":")
    university_id = int(university_id_str)
    course_number = int(course_str)
    state = flow_storage.get(callback.from_user.id)
    state.university_id = university_id
    state.course_number = course_number

    await _show_faculties_for_course(callback, session_factory, university_id, course_number)
    await callback.answer()


@router.callback_query(F.data.startswith("fac:"))
async def on_faculty_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
    sync_service: ScheduleSyncService,
) -> None:
    _, university_id_str, faculty_key, course_str = callback.data.split(":")
    university_id = int(university_id_str)
    course_number = int(course_str)
    state = flow_storage.get(callback.from_user.id)
    state.university_id = university_id
    state.faculty_key = faculty_key
    state.course_number = course_number

    await _show_rzgmu_groups_for_faculty_course(
        callback,
        session_factory,
        sync_service,
        university_id,
        faculty_key,
        course_number,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("back:ucrs:"))
async def on_back_to_university_courses(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    university_id = int(callback.data.split(":")[2])
    flow_storage.get(callback.from_user.id).university_id = university_id
    await _show_courses_for_university(callback, session_factory, university_id)
    await callback.answer()


@router.callback_query(F.data.startswith("back:fac:"))
async def on_back_to_faculties(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    parts = callback.data.split(":")
    university_id = int(parts[2])
    course_number = int(parts[3])
    state = flow_storage.get(callback.from_user.id)
    state.university_id = university_id
    state.course_number = course_number
    await _show_faculties_for_course(callback, session_factory, university_id, course_number)
    await callback.answer()


@router.callback_query(F.data.startswith("back:uni:"))
async def on_back_to_universities(callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        sub = await get_user_subscription(session, callback.from_user.id)
        text, keyboard = await _build_entry(
            session,
            sub is not None,
            _nick(callback.from_user),
            pick_schedule=True,
        )
        await session.commit()
    await _edit_or_answer(callback, text, keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("spec:"))
async def on_specialty_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    specialty_id = int(callback.data.split(":")[1])
    flow_storage.get(callback.from_user.id).specialty_id = specialty_id

    async with session_factory() as session:
        stmt = (
            select(ScheduleSource.course_number)
            .where(ScheduleSource.specialty_id == specialty_id)
            .distinct()
            .order_by(ScheduleSource.course_number)
        )
        result = await session.execute(stmt)
        course_numbers = list(result.scalars().all())

    if not course_numbers:
        await callback.answer("Курсы не найдены. Идёт синхронизация...", show_alert=True)
        return

    await _show_courses_for_specialty(callback, session_factory, specialty_id)
    await callback.answer()


@router.callback_query(F.data.startswith("course:"))
async def on_course_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
    sync_service: ScheduleSyncService,
) -> None:
    _, specialty_id_str, course_number_str = callback.data.split(":")
    specialty_id = int(specialty_id_str)
    course_number = int(course_number_str)
    flow_storage.get(callback.from_user.id).specialty_id = specialty_id

    async with session_factory() as session:
        stmt = select(ScheduleSource).where(
            ScheduleSource.specialty_id == specialty_id,
            ScheduleSource.course_number == course_number,
        )
        result = await session.execute(stmt)
        sources = list(result.scalars().all())

    if not sources:
        await callback.answer("Расписание для курса не найдено.", show_alert=True)
        return

    if len(sources) == 1:
        await _show_groups(callback, session_factory, sync_service, sources[0].id, specialty_id)
        await callback.answer()
        return

    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите тип расписания"),
        variants_keyboard(specialty_id, course_number, sources),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("back:course:"))
async def on_back_to_courses(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    specialty_id = int(callback.data.split(":")[2])
    await _show_courses_for_specialty(callback, session_factory, specialty_id)
    await callback.answer()


async def _show_specialties_for_id(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    specialty_id: int,
) -> None:
    async with session_factory() as session:
        specialty = await session.get(Specialty, specialty_id)
        if not specialty:
            await callback.answer("Направление не найдено.", show_alert=True)
            return
        faculty = faculty_for_code(specialty.code)
        faculty_key = faculty.key if faculty else RZGMU_FACULTIES[-1].key
        specialties = await _load_faculty_specialties(
            session, specialty.university_id, faculty_key
        )

    if len(specialties) == 1:
        await _show_courses_for_university(callback, session_factory, specialty.university_id)
        return

    faculty = next(item for item in RZGMU_FACULTIES if item.key == faculty_key)
    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите направление", f"<b>{faculty.name}</b>"),
        specialties_keyboard(specialties, specialty.university_id, faculty_key),
    )


async def _show_courses_for_specialty(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    specialty_id: int,
) -> None:
    async with session_factory() as session:
        specialty = await session.get(Specialty, specialty_id)
        stmt = (
            select(ScheduleSource.course_number)
            .where(ScheduleSource.specialty_id == specialty_id)
            .distinct()
            .order_by(ScheduleSource.course_number)
        )
        course_numbers = list((await session.execute(stmt)).scalars().all())

    title = specialty.name if specialty else "Направление"
    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите курс", f"<b>{title}</b>"),
        courses_keyboard(specialty_id, course_numbers),
    )


@router.callback_query(F.data.startswith("back:spec_by_id:"))
async def on_back_to_specialties_by_id(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    specialty_id = int(callback.data.split(":")[2])
    await _show_specialties_for_id(callback, session_factory, specialty_id)
    await callback.answer()


@router.callback_query(F.data.startswith("back:variant:"))
async def on_back_to_variant(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parts = callback.data.split(":")
    source_id = int(parts[2])
    specialty_id = int(parts[3]) if len(parts) > 3 else None

    async with session_factory() as session:
        source = await session.get(ScheduleSource, source_id)
        if not source:
            await callback.answer("Источник не найден.", show_alert=True)
            return
        specialty_id = specialty_id or source.specialty_id
        stmt = select(ScheduleSource).where(
            ScheduleSource.specialty_id == source.specialty_id,
            ScheduleSource.course_number == source.course_number,
        )
        sources = list((await session.execute(stmt)).scalars().all())
        course_number = source.course_number

    if len(sources) == 1:
        await _show_courses_for_specialty(callback, session_factory, specialty_id)
        await callback.answer()
        return

    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите тип расписания"),
        variants_keyboard(specialty_id, course_number, sources),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("back:groups:"))
async def on_back_to_groups(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
    flow_storage: FlowStorage,
) -> None:
    _, source_id_str, specialty_id_str = callback.data.split(":")
    source_id = int(source_id_str)
    specialty_id = int(specialty_id_str)
    state = flow_storage.get(callback.from_user.id)

    async with session_factory() as session:
        source = await session.get(ScheduleSource, source_id)
        if source and is_portal_source(source.pdf_path):
            specialty = await session.get(Specialty, specialty_id)
            university_id = specialty.university_id if specialty else 0
            university = await get_university_by_id(session, university_id)

            course_number = state.course_number or effective_course_number(
                source.course_number,
                source.variant_name,
                university.code if university else None,
            )
            if course_number:
                await _show_portal_groups_for_course(
                    callback,
                    session_factory,
                    university_id,
                    specialty_id,
                    course_number,
                    back_callback=f"back:fac:{university_id}:{course_number}",
                )
            else:
                await _show_portal_groups(
                    callback,
                    session_factory,
                    university_id,
                    specialty_id,
                )
            await callback.answer()
            return

        specialty = await session.get(Specialty, specialty_id)
        university_id = specialty.university_id if specialty else 0

    back_callback = None
    if state.course_number and university_id:
        back_callback = f"back:fac:{university_id}:{state.course_number}"

    await _show_groups(
        callback,
        session_factory,
        sync_service,
        source_id,
        specialty_id,
        back_callback=back_callback,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rgpage:"))
async def on_rsreu_group_page(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    _, specialty_id_str, page_str = callback.data.split(":")
    specialty_id = int(specialty_id_str)
    course_number = flow_storage.get(callback.from_user.id).course_number
    async with session_factory() as session:
        specialty = await session.get(Specialty, specialty_id)
        university_id = specialty.university_id if specialty else 0
    if course_number:
        await _show_portal_groups_for_course(
            callback,
            session_factory,
            university_id,
            specialty_id,
            course_number,
            int(page_str),
        )
    else:
        await _show_portal_groups(
            callback,
            session_factory,
            university_id,
            specialty_id,
            int(page_str),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("rgroup:"))
async def on_portal_group_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    source_id = int(callback.data.split(":")[1])

    async with session_factory() as session:
        stmt = (
            select(ScheduleSource)
            .options(selectinload(ScheduleSource.specialty).selectinload(Specialty.university))
            .where(ScheduleSource.id == source_id)
        )
        source = (await session.execute(stmt)).scalar_one_or_none()
        if not source:
            await callback.answer("Группа не найдена.", show_alert=True)
            return

        if not is_rsreu_source(source.pdf_path):
            await callback.answer("Некорректная ссылка на расписание.", show_alert=True)
            return
        ref = parse_rsreu_ref(source.pdf_path)
        if not ref:
            await callback.answer("Некорректная ссылка на расписание.", show_alert=True)
            return
        _, group_id = ref

    await sync_service.ensure_source_cached(source_id)

    async with session_factory() as session:
        await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )
        user = (
            await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        ).scalar_one()
        await set_user_subscription(session, user.id, source_id, group_id)

        stmt = (
            select(ScheduleSource)
            .options(selectinload(ScheduleSource.specialty).selectinload(Specialty.university))
            .where(ScheduleSource.id == source_id)
        )
        source = (await session.execute(stmt)).scalar_one()
        schedule = await _load_view_schedule(
            session, sync_service, source, source_id, group_id
        )
        await session.commit()

    if not schedule:
        await callback.answer("Расписание группы не найдено.", show_alert=True)
        return

    await _render_schedule_view(
        callback,
        session_factory,
        sync_service,
        source_id,
        group_id,
    )
    await callback.answer("Расписание сохранено")


@router.callback_query(F.data.startswith("variant:"))
async def on_variant_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    source_id = int(callback.data.split(":")[1])
    async with session_factory() as session:
        source = await session.get(ScheduleSource, source_id)
        specialty_id = source.specialty_id if source else 0
    await _show_groups(callback, session_factory, sync_service, source_id, specialty_id)
    await callback.answer()


async def _show_groups(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
    source_id: int,
    specialty_id: int,
    page: int = 0,
    back_callback: str | None = None,
) -> None:
    async with session_factory() as session:
        groups = await get_available_groups(session, source_id)

    if not groups:
        await sync_service.ensure_source_cached(source_id)
        async with session_factory() as session:
            groups = await get_available_groups(session, source_id)

    if not groups:
        await callback.answer("Не удалось загрузить группы из PDF.", show_alert=True)
        return

    await _edit_or_answer(
        callback,
        step_text(_nick(callback.from_user), "Выберите номер группы"),
        groups_keyboard(source_id, groups, specialty_id, page=page, back_callback=back_callback),
    )


@router.callback_query(F.data.startswith("gpage:"))
async def on_group_page(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    _, source_id_str, page_str = callback.data.split(":")
    source_id = int(source_id_str)
    async with session_factory() as session:
        source = await session.get(ScheduleSource, source_id)
        specialty_id = source.specialty_id if source else 0
    await _show_groups(
        callback, session_factory, sync_service, source_id, specialty_id, int(page_str)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("group:"))
async def on_group_selected(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    _, source_id_str, group_number_str = callback.data.split(":")
    source_id = int(source_id_str)
    group_number = int(group_number_str)

    async with session_factory() as session:
        await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )
        user = (
            await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
        ).scalar_one()
        await set_user_subscription(session, user.id, source_id, group_number)

        stmt = (
            select(ScheduleSource)
            .options(selectinload(ScheduleSource.specialty).selectinload(Specialty.university))
            .where(ScheduleSource.id == source_id)
        )
        source = (await session.execute(stmt)).scalar_one()
        schedule = await _load_view_schedule(
            session, sync_service, source, source_id, group_number
        )
        await session.commit()

    if not schedule:
        await callback.answer("Расписание группы не найдено.", show_alert=True)
        return

    await _render_schedule_view(
        callback,
        session_factory,
        sync_service,
        source_id,
        group_number,
        specialty_id=source.specialty_id,
    )
    await callback.answer("Расписание сохранено")


@router.callback_query(F.data.regexp(r"^rw[pn]:"))
async def on_week_shift(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    prefix, source_id, group_number, week_start, include_back = _parse_week_nav_callback(
        callback.data
    )
    delta = -1 if prefix == "rwp" else 1

    async with session_factory() as session:
        source = await _load_schedule_source(session, source_id)
        if not source:
            await callback.answer("Расписание не найдено.", show_alert=True)
            return

        new_week = shift_week(week_start, delta)

    await _render_schedule_view(
        callback,
        session_factory,
        sync_service,
        source_id,
        group_number,
        week_start=new_week,
        include_back=include_back,
        specialty_id=source.specialty_id,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rwc:"))
async def on_week_label(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    _, source_id, group_number, week_start, _ = _parse_week_nav_callback(callback.data)
    async with session_factory() as session:
        source = await _load_schedule_source(session, source_id)
        if not source:
            await callback.answer("Расписание не найдено.", show_alert=True)
            return
        schedule = await _load_view_schedule(
            session,
            sync_service,
            source,
            source_id,
            group_number,
            week_start=week_start,
        )
    label = _calendar_week_from_schedule(schedule)[1] or week_start.strftime("%d.%m.%Y")
    await callback.answer(f"Неделя {label}", show_alert=False)


@router.callback_query(F.data == "menu:my")
async def on_my_schedule(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    sync_service: ScheduleSyncService,
) -> None:
    async with session_factory() as session:
        sub = await get_user_subscription(session, callback.from_user.id)
        if not sub:
            await callback.answer("Сначала выберите расписание.", show_alert=True)
            return

    await _render_schedule_view(
        callback,
        session_factory,
        sync_service,
        sub.source_id,
        sub.group_number,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:notifications")
async def on_notifications_menu(callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        sub = await get_user_subscription(session, callback.from_user.id)
        if not sub:
            await callback.answer("Сначала выберите расписание.", show_alert=True)
            return
        enabled = sub.notifications_enabled

    text = (
        f"{e.ce(e.BELL, '🔊')} <b>Уведомления</b>\n\n"
        "• В 6:00 — расписание на сегодня\n"
        "• В 21:00 — расписание на завтра\n"
        "• За 20 минут до каждой пары"
    )
    await _edit_or_answer(callback, text, notifications_keyboard(enabled))
    await callback.answer()


@router.callback_query(F.data == "notify:toggle")
async def on_notifications_toggle(callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        sub = await get_user_subscription(session, callback.from_user.id)
        if not sub:
            await callback.answer("Сначала выберите расписание.", show_alert=True)
            return
        sub.notifications_enabled = not sub.notifications_enabled
        enabled = sub.notifications_enabled
        await session.commit()

    await _edit_or_answer(
        callback,
        f"{e.ce(e.BELL, '🔊')} Уведомления переключены.",
        notifications_keyboard(enabled),
    )
    await callback.answer()


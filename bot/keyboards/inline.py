from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import ScheduleSource, Specialty, University
from bot import emoji as e


def _back_button(callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="Назад",
        callback_data=callback_data,
        icon_custom_emoji_id=e.ARROW_LEFT,
    )


def _btn(text: str, callback_data: str, icon: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=callback_data,
        icon_custom_emoji_id=icon,
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Моё расписание", "menu:my", e.CALENDAR))
    builder.row(_btn("Сменить группу", "menu:change", e.PICK))
    builder.row(_btn("Уведомления", "menu:notifications", e.BELL))
    return builder.as_markup()


def universities_keyboard(universities: list[University]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for university in universities:
        builder.row(
            InlineKeyboardButton(
                text=university.name,
                callback_data=f"uni:{university.id}",
            )
        )
    builder.row(_btn("Главное меню", "menu:main", e.USER))
    return builder.as_markup()


def university_courses_keyboard(university_id: int, course_numbers: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for course_number in sorted(course_numbers):
        builder.row(
            InlineKeyboardButton(
                text=f"{course_number} курс",
                callback_data=f"ucrs:{university_id}:{course_number}",
            )
        )
    builder.row(_back_button("menu:main"))
    return builder.as_markup()


def faculties_for_course_keyboard(
    university_id: int,
    course_number: int,
    faculties: list,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for faculty in faculties:
        builder.row(
            InlineKeyboardButton(
                text=faculty.name,
                callback_data=f"fac:{university_id}:{faculty.key}:{course_number}",
            )
        )
    builder.row(_back_button(f"back:ucrs:{university_id}"))
    return builder.as_markup()


def specialties_keyboard(
    specialties: list[Specialty],
    university_id: int,
    faculty_key: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for specialty in specialties:
        builder.row(
            InlineKeyboardButton(
                text=f"{specialty.name} ({specialty.code})",
                callback_data=f"spec:{specialty.id}",
            )
        )
    builder.row(_back_button(f"back:fac:{university_id}"))
    return builder.as_markup()


def faculties_keyboard(university_id: int, university_code: str) -> InlineKeyboardMarkup:
    from parsers.rzgmu_faculties import RZGMU_FACULTIES as faculties

    builder = InlineKeyboardBuilder()
    for faculty in faculties:
        builder.row(
            InlineKeyboardButton(
                text=faculty.name,
                callback_data=f"fac:{university_id}:{faculty.key}",
            )
        )
    builder.row(_back_button("menu:main"))
    return builder.as_markup()


def rsreu_groups_keyboard(
    university_id: int,
    specialty_id: int,
    sources: list[ScheduleSource],
    page: int = 0,
    page_size: int = 21,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * page_size
    chunk = sources[start : start + page_size]

    row: list[InlineKeyboardButton] = []
    for source in chunk:
        row.append(
            InlineKeyboardButton(
                text=source.variant_name,
                callback_data=f"rgroup:{source.id}",
            )
        )
        if len(row) == 3:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=" ",
                callback_data=f"rgpage:{specialty_id}:{page - 1}",
                icon_custom_emoji_id=e.ARROW_LEFT,
            )
        )
    if start + page_size < len(sources):
        nav.append(
            InlineKeyboardButton(
                text=" ",
                callback_data=f"rgpage:{specialty_id}:{page + 1}",
                icon_custom_emoji_id=e.ARROW_RIGHT,
            )
        )
    if nav:
        builder.row(*nav)

    builder.row(_back_button(back_callback or f"back:fac:{university_id}"))
    return builder.as_markup()


def courses_keyboard(specialty_id: int, course_numbers: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for course_number in sorted(course_numbers):
        builder.row(
            InlineKeyboardButton(
                text=f"{course_number} курс",
                callback_data=f"course:{specialty_id}:{course_number}",
            )
        )
    builder.row(_back_button(f"back:spec_by_id:{specialty_id}"))
    return builder.as_markup()


def variants_keyboard(specialty_id: int, course_number: int, sources: list[ScheduleSource]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for source in sources:
        builder.row(
            InlineKeyboardButton(
                text=source.variant_name,
                callback_data=f"variant:{source.id}",
            )
        )
    builder.row(_back_button(f"back:course:{specialty_id}"))
    return builder.as_markup()


def groups_keyboard(
    source_id: int,
    groups: list[int],
    specialty_id: int,
    page: int = 0,
    page_size: int = 21,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * page_size
    chunk = groups[start : start + page_size]

    row: list[InlineKeyboardButton] = []
    for group_number in chunk:
        row.append(
            InlineKeyboardButton(
                text=str(group_number),
                callback_data=f"group:{source_id}:{group_number}",
            )
        )
        if len(row) == 4:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=" ",
                callback_data=f"gpage:{source_id}:{page - 1}",
                icon_custom_emoji_id=e.ARROW_LEFT,
            )
        )
    if start + page_size < len(groups):
        nav.append(
            InlineKeyboardButton(
                text=" ",
                callback_data=f"gpage:{source_id}:{page + 1}",
                icon_custom_emoji_id=e.ARROW_RIGHT,
            )
        )
    if nav:
        builder.row(*nav)

    builder.row(_back_button(back_callback or f"back:variant:{source_id}:{specialty_id}"))
    return builder.as_markup()


def _week_nav_callback(
    prefix: str,
    source_id: int,
    group_number: int,
    week_start: str,
    *,
    include_back: bool = False,
) -> str:
    base = f"{prefix}:{source_id}:{group_number}:{week_start}"
    if include_back:
        return f"{base}:back"
    return base


def schedule_nav_keyboard(
    source_id: int,
    group_number: int,
    *,
    include_back: bool = False,
    back_callback: str | None = None,
    show_week_nav: bool = False,
    week_start: str | None = None,
    week_label_text: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if show_week_nav and week_start and week_label_text:
        builder.row(
            InlineKeyboardButton(
                text="‹",
                callback_data=_week_nav_callback(
                    "rwp", source_id, group_number, week_start, include_back=include_back
                ),
                icon_custom_emoji_id=e.ARROW_LEFT,
            ),
            InlineKeyboardButton(
                text=week_label_text,
                callback_data=_week_nav_callback("rwc", source_id, group_number, week_start),
            ),
            InlineKeyboardButton(
                text="›",
                callback_data=_week_nav_callback(
                    "rwn", source_id, group_number, week_start, include_back=include_back
                ),
                icon_custom_emoji_id=e.ARROW_RIGHT,
            ),
        )

    if include_back and back_callback:
        builder.row(_back_button(back_callback))
    builder.row(_btn("Главное меню", "menu:main", e.USER))
    return builder.as_markup()


def notification_week_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Расписание на неделю", "menu:my", e.CALENDAR))
    return builder.as_markup()


def notifications_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Включены" if enabled else "Выключены",
            callback_data="notify:toggle",
            icon_custom_emoji_id=e.CHECK if enabled else e.CROSS,
        )
    )
    builder.row(_back_button("menu:main"))
    return builder.as_markup()


def admin_panel_keyboard(
    *,
    page: int,
    pages: int,
    searching: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Поиск", "admin:search", e.PICK))
    if searching:
        builder.row(_btn("Сбросить поиск", "admin:home", e.CROSS))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("Назад", f"admin:list:{page - 1}", e.ARROW_LEFT))
    if pages > 1:
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="admin:noop")
        )
    if page + 1 < pages:
        nav.append(_btn("Вперёд", f"admin:list:{page + 1}", e.ARROW_RIGHT))
    if nav:
        builder.row(*nav)
    builder.row(_btn("Главное меню", "menu:main", e.USER))
    return builder.as_markup()


def admin_search_prompt_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn("Отмена", "admin:home", e.CROSS))
    return builder.as_markup()

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
    if university_code == "rsreu":
        from parsers.rsreu_faculties import RSREU_FACULTIES as faculties
    else:
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


def day_selector_keyboard(
    source_id: int,
    group_number: int,
    specialty_id: int,
    *,
    show_rsreu_week_switch: bool = False,
    rsreu_week_type: str | None = None,
) -> InlineKeyboardMarkup:
    return schedule_nav_keyboard(
        source_id,
        group_number,
        include_back=True,
        back_callback=f"back:groups:{source_id}:{specialty_id}",
        show_rsreu_week_switch=show_rsreu_week_switch,
        rsreu_week_type=rsreu_week_type,
    )


def _rsreu_week_callback(
    source_id: int,
    group_number: int,
    week_type: str,
    day_index: int | None = None,
) -> str:
    if day_index is None:
        return f"rwk:{source_id}:{group_number}:{week_type}"
    return f"rwk:{source_id}:{group_number}:{week_type}:{day_index}"


def schedule_nav_keyboard(
    source_id: int,
    group_number: int,
    *,
    selected_day: int | None = None,
    selected_week: bool = False,
    include_back: bool = False,
    back_callback: str | None = None,
    show_rsreu_week_switch: bool = False,
    rsreu_week_type: str | None = None,
) -> InlineKeyboardMarkup:
    days = [
        ("Пн", 0),
        ("Вт", 1),
        ("Ср", 2),
        ("Чт", 3),
        ("Пт", 4),
        ("Сб", 5),
        ("Вс", 6),
    ]
    builder = InlineKeyboardBuilder()

    def _day_button(label: str, day_index: int) -> InlineKeyboardButton:
        text = f"· {label} ·" if selected_day == day_index else label
        return InlineKeyboardButton(
            text=text,
            callback_data=f"day:{source_id}:{group_number}:{day_index}",
        )

    builder.row(*[_day_button(label, day_index) for label, day_index in days[:4]])
    builder.row(*[_day_button(label, day_index) for label, day_index in days[4:]])

    if show_rsreu_week_switch:
        num_text = "· Числитель ·" if rsreu_week_type == "numerator" else "Числитель"
        den_text = "· Знаменатель ·" if rsreu_week_type == "denominator" else "Знаменатель"
        builder.row(
            InlineKeyboardButton(
                text=num_text,
                callback_data=_rsreu_week_callback(
                    source_id, group_number, "numerator", selected_day
                ),
            ),
            InlineKeyboardButton(
                text=den_text,
                callback_data=_rsreu_week_callback(
                    source_id, group_number, "denominator", selected_day
                ),
            ),
        )

    week_text = "· Вся неделя ·" if selected_week else "Вся неделя"
    builder.row(
        _btn(week_text, f"week:{source_id}:{group_number}", e.CALENDAR),
    )
    if include_back and back_callback:
        builder.row(_back_button(back_callback))
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

from __future__ import annotations

import io
import asyncio
import re
from dataclasses import dataclass, field

import httpx
import pdfplumber

from parsers.rzgmu_http import create_rzgmu_client, fetch_response

DAY_PATTERNS = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
}

MONTHS = (
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
)

TIME_RANGE_RE = re.compile(r"(\d{1,2}\.\d{2})\s*-\s*(\d{1,2}\.\d{2})")
# Семестровая нагрузка вида (17х4ч.) — не показываем в боте.
HOURS_RE = re.compile(r"\(\d+[хx]\d+ч\.?\)?", re.IGNORECASE)
# Количество лекций в скобках перед датами: «Физика (4) 2,16/09».
LECTURE_COUNT_RE = re.compile(r"\(\d+\)")
LOCATION_START_RE = re.compile(
    r"(?:^|[\s,;])(?:Медико|спортивный|лекционн|ауд\.|корп\.|ул\.)",
    re.IGNORECASE,
)
LECTURE_DATES_RE = re.compile(
    r"\d{1,2}[/,][\d/,;\s-]+(?:кр\.\s*\d{1,2}/\d{1,2})?",
    re.IGNORECASE,
)
GROUP_NUM_RE = re.compile(r"^(\d+)\s*гр\.?", re.IGNORECASE)
# «1-10гр.», «11-22 гр.», «23-32ин.гр.» — потоки лекций.
STREAM_GROUPS_RE = re.compile(
    r"(\d+)\s*[-–—]\s*(\d+)\s*(?:ин\.\s*)?гр\.?",
    re.IGNORECASE,
)
GROUPS_RANGE_RE = re.compile(
    r"(\d+)\s*[-–—]\s*(\d+)\s*(?:ин\.\s*)?гр\.?",
    re.IGNORECASE,
)
DN_RE = re.compile(r"^д\s*/?\s*н$", re.IGNORECASE)
GROUP_TOKEN_RE = re.compile(r"^(\d+)([a-zа-яё]+)$", re.IGNORECASE)
PRACTICE_HOURS_RE = re.compile(r"\(\d+\s*дн", re.IGNORECASE)


@dataclass
class ParsedLesson:
    start: str
    end: str
    subject: str
    extra: str


@dataclass
class ParseContext:
    group_columns: list[tuple[int, int]] = field(default_factory=list)
    stream_columns: list[tuple[int, tuple[int, int]]] = field(default_factory=list)
    current_day: int | None = None
    week_type: str = "numerator"
    week_type_sections: int = 0
    subject_times: dict[str, tuple[str, str]] = field(default_factory=dict)
    default_groups: tuple[int, ...] = (1,)
    group_tokens: dict[str, int] = field(default_factory=dict)


def normalize_time(value: str) -> str:
    hour, minute = value.split(".")
    return f"{int(hour):02d}:{minute}"


def normalize_letters(value: str) -> str:
    return re.sub(r"[^a-zа-яё]", "", value.lower())


def detect_day_index(cell_value: str | None) -> int | None:
    if not cell_value:
        return None
    letters = normalize_letters(cell_value)
    if len(letters) < 4:
        return None
    for day_name, index in DAY_PATTERNS.items():
        reversed_name = day_name[::-1]
        if reversed_name in letters or day_name in letters:
            return index
        if len(reversed_name) >= 5 and reversed_name[:5] in letters:
            return index
        if len(day_name) >= 5 and day_name[:5] in letters:
            return index
    return None


def is_dn_cell(value: str | None) -> bool:
    if not value:
        return False
    compact = re.sub(r"\s+", "", str(value).lower())
    return bool(DN_RE.match(compact))


def parse_group_token(value: str, ctx: ParseContext) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if 1 <= number <= 50 else None
    token = text.lower()
    if GROUP_TOKEN_RE.match(token):
        if token not in ctx.group_tokens:
            ctx.group_tokens[token] = len(ctx.group_tokens) + 1
        return ctx.group_tokens[token]
    return None


def parse_date_token(value: str) -> str | None:
    compact = re.sub(r"[^\d/]", "", str(value or ""))
    match = re.search(r"(\d{1,2})/(\d{1,2})", compact)
    if match:
        first, second = int(match.group(1)), int(match.group(2))
    else:
        match = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", str(value or ""))
        if not match:
            return None
        first, second = int(match.group(1)), int(match.group(2))
    if first > 12:
        month, day = second, first
    elif second > 12:
        month, day = first, second
    else:
        month, day = second, first
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{month}-{day}"
    return None

def parse_group_number(cell: str | None) -> int | None:
    if not cell:
        return None
    text = str(cell).strip()
    if text.isdigit():
        value = int(text)
        return value if 1 <= value <= 50 else None
    match = GROUP_NUM_RE.match(text)
    if match:
        return int(match.group(1))
    return None


def extract_group_columns(row: list) -> list[tuple[int, int]]:
    columns: list[tuple[int, int]] = []
    for col_index, cell in enumerate(row):
        if col_index == 0:
            continue
        group_number = parse_group_number(cell)
        if group_number is not None:
            columns.append((col_index, group_number))
    if not columns:
        return []
    if len(columns) == 1:
        return columns
    numbers = [group_number for _, group_number in columns]
    if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
        return columns
    return columns


def extract_stream_columns(row: list) -> list[tuple[int, tuple[int, int]]]:
    streams: list[tuple[int, tuple[int, int]]] = []
    for col_index, cell in enumerate(row):
        if col_index == 0:
            continue
        text = str(cell or "").strip()
        if "поток" not in text.lower():
            continue
        match = STREAM_GROUPS_RE.search(text)
        if match:
            streams.append((col_index, (int(match.group(1)), int(match.group(2)))))
    return streams


def extract_groups_from_header(row: list) -> tuple[int, ...]:
    header_text = " ".join(str(cell or "") for cell in row)
    match = GROUPS_RANGE_RE.search(header_text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        return tuple(range(start, end + 1))
    return ()


def strip_semester_hours(text: str) -> str:
    return HOURS_RE.sub("", text).strip()


def _split_location(text: str) -> tuple[str, str]:
    match = LOCATION_START_RE.search(text)
    if not match:
        return text.strip(), ""
    start = match.start()
    if text[start] in " ,;":
        start += 1
    return text[:start].strip(" ,;"), text[start:].strip(" ,;")


def _lesson_type_from_line(line: str) -> str:
    lowered = line.lower().strip(" ,;")
    if not lowered:
        return ""
    if re.fullmatch(r"лекции|лек\.", lowered) or re.match(r"^(лекции|лек\.)\b", lowered):
        return "Лек."
    if re.fullmatch(
        r"практические(?:\s+занятия)?|практика|практ\.|пр\.", lowered
    ) or re.match(r"^(практические(?:\s+занятия)?|практика|практ\.|пр\.)\b", lowered):
        return "Практ."
    if re.fullmatch(
        r"лабораторные(?:\s+занятия)?|лабораторная|лаб\.|лабораторн\.", lowered
    ) or re.match(r"^(лабораторные(?:\s+занятия)?|лабораторная|лаб\.|лабораторн\.)\b", lowered):
        return "Лаб."
    if re.fullmatch(r"семинар(?:ские)?(?:\s+занятия)?|сем\.", lowered) or re.match(
        r"^(семинар(?:ские)?(?:\s+занятия)?|сем\.)\b", lowered
    ):
        return "Сем."
    if re.search(r"\bлекции\s*$", lowered) and len(lowered) <= 32:
        return "Лек."
    if re.search(r"\bпрактические\s*$", lowered) and len(lowered) <= 32:
        return "Практ."
    return ""


def _lesson_type_from_lines(raw_lines: list[str]) -> str:
    """Return a type prefix only when the cell explicitly marks lesson type."""
    for line in raw_lines[:3]:
        prefix = _lesson_type_from_line(line)
        if prefix:
            return prefix
    return ""


def _is_explicit_type_line(line: str) -> bool:
    return bool(_lesson_type_from_line(line))


def _is_type_label_only(line: str) -> bool:
    lowered = line.lower().strip(" ,;")
    return bool(
        re.fullmatch(
            r"лекции|лек\.|практические(?:\s+занятия)?|практика|практ\.|пр\.|"
            r"лабораторные(?:\s+занятия)?|лабораторная|лаб\.|лабораторн\.|"
            r"семинар(?:ские)?(?:\s+занятия)?|сем\.",
            lowered,
        )
    )


def _with_lesson_type(subject: str, type_prefix: str) -> str:
    if not type_prefix:
        return subject
    lowered = subject.lower()
    for marker in ("лек.", "практ.", "лаб.", "сем."):
        if lowered.startswith(marker):
            return subject
    return f"{type_prefix} {subject}".strip()


def _split_lecture_segment(segment: str, *, type_prefix: str = "") -> tuple[str, str]:
    cleaned = strip_semester_hours(segment.replace("\n", " "))
    leading_type = _lesson_type_from_line(cleaned.split(" ", 1)[0]) if cleaned else ""
    if re.match(r"^Лекции\b", cleaned, flags=re.IGNORECASE):
        type_prefix = type_prefix or "Лек."
        cleaned = re.sub(r"^Лекции\s+", "", cleaned, flags=re.IGNORECASE).strip()
    elif leading_type and re.match(
        r"^(лек\.|практ\.|пр\.|лаб\.|сем\.|практика|практические|лабораторн\w*|семинар\w*)\b",
        cleaned,
        flags=re.IGNORECASE,
    ):
        type_prefix = type_prefix or leading_type
        cleaned = re.sub(
            r"^(лек\.|практ\.|пр\.|лаб\.|сем\.|практика|практические(?:\s+занятия)?|лабораторные(?:\s+занятия)?|лабораторная|лабораторн\.|семинар(?:ские)?(?:\s+занятия)?)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
    cleaned = LECTURE_COUNT_RE.sub("", cleaned).strip()

    body, location = _split_location(cleaned)
    date_match = LECTURE_DATES_RE.search(body)
    if date_match:
        dates = date_match.group(0).strip(" ,;")
        subject = body[: date_match.start()].strip(" ,;")
        extra_parts = [dates, location]
    else:
        subject = body.strip(" ,;")
        extra_parts = [location]

    subject = re.sub(r"\s+", " ", subject).strip() or "Занятие"
    subject = _with_lesson_type(subject, type_prefix)
    extra = " ".join(part for part in extra_parts if part).strip()
    return subject, extra


def _parse_time_chunk(start: str, end: str, chunk: str) -> list[ParsedLesson]:
    chunk = chunk.replace("\xa0", " ").strip()
    if not chunk:
        return []

    raw_lines = [line.strip() for line in chunk.split("\n") if line.strip()]
    type_prefix = _lesson_type_from_lines(raw_lines)
    is_lecture_block = type_prefix == "Лек." or any(
        re.match(r"^лекции\b", line.lower()) for line in raw_lines
    )

    if is_lecture_block:
        type_prefix = type_prefix or "Лек."
        segments: list[str] = []
        for line in raw_lines:
            if _is_type_label_only(line):
                continue
            cleaned = re.sub(r"^Лекции\s+", "", line, flags=re.IGNORECASE).strip()
            if cleaned and cleaned.lower() not in {"лекции", "лек."}:
                segments.append(cleaned)

        shared_location = ""
        if segments:
            _, location = _split_location(segments[-1])
            shared_location = location

        lessons: list[ParsedLesson] = []
        for segment in segments:
            subject, extra = _split_lecture_segment(segment, type_prefix=type_prefix)
            if shared_location and shared_location not in extra:
                extra = f"{extra} {shared_location}".strip() if extra else shared_location
            lessons.append(ParsedLesson(start=start, end=end, subject=subject, extra=extra))
        return lessons

    content_lines = [line for line in raw_lines if not _is_type_label_only(line)]
    flat = re.sub(r"\s*\|\s*", " ", " ".join(content_lines))
    flat = re.sub(r"\s+", " ", flat).strip()
    subject, extra = _split_lecture_segment(flat, type_prefix=type_prefix)
    return [ParsedLesson(start=start, end=end, subject=subject, extra=extra)]


def parse_cell_lessons(cell_text: str) -> list[ParsedLesson]:
    if not cell_text or not cell_text.strip():
        return []

    text = cell_text.replace("\xa0", " ").strip()
    lessons: list[ParsedLesson] = []
    matches = list(TIME_RANGE_RE.finditer(text))
    if not matches:
        return lessons

    for idx, match in enumerate(matches):
        start = normalize_time(match.group(1))
        end = normalize_time(match.group(2))
        chunk_start = match.end()
        chunk_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[chunk_start:chunk_end].strip()
        lessons.extend(_parse_time_chunk(start, end, chunk))

    return lessons


def _lesson_payload(lesson: ParsedLesson) -> dict:
    return {
        "start": lesson.start,
        "end": lesson.end,
        "subject": lesson.subject,
        "extra": lesson.extra,
    }


def _append_lesson(group_data: dict, bucket: str, lesson: ParsedLesson) -> None:
    day_lessons = group_data.setdefault(bucket, [])
    payload = _lesson_payload(lesson)
    if payload not in day_lessons:
        day_lessons.append(payload)
        day_lessons.sort(key=lambda item: item["start"])


def _append_week_type_lesson(
    group_data: dict,
    week_type: str,
    day_key: str,
    lesson: ParsedLesson,
) -> None:
    week_bucket = group_data.setdefault(f"__{week_type}__", {})
    day_lessons = week_bucket.setdefault(day_key, [])
    payload = _lesson_payload(lesson)
    if payload not in day_lessons:
        day_lessons.append(payload)
        day_lessons.sort(key=lambda item: item["start"])


def _week_bucket(ctx: ParseContext) -> str:
    return ctx.week_type


def _ensure_group(group_schedules: dict[int, dict], group_number: int) -> dict:
    return group_schedules.setdefault(group_number, {})


def _assign_lessons_to_groups(
    group_schedules: dict[int, dict],
    groups: tuple[int, ...],
    day_key: str,
    lessons: list[ParsedLesson],
    *,
    week_type: str | None = None,
) -> None:
    for group_number in groups:
        group_data = _ensure_group(group_schedules, group_number)
        for lesson in lessons:
            _append_lesson(group_data, day_key, lesson)
            if week_type:
                _append_week_type_lesson(group_data, week_type, day_key, lesson)


def build_column_month_map(row: list) -> dict[int, str]:
    month_at_col: dict[int, str] = {}
    active_month = ""
    for col_index, cell in enumerate(row):
        text = str(cell or "").strip().lower()
        for month in MONTHS:
            if month in text:
                active_month = month.capitalize()
                break
        if active_month:
            month_at_col[col_index] = active_month
    if not month_at_col:
        return {}

    filled: dict[int, str] = {}
    last_month = ""
    max_col = max(month_at_col)
    for col_index in range(max_col + 1):
        if col_index in month_at_col:
            last_month = month_at_col[col_index]
        if last_month:
            filled[col_index] = last_month
    return filled


def _is_practice_reference_table(table: list[list]) -> bool:
    if not table or len(table) < 2:
        return False
    if max(len(row) for row in table) > 3:
        return False
    sample = " ".join(str(row[0] or "") for row in table[:4]).lower()
    return "продолжительность" in sample or PRACTICE_HOURS_RE.search(sample) is not None


def _is_cyclic_table(table: list[list]) -> bool:
    if not table:
        return False
    header_text = " ".join(str(cell or "") for cell in table[0]).lower()
    month_hits = sum(1 for month in MONTHS[:4] if month in header_text)
    if month_hits >= 1 and any(
        str(cell or "").strip().isdigit() for row in table[1:4] for cell in (row[1:6] if row else [])
    ):
        return True
    for row in table[:6]:
        if not row:
            continue
        first = str(row[0] or "").strip().lower()
        if is_dn_cell(first):
            return False
        if (first.isdigit() or GROUP_TOKEN_RE.match(first)) and any(
            cell and not str(cell).strip().isdigit() for cell in row[1:3]
        ):
            subject = " ".join(str(cell or "") for cell in row[1:3]).lower()
            if subject and not TIME_RANGE_RE.search(subject):
                return True
    return False


def _is_lecture_days_table(table: list[list]) -> bool:
    if not table:
        return False
    header = " ".join(str(cell or "") for cell in table[0]).lower()
    return "лекцион" in header and "дн" in header


def _is_lecture_table(table: list[list]) -> bool:
    if not table:
        return False
    if _is_lecture_days_table(table):
        return True
    header = " ".join(str(cell or "") for cell in table[0]).lower()
    if is_dn_cell(str(table[0][0] if table[0] else "")):
        return True
    if "лекции" in header and detect_day_index(str(table[1][0] if len(table) > 1 else "")) is not None:
        return True
    if "время" in header and ("дисциплин" in header or "гр" in header):
        return True
    if "1 поток" in header:
        return True
    return False


def _is_weekly_table(table: list[list]) -> bool:
    if not table:
        return False
    if _is_cyclic_table(table) or _is_practice_reference_table(table) or _is_lecture_table(table):
        return False
    if any(extract_group_columns(row) for row in table[:8]):
        return True
    for row in table[:8]:
        if detect_day_index(row[0] if row else None) is not None:
            if any(parse_cell_lessons(str(cell or "")) for cell in (row[1:] if row else [])):
                return True
    return False


def _find_group_header_rows(table: list[list]) -> list[tuple[int, list[tuple[int, int]]]]:
    headers: list[tuple[int, list[tuple[int, int]]]] = []
    for row_index, row in enumerate(table):
        columns = extract_group_columns(row)
        if columns:
            headers.append((row_index, columns))
    return headers


def _rows_for_header(table: list[list], header_index: int) -> list[list]:
    if header_index == 0:
        return table[1:]
    if header_index == len(table) - 1:
        return table[:header_index]
    return table[header_index + 1 :]


def _parse_practice_table(table: list[list], ctx: ParseContext) -> None:
    for row in table:
        if not row or len(row) < 2:
            continue
        subject_raw = str(row[0] or "").replace("\xa0", " ").strip()
        time_raw = str(row[1] or "").replace("\xa0", " ").strip()
        if not subject_raw or "продолжительность" in subject_raw.lower():
            continue
        match = TIME_RANGE_RE.search(time_raw)
        if not match:
            continue
        subject = subject_raw.split("\n")[0].strip()
        subject = re.split(r"\(\d+\s*дн", subject, maxsplit=1)[0].strip(" ,;")
        if not subject:
            continue
        ctx.subject_times[subject.lower()] = (
            normalize_time(match.group(1)),
            normalize_time(match.group(2)),
        )


def _row_group_cells(
    row: list,
    group_columns: list[tuple[int, int]],
) -> list[tuple[int, int, str]]:
    cells: list[tuple[int, int, str]] = []
    for col_index, group_number in group_columns:
        if col_index >= len(row):
            continue
        cell_text = str(row[col_index] or "").strip()
        if cell_text:
            cells.append((col_index, group_number, cell_text))
    return cells


def _is_horizontal_group_row(
    row: list,
    group_columns: list[tuple[int, int]],
) -> tuple[bool, str]:
    """Горизонтальный блок: одна ячейка на все группы из шапки."""
    cells = _row_group_cells(row, group_columns)
    if len(cells) != 1:
        return False, ""
    col_index, _, cell_text = cells[0]
    first_col = min(column for column, _ in group_columns)
    if col_index != first_col:
        return False, ""
    return True, cell_text


def _parse_weekly_rows(
    rows: list[list],
    group_columns: list[tuple[int, int]],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    current_day = ctx.current_day
    all_groups = tuple(group_number for _, group_number in group_columns)

    for row in rows:
        day_index = detect_day_index(row[0] if row else None)
        if day_index is not None:
            current_day = day_index
            ctx.current_day = day_index

        if current_day is None:
            continue

        day_key = str(current_day)
        is_horizontal, horizontal_text = _is_horizontal_group_row(row, group_columns)
        if is_horizontal:
            lessons = parse_cell_lessons(horizontal_text)
            _assign_lessons_to_groups(
                group_schedules,
                all_groups,
                day_key,
                lessons,
                week_type=ctx.week_type if ctx.week_type_sections > 0 else None,
            )
            continue

        for col_index, group_number in group_columns:
            if col_index >= len(row):
                continue
            cell_text = row[col_index]
            if not cell_text:
                continue
            for lesson in parse_cell_lessons(str(cell_text)):
                group_data = _ensure_group(group_schedules, group_number)
                _append_lesson(group_data, day_key, lesson)
                if ctx.week_type_sections > 0:
                    _append_week_type_lesson(group_data, ctx.week_type, day_key, lesson)


def _parse_weekly_table(
    table: list[list],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    headers = _find_group_header_rows(table)
    if headers:
        for header_index, columns in headers:
            ctx.group_columns = columns
            rows = _rows_for_header(table, header_index)
            _parse_weekly_rows(rows, columns, group_schedules, ctx)
        return

    if ctx.group_columns:
        _parse_weekly_rows(table, ctx.group_columns, group_schedules, ctx)


def _parse_cyclic_table(
    table: list[list],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    column_months: dict[int, str] = {}
    day_labels: dict[int, str] = {}

    for row in table:
        if not row:
            continue

        month_map = build_column_month_map(row)
        if month_map:
            column_months = month_map

        if not str(row[0] or "").strip() and any(str(cell or "").strip().isdigit() for cell in row[1:]):
            day_labels.clear()
            for col_index, cell in enumerate(row[1:], start=1):
                if cell and str(cell).strip().isdigit():
                    day_labels[col_index] = str(cell).strip()
            continue

        first_cell = str(row[0] or "").strip()
        group_number = parse_group_token(first_cell, ctx) if first_cell else None
        if group_number is None:
            continue

        group_data = _ensure_group(group_schedules, group_number)

        for col_index, cell in enumerate(row[1:], start=1):
            if not cell:
                continue
            subject = str(cell).replace("\xa0", " ").strip()
            if not subject or subject.isdigit() or TIME_RANGE_RE.search(subject):
                continue
            subject = HOURS_RE.sub("", subject).strip()
            if not subject:
                continue

            month = column_months.get(col_index, "")
            day_label = day_labels.get(col_index, "")
            if not month or not day_label:
                continue

            calendar_key = f"{_month_number(month)}-{int(day_label)}"
            subject_name = subject.split("\n")[0].strip()
            extra_parts = [part for part in (month, f"день {day_label}") if part]
            payload = {
                "start": "",
                "end": "",
                "subject": subject_name,
                "extra": ", ".join(extra_parts),
            }

            calendar = group_data.setdefault("__calendar__", {})
            entries = calendar.setdefault(calendar_key, [])
            if payload not in entries:
                entries.append(payload)

            cyclic = group_data.setdefault("__cyclic__", [])
            if payload not in cyclic:
                cyclic.append(payload)


def _month_number(month_name: str) -> int:
    mapping = {
        "январь": 1,
        "февраль": 2,
        "март": 3,
        "апрель": 4,
        "май": 5,
        "сентябрь": 9,
        "октябрь": 10,
        "ноябрь": 11,
        "декабрь": 12,
    }
    return mapping.get(month_name.lower(), 0)


def _toggle_week_type(ctx: ParseContext) -> None:
    ctx.week_type_sections += 1
    if ctx.week_type_sections > 1:
        ctx.week_type = "denominator"


def _parse_lecture_row_simple(
    row: list,
    groups: tuple[int, ...],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    day_index = detect_day_index(row[0] if row else None)
    if day_index is not None:
        ctx.current_day = day_index
    if ctx.current_day is None:
        return

    bucket = _week_bucket(ctx)
    day_key = str(ctx.current_day)

    time_cell = str(row[1] if len(row) > 1 else "").strip()
    subject_cell = str(row[2] if len(row) > 2 else "").strip()
    extra_cell = str(row[3] if len(row) > 3 else "").strip()

    time_match = TIME_RANGE_RE.search(time_cell)
    if time_match:
        start = normalize_time(time_match.group(1))
        end = normalize_time(time_match.group(2))
        subject = subject_cell.split("\n")[0].strip() or "Лекция"
        subject = strip_semester_hours(subject)
        subject = _with_lesson_type(subject, "Лек.")
        extra = strip_semester_hours(extra_cell.replace("\n", " ").strip())
        lesson = ParsedLesson(start=start, end=end, subject=subject, extra=extra)
        _assign_lessons_to_groups(
            group_schedules, groups, day_key, [lesson], week_type=ctx.week_type
        )
        return

    if subject_cell:
        for line in subject_cell.split("\n"):
            line = line.strip()
            if not line:
                continue
            match = TIME_RANGE_RE.search(line)
            if match:
                start = normalize_time(match.group(1))
                end = normalize_time(match.group(2))
                subject = TIME_RANGE_RE.sub("", line).strip(" ,") or "Лекция"
            else:
                start, end = "", ""
                subject = line
            subject = _with_lesson_type(strip_semester_hours(subject), "Лек.")
            lesson = ParsedLesson(
                start=start,
                end=end,
                subject=subject,
                extra=extra_cell.replace("\n", " ").strip(),
            )
            _assign_lessons_to_groups(
                group_schedules, groups, day_key, [lesson], week_type=ctx.week_type
            )


def _parse_lecture_row_streams(
    row: list,
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    day_index = detect_day_index(row[0] if row else None)
    if day_index is not None:
        ctx.current_day = day_index
    if ctx.current_day is None or not ctx.stream_columns:
        return

    bucket = _week_bucket(ctx)
    day_key = str(ctx.current_day)

    for col_index, (start_group, end_group) in ctx.stream_columns:
        if col_index >= len(row):
            continue
        cell_text = str(row[col_index] or "").strip()
        if not cell_text:
            continue
        lessons = parse_cell_lessons(cell_text)
        if not lessons and TIME_RANGE_RE.search(cell_text):
            continue
        if not lessons:
            match = TIME_RANGE_RE.search(cell_text)
            if match:
                subject = TIME_RANGE_RE.sub("", cell_text).strip(" ,")
                subject_name = subject.split("\n")[0].strip() or "Лекция"
                lessons = [
                    ParsedLesson(
                        start=normalize_time(match.group(1)),
                        end=normalize_time(match.group(2)),
                        subject=_with_lesson_type(subject_name, "Лек."),
                        extra=" ".join(subject.split("\n")[1:]).strip(),
                    )
                ]
            else:
                continue
        else:
            lessons = [
                ParsedLesson(
                    start=lesson.start,
                    end=lesson.end,
                    subject=_with_lesson_type(lesson.subject, "Лек."),
                    extra=lesson.extra,
                )
                for lesson in lessons
            ]
        groups = tuple(range(start_group, end_group + 1))
        _assign_lessons_to_groups(
            group_schedules, groups, day_key, lessons, week_type=ctx.week_type
        )


def _parse_lecture_days_table(
    table: list[list],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    groups = ctx.default_groups
    group_data = _ensure_group(group_schedules, groups[0])

    for row in table[1:]:
        if not row:
            continue
        marker = str(row[0] if row else "").strip()
        day_index = detect_day_index(marker.split(",")[-1] if "," in marker else marker)
        if day_index is not None:
            ctx.current_day = day_index

        calendar_key = parse_date_token(marker)
        time_cell = str(row[1] if len(row) > 1 else "").strip()
        subject_cell = str(row[2] if len(row) > 2 else "").strip()
        extra_cell = str(row[3] if len(row) > 3 else "").strip()
        if not subject_cell and not time_cell:
            continue

        time_match = TIME_RANGE_RE.search(time_cell)
        if time_match:
            start = normalize_time(time_match.group(1))
            end = normalize_time(time_match.group(2))
        else:
            start, end = "", ""

        subject = subject_cell.split("\n")[0].strip() or "Лекция"
        subject = _with_lesson_type(strip_semester_hours(subject), "Лек.")
        payload = {
            "start": start,
            "end": end,
            "subject": subject,
            "extra": extra_cell.replace("\n", " ").strip(),
        }

        if calendar_key:
            calendar = group_data.setdefault("__calendar__", {})
            entries = calendar.setdefault(calendar_key, [])
            if payload not in entries:
                entries.append(payload)

        if ctx.current_day is not None:
            _append_lesson(group_data, str(ctx.current_day), ParsedLesson(**payload))


def _parse_lecture_table(
    table: list[list],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    if _is_lecture_days_table(table):
        _parse_lecture_days_table(table, group_schedules, ctx)
        return

    groups = extract_groups_from_header(table[0])
    if not groups:
        groups = ctx.default_groups

    streams = extract_stream_columns(table[0])
    if streams:
        ctx.stream_columns = streams

    start_index = 1
    if is_dn_cell(str(table[0][0] if table[0] else "")):
        _toggle_week_type(ctx)
        start_index = 1

    for row in table[start_index:]:
        if not row:
            continue
        if is_dn_cell(str(row[0] if row else "")) and extract_stream_columns(row):
            _toggle_week_type(ctx)
            ctx.stream_columns = extract_stream_columns(row)
            continue

        if ctx.stream_columns:
            _parse_lecture_row_streams(row, group_schedules, ctx)
        else:
            _parse_lecture_row_simple(row, groups, group_schedules, ctx)


def _apply_subject_times(group_schedules: dict[int, dict], subject_times: dict[str, tuple[str, str]]) -> None:
    if not subject_times:
        return

    def match_time(subject: str) -> tuple[str, str] | None:
        lowered = subject.lower()
        for key, value in subject_times.items():
            if key in lowered or lowered in key:
                return value
        return None

    def enrich_lessons(lessons: list) -> None:
        for lesson in lessons:
            if not isinstance(lesson, dict):
                continue
            if lesson.get("start"):
                continue
            matched = match_time(lesson.get("subject", ""))
            if matched:
                lesson["start"], lesson["end"] = matched

    for group_data in group_schedules.values():
        for key, value in group_data.items():
            if key == "__calendar__":
                for entries in value.values():
                    enrich_lessons(entries)
            elif key in {"__numerator__", "__denominator__"}:
                for entries in value.values():
                    enrich_lessons(entries)
            elif key == "__cyclic__":
                enrich_lessons(value)
            elif str(key).isdigit():
                enrich_lessons(value)


def _finalize_schedule(group_schedules: dict[int, dict]) -> None:
    for group_data in group_schedules.values():
        has_week_types = bool(
            group_data.get("__numerator__")
            and group_data.get("__denominator__")
            and any(group_data.get("__denominator__", {}).values())
        )
        has_calendar = bool(group_data.get("__calendar__"))

        if has_week_types:
            meta = group_data.setdefault("__meta__", {})
            meta["has_week_types"] = True
            meta["format"] = "weekly"
            selected = group_data.get("__numerator__") or group_data.get("__denominator__") or {}
            for day_key, lessons in selected.items():
                if str(day_key).isdigit() and day_key not in group_data:
                    group_data[day_key] = lessons

        if has_calendar:
            meta = group_data.setdefault("__meta__", {})
            meta["format"] = "calendar"
            cyclic = group_data.setdefault("__cyclic__", [])
            for entries in group_data["__calendar__"].values():
                for item in entries:
                    if item not in cyclic:
                        cyclic.append(item)
        elif any(str(day).isdigit() for day in group_data.keys()):
            meta = group_data.setdefault("__meta__", {})
            meta.setdefault("format", "weekly")


def _classify_table(table: list[list]) -> str:
    if _is_practice_reference_table(table):
        return "practice"
    if _is_cyclic_table(table):
        return "cyclic"
    if _is_lecture_table(table):
        return "lecture"
    if _is_weekly_table(table):
        return "weekly"
    return "unknown"


def _parse_table(
    table: list[list],
    group_schedules: dict[int, dict],
    ctx: ParseContext,
) -> None:
    if not table:
        return

    kind = _classify_table(table)
    if kind == "practice":
        _parse_practice_table(table, ctx)
    elif kind == "cyclic":
        _parse_cyclic_table(table, group_schedules, ctx)
    elif kind == "lecture":
        _parse_lecture_table(table, group_schedules, ctx)
    elif kind == "weekly":
        _parse_weekly_table(table, group_schedules, ctx)
    elif ctx.group_columns:
        _parse_weekly_table(table, group_schedules, ctx)


def parse_pdf_bytes(content: bytes) -> dict[int, dict[str, list[dict]]]:
    group_schedules: dict[int, dict[str, list[dict]]] = {}
    ctx = ParseContext()

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                _parse_table(table, group_schedules, ctx)

    _apply_subject_times(group_schedules, ctx.subject_times)
    _finalize_schedule(group_schedules)
    return group_schedules


class RzgmuPdfParser:
    def __init__(self, base_url: str = "https://www.rzgmu.ru") -> None:
        self.base_url = base_url.rstrip("/")

    async def fetch_bytes(
        self,
        pdf_path: str,
        client: httpx.AsyncClient | None = None,
    ) -> bytes:
        url = f"{self.base_url}{pdf_path}"
        if client is None:
            async with create_rzgmu_client() as owned_client:
                response = await fetch_response(owned_client, url)
                return response.content
        response = await fetch_response(client, url)
        return response.content

    async def fetch_and_parse(
        self,
        pdf_path: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[int, dict[str, list[dict]]]:
        content = await self.fetch_bytes(pdf_path, client=client)
        return await asyncio.to_thread(parse_pdf_bytes, content)

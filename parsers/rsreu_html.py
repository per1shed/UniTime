from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup, Tag

from parsers.rsreu_http import create_rsreu_client, fetch_response

LESSON_TYPE_RE = re.compile(
    r"^(Лек\.|Лаб\.|Упр\.|Практ\.|Сем\.|Конс\.|Зач\.|Экз\.|Диф\.зач\.)\s*",
    re.IGNORECASE,
)

LESSON_TYPE_BY_CELL_CLASS = {
    "schedule-lesson-type-1": "Лек.",
    "schedule-lesson-type-2": "Лаб.",
    "schedule-lesson-type-3": "Упр.",
}

LESSON_TYPE_PREFIXES = (
    "Лек.",
    "Лаб.",
    "Упр.",
    "Практ.",
    "Сем.",
    "Конс.",
    "Зач.",
    "Экз.",
    "Диф.зач.",
)

NUMERATOR = "numerator"
DENOMINATOR = "denominator"

WEEK_TYPE_DISPLAY = {
    NUMERATOR: "Числитель",
    DENOMINATOR: "Знаменатель",
}


def parse_week_type(label: str) -> str | None:
    normalized = label.lower().replace("ё", "е")
    if "числ" in normalized:
        return NUMERATOR
    if "знам" in normalized:
        return DENOMINATOR
    return None


def week_type_label(week_type: str | None) -> str | None:
    if not week_type:
        return None
    return WEEK_TYPE_DISPLAY.get(week_type)


def pick_current_week(weeks: list["WeekInfo"], today: date | None = None) -> "WeekInfo | None":
    if not weeks:
        return None

    moment = today or date.today()
    for week in weeks:
        start = date.fromisoformat(week.date)
        end = start + timedelta(days=6)
        if start <= moment <= end:
            return week

    past = [week for week in weeks if date.fromisoformat(week.date) <= moment]
    if past:
        return max(past, key=lambda week: week.date)

    return min(weeks, key=lambda week: week.date)


def pick_week_by_type(
    weeks: list["WeekInfo"],
    week_type: str,
    today: date | None = None,
) -> "WeekInfo | None":
    filtered = [week for week in weeks if parse_week_type(week.label) == week_type]
    return pick_current_week(filtered, today)


def pick_week_by_start(weeks: list["WeekInfo"], week_start: date) -> "WeekInfo | None":
    if not weeks:
        return None

    target = week_start.isoformat()
    exact = next((week for week in weeks if week.date == target), None)
    if exact:
        return exact

    for week in weeks:
        start = date.fromisoformat(week.date)
        end = start + timedelta(days=6)
        if start <= week_start <= end:
            return week

    past = [week for week in weeks if date.fromisoformat(week.date) <= week_start]
    if past:
        return max(past, key=lambda week: week.date)

    return min(weeks, key=lambda week: week.date)


def cached_week_dates(cached: dict | None) -> list[date]:
    """Sorted week start dates available in a multi-week RSREU cache."""
    if not cached:
        return []
    weeks_map = cached.get("__weeks__")
    if not isinstance(weeks_map, dict) or not weeks_map:
        return []
    dates: list[date] = []
    for key in weeks_map:
        try:
            dates.append(date.fromisoformat(str(key)))
        except ValueError:
            continue
    dates.sort()
    return dates


def neighbor_week_start(
    cached: dict | None,
    current: date,
    delta: int,
) -> date | None:
    """Move to the previous/next cached week. Returns None at the boundary."""
    dates = cached_week_dates(cached)
    if not dates:
        return None

    if current in dates:
        index = dates.index(current)
    else:
        # Snap to the nearest week at/before current, else the first one.
        past = [item for item in dates if item <= current]
        index = dates.index(max(past)) if past else 0

    target = index + delta
    if target < 0 or target >= len(dates):
        return None
    return dates[target]


def weeks_window(
    weeks: list["WeekInfo"],
    today: date | None = None,
    *,
    before: int = 2,
    after: int = 6,
) -> list["WeekInfo"]:
    """Return a slice of weeks around the current one for caching."""
    if not weeks:
        return []
    current = pick_current_week(weeks, today)
    if not current:
        return weeks[: before + after + 1]
    try:
        index = next(i for i, week in enumerate(weeks) if week.date == current.date)
    except StopIteration:
        return weeks[: before + after + 1]
    start = max(0, index - before)
    end = min(len(weeks), index + after + 1)
    return weeks[start:end]


def schedule_from_weeks_cache(
    cached: dict | None,
    week_start: date | None = None,
    *,
    today: date | None = None,
) -> dict | None:
    """Pick a concrete week schedule from multi-week RSREU cache."""
    if not cached:
        return None

    weeks_map = cached.get("__weeks__")
    if not isinstance(weeks_map, dict) or not weeks_map:
        return dict(cached)

    infos = [
        WeekInfo(date=key, label=(value or {}).get("__week__", {}).get("label", key))
        for key, value in weeks_map.items()
        if isinstance(value, dict)
    ]
    infos.sort(key=lambda item: item.date)
    if not infos:
        return dict(cached)

    if week_start is not None:
        selected = pick_week_by_start(infos, week_start)
    else:
        selected = pick_current_week(infos, today)
    if not selected:
        selected = infos[0]

    week_data = weeks_map.get(selected.date)
    if not isinstance(week_data, dict):
        return dict(cached)

    result = {key: value for key, value in week_data.items() if not str(key).startswith("__")}
    result["__week__"] = dict(week_data.get("__week__", {}))
    result["__weeks__"] = weeks_map
    from parsers.rzgmu_dates import monday_of, week_label

    week_date = result["__week__"].get("date") or selected.date
    try:
        label_start = monday_of(date.fromisoformat(str(week_date)))
    except ValueError:
        label_start = monday_of(date.fromisoformat(selected.date))
    result["__week__"]["calendar_start"] = label_start.isoformat()
    result["__week__"]["calendar_label"] = week_label(label_start)
    return result


def schedule_has_lesson_types(schedule: dict | None) -> bool:
    """True when cached RSREU schedule already includes lesson type prefixes."""
    if not schedule:
        return False

    meta = schedule.get("__meta__", {})
    if meta.get("lesson_types"):
        return True

    weeks_map = schedule.get("__weeks__")
    candidates: list[dict] = []
    if isinstance(weeks_map, dict) and weeks_map:
        candidates.extend(item for item in weeks_map.values() if isinstance(item, dict))
    else:
        candidates.append(schedule)

    for item in candidates:
        for key, day in item.items():
            if str(key).startswith("__") or not isinstance(day, list):
                continue
            for lesson in day:
                if not isinstance(lesson, dict):
                    continue
                subject = str(lesson.get("subject", ""))
                if any(subject.startswith(prefix) for prefix in LESSON_TYPE_PREFIXES):
                    return True
    return False


@dataclass
class GroupInfo:
    group_id: int
    label: str


@dataclass
class WeekInfo:
    date: str
    label: str


class RsreuHtmlParser:
    def __init__(self, base_url: str = "https://rasp.rsreu.ru") -> None:
        self.base_url = base_url.rstrip("/")

    async def fetch_groups(
        self,
        faculty_id: int,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[GroupInfo]:
        url = f"{self.base_url}/schedule-frame/group?faculty={faculty_id}"
        if client is None:
            async with create_rsreu_client() as owned_client:
                response = await fetch_response(owned_client, url)
        else:
            response = await fetch_response(client, url)
        html_text = response.text

        match = re.search(r':options="(\[.*?\])"', html_text)
        if not match:
            return []

        raw = html.unescape(match.group(1))
        options = json.loads(raw)
        groups: list[GroupInfo] = []
        for item in options:
            value = item.get("value", 0)
            label = item.get("label", "")
            if value and label and label != "-- Не выбрана --":
                groups.append(GroupInfo(group_id=int(value), label=str(label)))
        groups.sort(key=lambda g: g.label)
        return groups

    async def fetch_weeks(
        self,
        faculty_id: int,
        group_id: int,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[WeekInfo]:
        url = (
            f"{self.base_url}/schedule-frame/group"
            f"?faculty={faculty_id}&group={group_id}"
        )
        if client is None:
            async with create_rsreu_client() as owned_client:
                response = await fetch_response(owned_client, url)
        else:
            response = await fetch_response(client, url)
        soup = BeautifulSoup(response.text, "lxml")

        weeks: list[WeekInfo] = []
        for option in soup.select('select[name="date"] option'):
            value = option.get("value", "").strip()
            label = option.get_text(" ", strip=True)
            if value:
                weeks.append(WeekInfo(date=value, label=label))
        return weeks

    async def fetch_group_schedule(
        self,
        faculty_id: int,
        group_id: int,
        week_date: str | None = None,
        *,
        today: date | None = None,
        weeks: list[WeekInfo] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        if client is None:
            async with create_rsreu_client() as owned_client:
                return await self.fetch_group_schedule(
                    faculty_id,
                    group_id,
                    week_date,
                    today=today,
                    weeks=weeks,
                    client=owned_client,
                )

        if weeks is None:
            weeks = await self.fetch_weeks(faculty_id, group_id, client=client)
        if not weeks:
            return {}

        selected = None
        if week_date:
            selected = next((week for week in weeks if week.date == week_date), None)
        else:
            selected = pick_current_week(weeks, today)

        if not selected:
            return {}

        url = (
            f"{self.base_url}/schedule-frame/group"
            f"?faculty={faculty_id}&group={group_id}&date={selected.date}"
        )
        response = await fetch_response(client, url)
        soup = BeautifulSoup(response.text, "lxml")

        schedule = self._parse_schedule_table(soup)
        week_type = parse_week_type(selected.label)
        schedule["__week__"] = {
            "date": selected.date,
            "label": selected.label,
            "type": week_type,
            "type_label": week_type_label(week_type) or "",
        }
        return schedule

    def _parse_schedule_table(self, soup: BeautifulSoup) -> dict:
        table = soup.select_one("table.table-vertical-borders")
        if not table:
            return {}

        rows = table.find_all("tr", recursive=False)
        if len(rows) < 2:
            return {}

        schedule: dict[str, list[dict]] = {str(i): [] for i in range(7)}
        for row in rows[1:]:
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue

            start, end = self._parse_time_cell(cells[0])
            for day_index, cell in enumerate(cells[1:7]):
                for lesson in self._parse_lesson_cell(cell, start, end):
                    schedule[str(day_index)].append(lesson)

        return schedule

    def _parse_time_cell(self, cell: Tag) -> tuple[str, str]:
        bold = cell.find("div", style=re.compile(r"font-weight:\s*bold"))
        plain_divs = cell.find_all("div", recursive=False)
        start = bold.get_text(" ", strip=True) if bold else ""
        end = ""
        for div in plain_divs:
            if div is bold:
                continue
            text = div.get_text(" ", strip=True)
            if text:
                end = text
                break
        return start, end

    def _lesson_type_from_block(self, block: Tag) -> str:
        badge = block.select_one(".schedule-lesson-type-badge")
        if badge:
            return self._normalize_lesson_type(badge.get_text(" ", strip=True))
        return ""

    def _lesson_type_from_cell(self, cell: Tag) -> str:
        for class_name in cell.get("class", []):
            if class_name in LESSON_TYPE_BY_CELL_CLASS:
                return LESSON_TYPE_BY_CELL_CLASS[class_name]
        return ""

    @staticmethod
    def _normalize_lesson_type(value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return ""
        if cleaned.endswith("."):
            return cleaned
        lowered = cleaned.lower().rstrip(".")
        aliases = {
            "лек": "Лек.",
            "лаб": "Лаб.",
            "упр": "Упр.",
            "практ": "Практ.",
            "сем": "Сем.",
        }
        return aliases.get(lowered, cleaned)

    def _parse_lesson_cell(
        self, cell: Tag, start: str, end: str
    ) -> list[dict]:
        classes = cell.get("class", [])
        if not classes or "schedule-cell" not in classes:
            return []

        cell_type = self._lesson_type_from_cell(cell)
        cell_badge = cell.select_one(".schedule-lesson-type-badge")
        if cell_badge and not cell_type:
            cell_type = self._normalize_lesson_type(cell_badge.get_text(" ", strip=True))

        blocks = cell.find_all("div", recursive=False)
        if not blocks:
            blocks = [cell]

        lessons: list[dict] = []
        for block in blocks:
            nested = [
                item
                for item in block.find_all("div", recursive=False)
                if item.select_one(".schedule-lesson-type-badge") or item.get_text(strip=True)
            ]
            targets = nested if len(nested) > 1 else [block]
            for target in targets:
                lesson_type = (
                    self._lesson_type_from_block(target)
                    or cell_type
                )
                text = self._cell_block_text(target)
                if not text:
                    continue
                subject, extra = self._split_subject_extra(text, lesson_type=lesson_type)
                lessons.append(
                    {
                        "start": start,
                        "end": end,
                        "subject": subject,
                        "extra": extra,
                        "type": lesson_type,
                    }
                )
        return lessons

    def _cell_block_text(self, block: Tag) -> str:
        clone = BeautifulSoup(str(block), "lxml")
        for badge in clone.select(".schedule-lesson-type-badge"):
            badge.decompose()
        text = clone.get_text("\n", strip=True)
        text = re.sub(r"\n+", "\n", text)
        return text.strip()

    def _split_subject_extra(self, text: str, *, lesson_type: str = "") -> tuple[str, str]:
        type_prefix = self._normalize_lesson_type(lesson_type)
        match = LESSON_TYPE_RE.match(text)
        if match:
            type_prefix = self._normalize_lesson_type(match.group(1))
            text = text[match.end():].strip()
        text = text.replace("\xa0", " ")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return type_prefix or "Занятие", ""
        subject = lines[0].rstrip(",").strip()
        if type_prefix:
            subject = f"{type_prefix} {subject}".strip()
        extra_parts = [part.rstrip(",").strip() for part in lines[1:] if part.strip()]
        extra = ", ".join(extra_parts)
        extra = re.sub(r",\s*,", ", ", extra)
        extra = re.sub(r",\s*ауд\.", ", ауд.", extra)
        return subject or "Занятие", extra

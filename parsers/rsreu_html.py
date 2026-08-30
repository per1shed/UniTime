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

    def _parse_lesson_cell(
        self, cell: Tag, start: str, end: str
    ) -> list[dict]:
        if not cell.get("class") or "schedule-cell" not in cell.get("class", []):
            return []

        lessons: list[dict] = []
        for block in cell.find_all("div", recursive=False):
            text = self._cell_block_text(block)
            if not text:
                continue
            subject, extra = self._split_subject_extra(text)
            lessons.append(
                {
                    "start": start,
                    "end": end,
                    "subject": subject,
                    "extra": extra,
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

    def _split_subject_extra(self, text: str) -> tuple[str, str]:
        text = LESSON_TYPE_RE.sub("", text).strip()
        text = text.replace("\xa0", " ")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return "Занятие", ""
        subject = lines[0].rstrip(",").strip()
        extra_parts = [part.rstrip(",").strip() for part in lines[1:] if part.strip()]
        extra = ", ".join(extra_parts)
        extra = re.sub(r",\s*,", ", ", extra)
        extra = re.sub(r",\s*ауд\.", ", ауд.", extra)
        return subject or "Занятие", extra

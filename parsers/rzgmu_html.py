from dataclasses import dataclass
import re

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass
class ScheduleLink:
    course_number: int
    variant_name: str
    pdf_path: str


@dataclass
class SpecialtyInfo:
    code: str
    name: str
    links: list[ScheduleLink]


COURSE_RE = re.compile(r"(\d+)\s*кур[сc]", re.IGNORECASE)
SPEC_RE = re.compile(r"^(.+?)\s*\(([\d.]+)\)\s*$")


class RzgmuHtmlParser:
    def __init__(self, base_url: str = "https://www.rzgmu.ru") -> None:
        self.base_url = base_url.rstrip("/")

    async def fetch_specialties(self, schedule_path: str) -> list[SpecialtyInfo]:
        url = f"{self.base_url}{schedule_path}"
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        soup = BeautifulSoup(html, "lxml")
        specialties: list[SpecialtyInfo] = []

        for strong in soup.find_all("strong"):
            text = strong.get_text(" ", strip=True)
            match = SPEC_RE.match(text)
            if not match:
                continue

            name, code = match.group(1).strip(), match.group(2).strip()
            ul = strong.find_next("ul")
            if not ul:
                continue

            links: list[ScheduleLink] = []
            for li in ul.find_all("li", recursive=False):
                links.extend(self._parse_course_li(li))

            if links:
                specialties.append(SpecialtyInfo(code=code, name=name, links=links))

        return specialties

    def _parse_course_li(self, li: Tag) -> list[ScheduleLink]:
        course_number = self._extract_course_number(li)
        if course_number is None:
            return []

        result: list[ScheduleLink] = []
        seen_paths: set[str] = set()
        for anchor in li.find_all("a", href=True):
            href = anchor["href"].strip()
            label = anchor.get_text(" ", strip=True)
            if not href.startswith("/upload/schedule/") or not href.endswith(".pdf"):
                continue
            if not label or label == "\u200b":
                continue
            if href in seen_paths:
                continue
            seen_paths.add(href)
            result.append(
                ScheduleLink(
                    course_number=course_number,
                    variant_name=label,
                    pdf_path=href,
                )
            )
        return result

    def _extract_course_number(self, li: Tag) -> int | None:
        for child in li.children:
            if isinstance(child, NavigableString):
                match = COURSE_RE.search(str(child))
                if match:
                    return int(match.group(1))
            elif isinstance(child, Tag) and child.name != "ul":
                match = COURSE_RE.search(child.get_text(" ", strip=True))
                if match:
                    return int(match.group(1))
        match = COURSE_RE.search(li.get_text(" ", strip=True))
        return int(match.group(1)) if match else None

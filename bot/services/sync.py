import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot.config import Settings
from bot.db.models import ScheduleSource, Specialty, University
from bot.db.repository import (
    dedupe_schedule_sources,
    ensure_universities,
    get_group_schedule,
    is_rsreu_source,
    parse_rsreu_ref,
    save_schedule_cache,
    upsert_schedule_source,
    upsert_specialty,
)
from parsers.rsreu_course import course_from_group_label as course_from_rsreu_group_label
from parsers.rsreu_faculties import RSREU_FACULTIES
from parsers.rsreu_html import RsreuHtmlParser, pick_current_week, pick_week_by_type
from parsers.rzgmu_html import RzgmuHtmlParser, ScheduleLink, SpecialtyInfo
from parsers.rzgmu_http import create_rzgmu_client
from parsers.rzgmu_pdf import RzgmuPdfParser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RzgmuSyncItem:
    specialty_info: SpecialtyInfo
    link: ScheduleLink
    source_id: int


class ScheduleSyncService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.rzgmu_html_parser = RzgmuHtmlParser()
        self.rzgmu_pdf_parser = RzgmuPdfParser()
        self.rsreu_parser = RsreuHtmlParser()

    async def sync_all(self) -> None:
        async with self.session_factory() as session:
            universities = await ensure_universities(session)
            await session.commit()

        await asyncio.gather(
            *[
                self._sync_university(university.code, university.id)
                for university in universities
            ]
        )

    async def _sync_university(self, code: str, university_id: int) -> None:
        async with self.session_factory() as session:
            university = await session.get(University, university_id)
            if not university:
                return
            if code == "rzgmu":
                await self._sync_rzgmu(session, university)
            elif code == "rsreu":
                await self._sync_rsreu(session, university)
            await session.commit()

    async def _sync_rzgmu(self, session: AsyncSession, university: University) -> None:
        async with create_rzgmu_client() as client:
            specialties = await self.rzgmu_html_parser.fetch_specialties(
                university.schedule_page_path,
                client=client,
            )

            work_items: list[_RzgmuSyncItem] = []
            for specialty_info in specialties:
                specialty = await upsert_specialty(
                    session,
                    university.id,
                    specialty_info.code,
                    specialty_info.name,
                )
                for link in specialty_info.links:
                    source = await upsert_schedule_source(
                        session,
                        specialty.id,
                        link.course_number,
                        link.variant_name,
                        link.pdf_path,
                    )
                    work_items.append(
                        _RzgmuSyncItem(
                            specialty_info=specialty_info,
                            link=link,
                            source_id=source.id,
                        )
                    )
            await session.commit()

            if not work_items:
                return

            semaphore = asyncio.Semaphore(self.settings.rzgmu_sync_concurrency)

            async def process_item(item: _RzgmuSyncItem) -> None:
                async with semaphore:
                    try:
                        group_schedules = await self.rzgmu_pdf_parser.fetch_and_parse(
                            item.link.pdf_path,
                            client=client,
                        )
                        async with self.session_factory() as item_session:
                            await save_schedule_cache(
                                item_session,
                                item.source_id,
                                group_schedules,
                            )
                            await item_session.commit()
                        logger.info(
                            "Parsed %s course %s (%s): %s groups",
                            item.specialty_info.code,
                            item.link.course_number,
                            item.link.variant_name,
                            len(group_schedules),
                        )
                    except Exception:
                        logger.exception(
                            "Failed to parse schedule %s for %s",
                            item.link.pdf_path,
                            item.specialty_info.name,
                        )

            await asyncio.gather(*(process_item(item) for item in work_items))

    async def _sync_rsreu(self, session: AsyncSession, university: University) -> None:
        await dedupe_schedule_sources(session, university.id)

        async def sync_faculty(faculty) -> None:
            async with self.session_factory() as faculty_session:
                specialty = await upsert_specialty(
                    faculty_session,
                    university.id,
                    faculty.key,
                    faculty.name,
                )
                try:
                    groups = await self.rsreu_parser.fetch_groups(faculty.site_id)
                except Exception:
                    logger.exception("Failed to fetch RSREU groups for %s", faculty.name)
                    return

                for group in groups:
                    course_number = course_from_rsreu_group_label(group.label) or 0
                    await upsert_schedule_source(
                        faculty_session,
                        specialty.id,
                        course_number,
                        group.label,
                        f"rsreu:{faculty.site_id}:{group.group_id}",
                    )
                await faculty_session.commit()
                logger.info("Synced RSREU %s: %s groups", faculty.name, len(groups))

        await asyncio.gather(*(sync_faculty(faculty) for faculty in RSREU_FACULTIES))

    async def load_rsreu_schedule(
        self,
        session: AsyncSession,
        source_id: int,
        group_number: int,
        *,
        week_type: str | None = None,
    ) -> dict | None:
        source = await session.get(ScheduleSource, source_id)
        if not source or not is_rsreu_source(source.pdf_path):
            return None

        ref = parse_rsreu_ref(source.pdf_path)
        if not ref:
            return None

        faculty_id, group_id = ref
        if group_id != group_number:
            return None

        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        weeks = await self.rsreu_parser.fetch_weeks(faculty_id, group_id)
        if week_type:
            selected_week = pick_week_by_type(weeks, week_type, today)
        else:
            selected_week = pick_current_week(weeks, today)
        if not selected_week:
            return None

        schedule = await self.rsreu_parser.fetch_group_schedule(
            faculty_id,
            group_id,
            selected_week.date,
            today=today,
        )
        if schedule:
            await save_schedule_cache(session, source_id, {group_number: schedule})
        return schedule

    async def ensure_rsreu_schedule_current(
        self,
        session: AsyncSession,
        source_id: int,
        group_number: int,
    ) -> None:
        cached = await get_group_schedule(session, source_id, group_number)
        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        source = await session.get(ScheduleSource, source_id)
        if not source or not is_rsreu_source(source.pdf_path):
            return

        ref = parse_rsreu_ref(source.pdf_path)
        if not ref:
            return

        faculty_id, group_id = ref
        if group_id != group_number:
            return

        weeks = await self.rsreu_parser.fetch_weeks(faculty_id, group_id)
        current_week = pick_current_week(weeks, today)
        if not current_week:
            return

        cached_date = (cached or {}).get("__week__", {}).get("date")
        if cached and cached_date == current_week.date:
            return

        await self.load_rsreu_schedule(session, source_id, group_number)

    async def ensure_source_cached(self, source_id: int) -> None:
        async with self.session_factory() as session:
            stmt = (
                select(ScheduleSource)
                .options(selectinload(ScheduleSource.specialty).selectinload(Specialty.university))
                .where(ScheduleSource.id == source_id)
            )
            result = await session.execute(stmt)
            source = result.scalar_one_or_none()
            if not source:
                return

            if is_rsreu_source(source.pdf_path):
                ref = parse_rsreu_ref(source.pdf_path)
                if not ref:
                    return
                faculty_id, group_id = ref
                today = datetime.now(ZoneInfo(self.settings.timezone)).date()
                schedule = await self.rsreu_parser.fetch_group_schedule(
                    faculty_id,
                    group_id,
                    today=today,
                )
                if schedule:
                    await save_schedule_cache(session, source.id, {group_id: schedule})
            else:
                group_schedules = await self.rzgmu_pdf_parser.fetch_and_parse(source.pdf_path)
                await save_schedule_cache(session, source.id, group_schedules)
            await session.commit()

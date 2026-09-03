import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from bot.config import Settings
from bot import emoji as e
from bot.db.models import ScheduleSource, User, UserSubscription
from bot.db.repository import (
    break_line_after,
    format_lesson_highlight,
    format_lessons_list,
    get_group_schedule,
    is_rsreu_source,
    lessons_from_day_data,
    mark_notification_sent,
    notification_key,
    notification_was_sent,
    resolve_schedule_for_view,
    schedule_is_calendar_format,
)
from parsers.rzgmu_week import calendar_key_for_date
from bot.services.keyboard_tracker import get_keyboard_tracker
from bot.services.sync import ScheduleSyncService
from bot.utils.academic_calendar import is_study_day

logger = logging.getLogger(__name__)


def _is_due_this_minute(now: datetime, target: datetime) -> bool:
    """True when the 1-minute scheduler tick lands on the target clock minute."""
    return now.hour == target.hour and now.minute == target.minute


class NotificationService:
    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        sync_service: ScheduleSyncService,
    ) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.settings = settings
        self.sync_service = sync_service
        self.tz = ZoneInfo(settings.timezone)

    async def process_due_notifications(self) -> None:
        now = datetime.now(self.tz)
        if not is_study_day(now.date()):
            return

        async with self.session_factory() as session:
            stmt = (
                select(UserSubscription)
                .join(User)
                .where(UserSubscription.notifications_enabled.is_(True))
                .options(
                    selectinload(UserSubscription.user),
                    selectinload(UserSubscription.source)
                    .selectinload(ScheduleSource.specialty),
                )
            )
            result = await session.execute(stmt)
            subscriptions = list(result.scalars().unique().all())

            for sub in subscriptions:
                if is_rsreu_source(sub.source.pdf_path):
                    await self.sync_service.ensure_rsreu_schedule_current(
                        session,
                        sub.source_id,
                        sub.group_number,
                    )
                schedule = await get_group_schedule(session, sub.source_id, sub.group_number)
                if not schedule:
                    continue
                schedule = resolve_schedule_for_view(
                    schedule,
                    sub.source.pdf_path,
                    now=now,
                )
                await self._process_subscription(session, sub, schedule, now)

            await session.commit()

    async def _process_subscription(
        self,
        session: AsyncSession,
        sub: UserSubscription,
        schedule: dict,
        now: datetime,
    ) -> None:
        day_index = now.weekday()
        if schedule_is_calendar_format(schedule):
            today_key = calendar_key_for_date(now)
            tomorrow = now + timedelta(days=1)
            tomorrow_key = calendar_key_for_date(tomorrow)
            lessons_today = lessons_from_day_data(schedule.get("__calendar__", {}).get(today_key, []))
            lessons_tomorrow = lessons_from_day_data(
                schedule.get("__calendar__", {}).get(tomorrow_key, [])
            )
        else:
            lessons_today = lessons_from_day_data(schedule.get(str(day_index), []))
            lessons_tomorrow = lessons_from_day_data(
                schedule.get(str((day_index + 1) % 7), [])
            )

        morning_target = now.replace(
            hour=self.settings.morning_hour,
            minute=self.settings.morning_minute,
            second=0,
            microsecond=0,
        )
        if _is_due_this_minute(now, morning_target):
            key = notification_key("morning", now, str(day_index))
            if not await notification_was_sent(session, sub.user_id, key):
                text = (
                    f"{e.ce(e.SUN, '☀️')} <b>Доброе утро!</b>\n"
                    f"Сегодня у тебя следующие пары:\n\n"
                    f"{format_lessons_list(lessons_today)}"
                )
                await self._send(sub.user.telegram_id, text)
                await mark_notification_sent(session, sub.user_id, key)

        if lessons_today:
            for lesson in lessons_today:
                if not lesson.start or ":" not in lesson.start:
                    continue
                start_h, start_m = map(int, lesson.start.split(":"))
                lesson_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
                reminder_at = lesson_start - timedelta(minutes=self.settings.lesson_reminder_minutes)
                if _is_due_this_minute(now, reminder_at):
                    key = notification_key("lesson", now, f"{lesson.start}:{lesson.subject}")
                    if not await notification_was_sent(session, sub.user_id, key):
                        text = (
                            f"Через {self.settings.lesson_reminder_minutes} минут начнётся пара:\n"
                            f"{format_lesson_highlight(lesson)}"
                        )
                        break_line = break_line_after(lesson, lessons_today)
                        if break_line:
                            text += f"\n{break_line}"
                        await self._send(sub.user.telegram_id, text)
                        await mark_notification_sent(session, sub.user_id, key)

        tomorrow_index = (day_index + 1) % 7
        evening_target = now.replace(
            hour=self.settings.evening_hour,
            minute=self.settings.evening_minute,
            second=0,
            microsecond=0,
        )
        if _is_due_this_minute(now, evening_target):
            tomorrow = now + timedelta(days=1)
            key = notification_key("evening", tomorrow, str(tomorrow_index))
            if not await notification_was_sent(session, sub.user_id, key):
                text = (
                    f"{e.ce(e.CALENDAR, '🗓')} <b>Добрый вечер!</b>\n"
                    f"Завтра у тебя следующие пары:\n\n"
                    f"{format_lessons_list(lessons_tomorrow)}"
                )
                await self._send(sub.user.telegram_id, text)
                await mark_notification_sent(session, sub.user_id, key)

    async def _send(self, telegram_id: int, text: str) -> None:
        try:
            await get_keyboard_tracker().clear_old(self.bot, telegram_id)
            await self.bot.send_message(telegram_id, text)
        except Exception:
            logger.exception("Failed to send notification to %s", telegram_id)


def setup_scheduler(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    sync_service: ScheduleSyncService,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    notifications = NotificationService(bot, session_factory, settings, sync_service)

    scheduler.add_job(
        notifications.process_due_notifications,
        trigger="interval",
        minutes=1,
        id="notifications",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_service.sync_all,
        trigger="interval",
        hours=settings.schedule_sync_hours,
        id="schedule_sync",
        replace_existing=True,
    )
    return scheduler

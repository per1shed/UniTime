import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import get_settings
from bot.db.repository import ensure_universities
from bot.db.session import create_session_factory, init_db
from bot.handlers.user import router as user_router
from bot.services.notifications import setup_scheduler
from bot.services.sync import ScheduleSyncService
from bot.states.flow import FlowStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="change", description="Сменить группу"),
        ]
    )


async def main() -> None:
    settings = get_settings()
    session_factory = create_session_factory(settings)
    await init_db(session_factory)

    async with session_factory() as session:
        await ensure_universities(session)
        await session.commit()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await setup_bot_commands(bot)
    dp = Dispatcher()

    flow_storage = FlowStorage()
    sync_service = ScheduleSyncService(session_factory, settings)

    dp.include_router(user_router)

    dp["session_factory"] = session_factory
    dp["flow_storage"] = flow_storage
    dp["sync_service"] = sync_service

    async def run_initial_sync() -> None:
        logger.info("Starting initial schedule sync...")
        try:
            await sync_service.sync_all()
            logger.info("Initial schedule sync finished")
        except Exception:
            logger.exception("Initial schedule sync failed, bot will continue")

    scheduler = setup_scheduler(bot, session_factory, settings, sync_service)
    scheduler.start()

    logger.info("Bot started")
    sync_task = asyncio.create_task(run_initial_sync())
    try:
        await dp.start_polling(bot, session_factory=session_factory, flow_storage=flow_storage, sync_service=sync_service)
    finally:
        sync_task.cancel()
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import Settings
from bot.db.models import Base


def create_engine(settings: Settings):
    return create_async_engine(settings.database_url, echo=False)


def create_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    factory.engine = engine  # type: ignore[attr-defined]
    return factory


async def init_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    engine = session_factory.engine  # type: ignore[attr-defined]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_keyboard_message_id BIGINT")
        )


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session

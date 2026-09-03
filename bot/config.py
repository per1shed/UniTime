from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    timezone: str
    morning_hour: int
    morning_minute: int
    evening_hour: int
    evening_minute: int
    lesson_reminder_minutes: int
    break_reminder_minutes: int
    schedule_sync_hours: int
    rzgmu_sync_concurrency: int
    rsreu_sync_concurrency: int
    rsreu_proxy: str | None = None
    rsreu_cache_only: bool = False


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        user = os.getenv("POSTGRES_USER", "unitime")
        password = os.getenv("POSTGRES_PASSWORD", "unitime_secret")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "unitime")
        database_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token and os.getenv("UNITIME_SKIP_BOT_TOKEN") != "1":
        raise RuntimeError("BOT_TOKEN is not set")
    if not bot_token:
        bot_token = "unused"

    return Settings(
        bot_token=bot_token,
        database_url=database_url,
        timezone=os.getenv("TZ", "Europe/Moscow"),
        morning_hour=int(os.getenv("MORNING_HOUR", "6")),
        morning_minute=int(os.getenv("MORNING_MINUTE", "0")),
        evening_hour=int(os.getenv("EVENING_HOUR", "21")),
        evening_minute=int(os.getenv("EVENING_MINUTE", "0")),
        lesson_reminder_minutes=int(os.getenv("LESSON_REMINDER_MINUTES", "20")),
        break_reminder_minutes=int(os.getenv("BREAK_REMINDER_MINUTES", "5")),
        schedule_sync_hours=int(os.getenv("SCHEDULE_SYNC_HOURS", "12")),
        rzgmu_sync_concurrency=int(os.getenv("RZGMU_SYNC_CONCURRENCY", "6")),
        rsreu_sync_concurrency=int(os.getenv("RSREU_SYNC_CONCURRENCY", "4")),
        rsreu_proxy=os.getenv("RSREU_PROXY") or None,
        rsreu_cache_only=os.getenv("RSREU_CACHE_ONLY", "").lower() in {"1", "true", "yes"},
    )

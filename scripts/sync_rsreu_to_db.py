#!/usr/bin/env python3
"""Fetch RSREU on a machine with site access and push data to the bot database.

Run on Mac (where rasp.rsreu.ru opens) or via GitHub Actions (see .github/workflows/rsreu-sync.yml):

    python scripts/sync_rsreu_to_db.py

Requires DATABASE_URL in .env pointing to the VPS Postgres
(or SSH tunnel: postgresql+asyncpg://unitime:pass@127.0.0.1:5433/unitime).

On the VPS set RSREU_CACHE_ONLY=1 so the bot reads cache only and does not
call rasp.rsreu.ru directly.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import get_settings
from bot.db.models import ScheduleSource, Specialty, University
from bot.db.repository import ensure_universities, get_group_schedule, parse_rsreu_ref
from bot.db.session import create_session_factory, init_db
from bot.services.sync import ScheduleSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("sync_rsreu_to_db")

_FETCH_RETRIES = 3


def _schedule_is_cached(schedule: dict | None) -> bool:
    if not schedule:
        return False
    if schedule.get("__week__", {}).get("date"):
        return True
    return any(schedule.get(str(day)) for day in range(7))


async def cache_all_schedules(sync: ScheduleSyncService) -> tuple[int, int, int]:
    ok = 0
    failed = 0
    skipped = 0
    lock = asyncio.Lock()

    async with sync.session_factory() as session:
        stmt = (
            select(ScheduleSource)
            .join(Specialty)
            .join(University)
            .where(University.code == "rsreu")
            .options(selectinload(ScheduleSource.specialty))
        )
        sources = (await session.execute(stmt)).scalars().all()

    semaphore = asyncio.Semaphore(sync.settings.rsreu_sync_concurrency)

    async def cache_one(source: ScheduleSource) -> None:
        nonlocal ok, failed, skipped
        ref = parse_rsreu_ref(source.pdf_path)
        if not ref:
            return
        _, group_id = ref

        async with semaphore:
            async with sync.session_factory() as session:
                cached = await get_group_schedule(session, source.id, group_id)
                if _schedule_is_cached(cached):
                    async with lock:
                        skipped += 1
                    return

                last_error: Exception | None = None
                for attempt in range(_FETCH_RETRIES):
                    try:
                        schedule = await sync.load_rsreu_schedule(session, source.id, group_id)
                        await session.commit()
                        async with lock:
                            if schedule:
                                ok += 1
                            else:
                                failed += 1
                                logger.warning("Empty schedule for %s", source.variant_name)
                        return
                    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
                        last_error = exc
                        if attempt < _FETCH_RETRIES - 1:
                            delay = 5 * (attempt + 1)
                            logger.warning(
                                "Retry %s/%s for %s in %ss: %s",
                                attempt + 2,
                                _FETCH_RETRIES,
                                source.variant_name,
                                delay,
                                exc,
                            )
                            await asyncio.sleep(delay)
                    except Exception:
                        async with lock:
                            failed += 1
                        logger.exception(
                            "Failed to cache %s (source_id=%s)",
                            source.variant_name,
                            source.id,
                        )
                        return

                async with lock:
                    failed += 1
                logger.error(
                    "Failed to cache %s after %s attempts: %s",
                    source.variant_name,
                    _FETCH_RETRIES,
                    last_error,
                )

    await asyncio.gather(*(cache_one(source) for source in sources))
    return ok, failed, skipped


async def main() -> None:
    settings = get_settings()
    if settings.rsreu_cache_only:
        logger.error("Disable RSREU_CACHE_ONLY on the machine that fetches RSREU")
        sys.exit(1)

    session_factory = create_session_factory(settings)
    await init_db(session_factory)

    async with session_factory() as session:
        await ensure_universities(session)
        await session.commit()

    sync = ScheduleSyncService(session_factory, settings)
    logger.info("Syncing RSREU group list...")
    await sync.sync_rsreu_only()

    logger.info(
        "Caching schedules for all RSREU groups (concurrency=%s)...",
        settings.rsreu_sync_concurrency,
    )
    ok, failed, skipped = await cache_all_schedules(sync)
    logger.info(
        "Done: %s fetched, %s skipped (already cached), %s failed",
        ok,
        skipped,
        failed,
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

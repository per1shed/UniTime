import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, TelegramObject
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import User

logger = logging.getLogger(__name__)


@dataclass
class _Payload:
    text: str
    reply_markup: InlineKeyboardMarkup


class KeyboardTracker:
    """Keeps an inline keyboard only on the latest visible bot message."""

    def __init__(self) -> None:
        self._latest: dict[int, int] = {}
        self._known: dict[int, set[int]] = {}
        self._payload: dict[int, _Payload] = {}
        self._hydrated: set[int] = set()

    def known(self, chat_id: int) -> bool:
        return chat_id in self._hydrated

    def latest_id(self, chat_id: int) -> int | None:
        return self._latest.get(chat_id)

    def is_latest(self, chat_id: int, message_id: int) -> bool:
        return self._latest.get(chat_id) == message_id

    def mark_hydrated(self, chat_id: int) -> None:
        self._hydrated.add(chat_id)

    def remember_existing(self, chat_id: int, message_id: int) -> None:
        self._latest[chat_id] = message_id
        self._known.setdefault(chat_id, set()).add(message_id)
        self._hydrated.add(chat_id)

    def register(
        self,
        chat_id: int,
        message_id: int,
        *,
        text: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        self._latest[chat_id] = message_id
        self._known.setdefault(chat_id, set()).add(message_id)
        self._hydrated.add(chat_id)
        if text is not None and reply_markup is not None:
            self._payload[chat_id] = _Payload(text=text, reply_markup=reply_markup)

    def forget(self, chat_id: int) -> None:
        self._latest.pop(chat_id, None)
        self._known.pop(chat_id, None)
        self._payload.pop(chat_id, None)

    async def strip_message(self, bot: Bot, chat_id: int, message_id: int) -> None:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.debug("Could not remove keyboard from %s/%s: %s", chat_id, message_id, exc)
        except Exception:
            logger.exception("Failed to remove keyboard from %s/%s", chat_id, message_id)

    async def clear_old(self, bot: Bot, chat_id: int, *, except_message_id: int | None = None) -> None:
        ids = set(self._known.get(chat_id, set()))
        latest = self._latest.get(chat_id)
        if latest is not None:
            ids.add(latest)
        for message_id in ids:
            if message_id == except_message_id:
                continue
            await self.strip_message(bot, chat_id, message_id)
            known = self._known.get(chat_id)
            if known is not None:
                known.discard(message_id)
        if except_message_id is None and chat_id in self._known:
            self._known[chat_id].clear()

    async def restore_latest(self, bot: Bot, chat_id: int) -> None:
        message_id = self._latest.get(chat_id)
        payload = self._payload.get(chat_id)
        if message_id is None or payload is None:
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=payload.reply_markup,
            )
            self._known.setdefault(chat_id, set()).add(message_id)
        except TelegramBadRequest as exc:
            logger.debug("Could not restore keyboard on %s/%s: %s", chat_id, message_id, exc)
        except Exception:
            logger.exception("Failed to restore keyboard on %s/%s", chat_id, message_id)

    async def resend_latest(self, bot: Bot, chat_id: int) -> Message | None:
        payload = self._payload.get(chat_id)
        if payload is None:
            return None
        await self.clear_old(bot, chat_id)
        sent = await bot.send_message(
            chat_id,
            payload.text,
            reply_markup=payload.reply_markup,
        )
        self.register(
            chat_id,
            sent.message_id,
            text=payload.text,
            reply_markup=payload.reply_markup,
        )
        return sent


async def hydrate_tracker(
    tracker: KeyboardTracker,
    session_factory: async_sessionmaker[AsyncSession] | None,
    chat_id: int,
) -> None:
    if tracker.known(chat_id) or session_factory is None:
        return
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == chat_id))
        ).scalar_one_or_none()
        if user and user.last_keyboard_message_id:
            tracker.remember_existing(chat_id, user.last_keyboard_message_id)
        else:
            tracker.mark_hydrated(chat_id)


async def persist_keyboard_message(
    session_factory: async_sessionmaker[AsyncSession] | None,
    telegram_id: int,
    message_id: int,
) -> None:
    if session_factory is None:
        return
    try:
        async with session_factory() as session:
            await session.execute(
                update(User)
                .where(User.telegram_id == telegram_id)
                .values(last_keyboard_message_id=message_id)
            )
            await session.commit()
    except Exception:
        logger.debug("Could not persist keyboard message for %s", telegram_id, exc_info=True)


async def bind_keyboard(
    chat_id: int,
    message_id: int,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    tracker = get_keyboard_tracker()
    tracker.register(chat_id, message_id, text=text, reply_markup=reply_markup)
    await persist_keyboard_message(get_tracker_session_factory(), chat_id, message_id)


async def handle_incoming_user_message(message: Message) -> None:
    if message.from_user and message.from_user.is_bot:
        return

    factory = get_tracker_session_factory()
    tracker = get_keyboard_tracker()
    chat_id = message.chat.id
    await hydrate_tracker(tracker, factory, chat_id)
    await tracker.clear_old(message.bot, chat_id)

    is_command = bool(message.text and message.text.startswith("/"))
    if is_command:
        return

    deleted = False
    try:
        await message.delete()
        deleted = True
    except Exception:
        logger.debug("Could not delete user message in chat %s", chat_id, exc_info=True)

    if deleted:
        await tracker.restore_latest(message.bot, chat_id)
        return

    sent = await tracker.resend_latest(message.bot, chat_id)
    if sent is not None:
        await persist_keyboard_message(factory, chat_id, sent.message_id)


_tracker: KeyboardTracker | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def setup_keyboard_tracker(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = session_factory


def get_keyboard_tracker() -> KeyboardTracker:
    global _tracker
    if _tracker is None:
        _tracker = KeyboardTracker()
    return _tracker


def get_tracker_session_factory() -> async_sessionmaker[AsyncSession] | None:
    return _session_factory


class KeyboardGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            await handle_incoming_user_message(event)
        return await handler(event, data)

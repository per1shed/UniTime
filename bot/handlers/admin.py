import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import emoji as e
from bot.config import get_settings, is_admin
from bot.db.models import User
from bot.db.repository import (
    count_active_users_since,
    count_new_users_since,
    count_users,
    current_month_start,
    list_users_page,
    touch_user_activity,
)
from bot.keyboards.inline import admin_panel_keyboard, admin_search_prompt_keyboard
from bot.states.flow import FlowStorage

logger = logging.getLogger(__name__)

router = Router()
PAGE_SIZE = 8

class AdminSearchInput(Filter):
    async def __call__(self, message: Message, flow_storage: FlowStorage) -> bool:
        user = message.from_user
        return bool(
            user
            and is_admin(user.id)
            and flow_storage.is_admin_search(user.id)
            and message.text
            and not message.text.startswith("/")
        )


class ActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and event.from_user:
            factory: async_sessionmaker[AsyncSession] | None = data.get("session_factory")
            if factory is not None:
                try:
                    async with factory() as session:
                        await touch_user_activity(
                            session,
                            event.from_user.id,
                            event.from_user.username,
                            event.from_user.first_name,
                        )
                        await session.commit()
                except Exception:
                    logger.debug("Could not record button activity", exc_info=True)
        return await handler(event, data)


def _user_line(user: User) -> str:
    if user.username:
        nick = "@" + html.escape(user.username)
    else:
        nick = html.escape(user.first_name or "без имени")
    return f"· {nick} · <code>{user.telegram_id}</code>"


async def _admin_view(
    session: AsyncSession,
    *,
    page: int = 0,
    query: str | None = None,
) -> tuple[str, Any]:
    tz = get_settings().timezone
    since = current_month_start(tz)
    total_users = await count_users(session)
    new_users = await count_new_users_since(session, since)
    active_users = await count_active_users_since(session, since)

    page = max(page, 0)
    users, filtered_total = await list_users_page(
        session,
        offset=page * PAGE_SIZE,
        limit=PAGE_SIZE,
        query=query,
    )
    pages = max(1, (filtered_total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page >= pages:
        page = pages - 1
        users, filtered_total = await list_users_page(
            session,
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE,
            query=query,
        )

    lines = [
        f"{e.ce(e.USER, '👤')} Админка",
        "",
        f"Пользователи: <b>{total_users}</b>",
        f"Новые за месяц: <b>{new_users}</b>",
        f"Активные за месяц: <b>{active_users}</b>",
        "",
    ]
    if query:
        lines.append(f"Поиск: <b>{html.escape(query)}</b> · найдено {filtered_total}")
    else:
        lines.append(f"Список пользователей · {filtered_total}")
    lines.append("")
    if users:
        lines.extend(_user_line(user) for user in users)
    else:
        lines.append("Никого не найдено.")

    keyboard = admin_panel_keyboard(
        page=page,
        pages=pages,
        searching=bool(query),
    )
    return "\n".join(lines), keyboard


async def _show_admin_message(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
    *,
    page: int = 0,
) -> None:
    from bot.handlers.user import _send_with_keyboard

    query = flow_storage.get_admin_query(message.from_user.id)
    async with session_factory() as session:
        text, keyboard = await _admin_view(session, page=page, query=query)
    await _send_with_keyboard(message, text, keyboard)


async def _show_admin_callback(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
    *,
    page: int = 0,
) -> None:
    from bot.handlers.user import _edit_or_answer

    query = flow_storage.get_admin_query(callback.from_user.id)
    async with session_factory() as session:
        text, keyboard = await _admin_view(session, page=page, query=query)
    await _edit_or_answer(callback, text, keyboard)


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    if not is_admin(message.from_user.id):
        from bot.handlers.user import _send_entry

        await _send_entry(message, session_factory, ensure_user=True)
        return

    flow_storage.set_admin_search(message.from_user.id, False)
    flow_storage.set_admin_query(message.from_user.id, None)
    await _show_admin_message(message, session_factory, flow_storage)


@router.callback_query(F.data == "admin:home")
async def on_admin_home(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    flow_storage.set_admin_search(callback.from_user.id, False)
    flow_storage.set_admin_query(callback.from_user.id, None)
    await _show_admin_callback(callback, session_factory, flow_storage)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:list:"))
async def on_admin_list(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    flow_storage.set_admin_search(callback.from_user.id, False)
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    await _show_admin_callback(callback, session_factory, flow_storage, page=page)
    await callback.answer()


@router.callback_query(F.data == "admin:search")
async def on_admin_search(
    callback: CallbackQuery,
    flow_storage: FlowStorage,
) -> None:
    from bot.handlers.user import _edit_or_answer

    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    flow_storage.set_admin_search(callback.from_user.id, True)
    await _edit_or_answer(
        callback,
        f"{e.ce(e.PICK, '⬇️')} Введите ник или ID пользователя:",
        admin_search_prompt_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:noop")
async def on_admin_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(AdminSearchInput())
async def on_admin_search_text(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    flow_storage: FlowStorage,
) -> None:
    query = (message.text or "").strip()
    flow_storage.set_admin_search(message.from_user.id, False)
    flow_storage.set_admin_query(message.from_user.id, query)
    try:
        await message.delete()
    except Exception:
        logger.debug("Could not delete admin search query", exc_info=True)
    await _show_admin_message(message, session_factory, flow_storage)

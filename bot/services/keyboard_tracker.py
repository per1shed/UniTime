import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


class KeyboardTracker:
    """Keeps inline keyboard only on the latest bot message in a chat."""

    def __init__(self) -> None:
        self._messages: dict[int, int] = {}

    def register(self, chat_id: int, message_id: int) -> None:
        self._messages[chat_id] = message_id

    def forget(self, chat_id: int) -> None:
        self._messages.pop(chat_id, None)

    async def clear_old(self, bot: Bot, chat_id: int, *, except_message_id: int | None = None) -> None:
        message_id = self._messages.get(chat_id)
        if message_id is None or message_id == except_message_id:
            return

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
        finally:
            if self._messages.get(chat_id) == message_id:
                self.forget(chat_id)


_tracker: KeyboardTracker | None = None


def get_keyboard_tracker() -> KeyboardTracker:
    global _tracker
    if _tracker is None:
        _tracker = KeyboardTracker()
    return _tracker

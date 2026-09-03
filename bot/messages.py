from datetime import datetime
from zoneinfo import ZoneInfo

from bot import emoji as e
from bot.config import get_settings


def user_nick(first_name: str | None, username: str | None = None) -> str | None:
    if first_name and first_name.strip():
        return first_name.strip()
    return None


def step_prompt(text: str) -> str:
    label = text if text.endswith(":") else f"{text}:"
    return f"{e.ce(e.PICK, '⬇️')} {label}"


def time_of_day_greeting(now: datetime | None = None) -> str:
    """06–12 утро, 12–18 день, 18–00 вечер, 00–06 ночь."""
    moment = now or datetime.now(ZoneInfo(get_settings().timezone))
    hour = moment.hour
    if 6 <= hour < 12:
        return "доброе утро"
    if 12 <= hour < 18:
        return "добрый день"
    if 18 <= hour < 24:
        return "добрый вечер"
    return "доброй ночи"


def greeting_line(nick: str | None) -> str:
    greeting = time_of_day_greeting()
    if nick:
        return f"{e.ce(e.USER, '👤')} {nick}, {greeting}"
    return greeting.capitalize()


def main_menu_text(nick: str | None, selection: str | None = None) -> str:
    lines = [greeting_line(nick)]
    if selection:
        lines.extend(["", selection])
    lines.extend(["", step_prompt("Выберите действие")])
    return "\n".join(lines)


def entry_text(nick: str | None) -> str:
    return f"{greeting_line(nick)}\n\n{step_prompt('Выберите курс')}"


def loading_text(nick: str | None) -> str:
    return (
        f"{greeting_line(nick)}\n\n"
        "Расписание загружается, попробуйте через минуту."
    )


def step_text(nick: str | None, prompt: str, context: str | None = None) -> str:
    lines = [greeting_line(nick)]
    if context:
        lines.extend(["", context])
    lines.extend(["", step_prompt(prompt)])
    return "\n".join(lines)


def schedule_choice_text(context: str) -> str:
    return f"{context}\n\n{step_prompt('Выберите день')}"

from bot import emoji as e


def user_nick(first_name: str | None, username: str | None = None) -> str | None:
    if first_name and first_name.strip():
        return first_name.strip()
    return None


def step_prompt(text: str) -> str:
    label = text if text.endswith(":") else f"{text}:"
    return f"{e.ce(e.PICK, '⬇️')} {label}"


def greeting_line(nick: str | None) -> str:
    if nick:
        return f"{e.ce(e.USER, '👤')} {nick}, приветствуем в UniTime"
    return "Добро пожаловать в UniTime"


def main_menu_text(nick: str | None) -> str:
    return f"{greeting_line(nick)}\n\n{step_prompt('Выберите действие')}"


def entry_text(nick: str | None) -> str:
    return f"{greeting_line(nick)}\n\n{step_prompt('Выберите университет')}"


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

import re

# РГРТУ: номер группы кодирует курс по правилам вуза.
# 4-значные группы (5011, 6011): курс = 7 − первая цифра (6xxx → 1 курс, 5xxx → 2 курс…)
# 3-значные группы (110, 610): первая цифра = номер курса
# Источник: rsreu.ru — списки учебных групп по курсам


def course_from_group_label(label: str) -> int | None:
    label = label.strip()
    if not label:
        return None

    digits = re.sub(r"\D", "", label)
    if not digits:
        return None

    is_master = label.rstrip().upper().endswith(("М", "M"))

    if len(digits) >= 4 and not is_master:
        first = int(digits[0])
        if 1 <= first <= 6:
            return 7 - first
        return None

    if len(digits) == 3:
        first = int(digits[0])
        if 1 <= first <= 6:
            return first
        return None

    if is_master and len(digits) >= 3:
        first = int(digits[0])
        if 1 <= first <= 6:
            return first
        return None

    if len(digits) <= 2 and digits[0] != "0":
        first = int(digits[0])
        if 1 <= first <= 6:
            return first

    return None

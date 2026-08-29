from __future__ import annotations

from datetime import date


def is_study_day(day: date) -> bool:
    """Return True when classes or exam session are in progress (RZGMU-like calendar).

    Study periods:
    - Autumn semester + winter exam session: September 1 – January 31
    - Spring semester + summer exam session: February 9 – June 30

    Breaks (no notifications):
    - Winter holidays: February 1 – February 8
    - Summer holidays: July 1 – August 31
    - Before academic year: any date before September 1 (except Jan study days above)
    """
    month, dom = day.month, day.day

    # Winter holidays
    if month == 2 and dom <= 8:
        return False

    # Summer holidays
    if month >= 7 and month <= 8:
        return False

    # Autumn semester and winter exam session: September 1 – January 31
    if month >= 9 or month == 1:
        return True

    # Spring semester and summer exam session: February 9 – June 30
    if month == 2 and dom >= 9:
        return True
    if 3 <= month <= 6:
        return True

    return False

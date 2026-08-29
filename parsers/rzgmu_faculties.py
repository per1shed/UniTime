from dataclasses import dataclass


@dataclass(frozen=True)
class FacultyInfo:
    key: str
    name: str
    specialty_codes: tuple[str, ...]


RZGMU_FACULTIES: tuple[FacultyInfo, ...] = (
    FacultyInfo("lech", "Лечебный факультет", ("31.05.01",)),
    FacultyInfo("ped", "Педиатрический факультет", ("31.05.02",)),
    FacultyInfo("mpd", "Медико-профилактический факультет", ("32.05.01",)),
    FacultyInfo("stom", "Стоматологический факультет", ("31.05.03",)),
    FacultyInfo("farm", "Фармацевтический факультет", ("33.05.01",)),
    FacultyInfo("psy", "Факультет клинической психологии", ("37.05.01",)),
    FacultyInfo(
        "spo",
        "Среднее профессиональное образование",
        ("31.02.01", "34.02.01", "33.02.01", "31.02.03", "31.02.05"),
    ),
)


def faculty_for_code(code: str) -> FacultyInfo | None:
    for faculty in RZGMU_FACULTIES:
        if code in faculty.specialty_codes:
            return faculty
    return None

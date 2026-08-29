from dataclasses import dataclass


@dataclass(frozen=True)
class FacultyInfo:
    key: str
    name: str
    site_id: int


RSREU_FACULTIES: tuple[FacultyInfo, ...] = (
    FacultyInfo("frt", "ФРТ", 1),
    FacultyInfo("fe", "ФЭ", 2),
    FacultyInfo("faitu", "ФАИТУ", 3),
    FacultyInfo("fvt", "ФВТ", 4),
    FacultyInfo("ief", "ИЭФ", 5),
    FacultyInfo("oa", "ОА", 10),
    FacultyInfo("ido", "ИДО", 11),
)


def faculty_for_key(key: str) -> FacultyInfo | None:
    for faculty in RSREU_FACULTIES:
        if faculty.key == key:
            return faculty
    return None


def faculty_for_site_id(site_id: int) -> FacultyInfo | None:
    for faculty in RSREU_FACULTIES:
        if faculty.site_id == site_id:
            return faculty
    return None

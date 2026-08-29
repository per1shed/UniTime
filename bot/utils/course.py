from parsers.rsreu_course import course_from_group_label as course_from_rsreu_group_label


def effective_course_number(
    course_number: int,
    variant_name: str,
    university_code: str | None = None,
) -> int | None:
    if course_number > 0:
        return course_number
    if university_code == "rsreu":
        return course_from_rsreu_group_label(variant_name)
    return None

from dataclasses import dataclass, field


@dataclass
class UserFlowState:
    university_id: int | None = None
    faculty_key: str | None = None
    specialty_id: int | None = None
    course_number: int | None = None
    course_source_id: int | None = None
    variant_source_id: int | None = None


class FlowStorage:
    def __init__(self) -> None:
        self._states: dict[int, UserFlowState] = {}
        self._admin_search: set[int] = set()
        self._admin_query: dict[int, str] = {}

    def get(self, user_id: int) -> UserFlowState:
        if user_id not in self._states:
            self._states[user_id] = UserFlowState()
        return self._states[user_id]

    def reset(self, user_id: int) -> None:
        self._states[user_id] = UserFlowState()
        self._admin_search.discard(user_id)
        self._admin_query.pop(user_id, None)

    def set_admin_search(self, user_id: int, enabled: bool) -> None:
        if enabled:
            self._admin_search.add(user_id)
        else:
            self._admin_search.discard(user_id)

    def is_admin_search(self, user_id: int) -> bool:
        return user_id in self._admin_search

    def set_admin_query(self, user_id: int, query: str | None) -> None:
        if query:
            self._admin_query[user_id] = query
        else:
            self._admin_query.pop(user_id, None)

    def get_admin_query(self, user_id: int) -> str | None:
        return self._admin_query.get(user_id)

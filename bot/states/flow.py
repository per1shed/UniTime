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

    def get(self, user_id: int) -> UserFlowState:
        if user_id not in self._states:
            self._states[user_id] = UserFlowState()
        return self._states[user_id]

    def reset(self, user_id: int) -> None:
        self._states[user_id] = UserFlowState()

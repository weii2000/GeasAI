from dataclasses import dataclass

from .types import Phase


@dataclass(frozen=True)
class Profile:
    prompt: str = ""
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


BASE_PROFILE = Profile(
    tools=("web_search",),
)

PHASE_PROFILES = {
    Phase.PLAN: Profile(
        tools=("update_plan", "submit_plan"),
    ),
    Phase.REVIEW: Profile(
        tools=(
            "update_review_report",
            "request_change",
            "approve_plan",
        ),
    ),
    Phase.IDLE: Profile(
        tools=("request_change",),
    ),
}

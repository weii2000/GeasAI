from geas.ai.event_stream import EventStream
from geas.ai.types import Message

from .types import AgentRunEndEvent, AgentRunEvent


def _is_run_done(event: AgentRunEvent) -> bool:
    return isinstance(event, AgentRunEndEvent)


def _get_run_result(event: AgentRunEvent) -> list[Message]:
    if isinstance(event, AgentRunEndEvent):
        return event.messages
    raise ValueError("Agent run stream has not finished")


class AgentRunStream(EventStream[AgentRunEvent, list[Message]]):
    def __init__(self) -> None:
        super().__init__(_is_run_done, _get_run_result)

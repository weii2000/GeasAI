from geas.ai.event_stream import EventStream
from geas.ai.types import Message

from .types import AgentEndEvent, AgentEvent


def _is_agent_done(event: AgentEvent) -> bool:
    return isinstance(event, AgentEndEvent)


def _get_agent_result(event: AgentEvent) -> list[Message]:
    if isinstance(event, AgentEndEvent):
        return event.messages
    raise ValueError("Agent stream has not finished")


class AgentEventStream(EventStream[AgentEvent, list[Message]]):
    def __init__(self) -> None:
        super().__init__(_is_agent_done, _get_agent_result)

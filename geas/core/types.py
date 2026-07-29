from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from geas.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    ImageContent,
    Message,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
)


@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent]
    details: object | None = None


type ToolExecute = Callable[
    [str, dict[str, object]],
    Awaitable[AgentToolResult],
]


@dataclass
class AgentTool(Tool):
    execute: ToolExecute


@dataclass
class AgentContext:
    messages: list[Message]
    system_prompt: str = ""
    tools: list[AgentTool] = field(default_factory=list)


type PrepareNextTurn = Callable[
    [AgentContext],
    Awaitable[AgentContext | None],
]

type ShouldStopAfterTurn = Callable[
    ["TurnEndEvent"],
    Awaitable[bool],
]


@dataclass
class AgentState:
    model: Model
    system_prompt: str = ""
    tools: list[AgentTool] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    is_streaming: bool = False
    streaming_message: Message | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error_message: str | None = None


@dataclass
class AgentLoopConfig:
    model: Model
    prepare_next_turn: PrepareNextTurn | None = None
    should_stop_after_turn: ShouldStopAfterTurn | None = None


@dataclass
class AgentStartEvent:
    type: Literal["agent_start"]


@dataclass
class AgentEndEvent:
    type: Literal["agent_end"]
    messages: list[Message]


@dataclass
class TurnStartEvent:
    type: Literal["turn_start"]


@dataclass
class TurnEndEvent:
    type: Literal["turn_end"]
    message: AssistantMessage
    tool_results: list[ToolResultMessage]


@dataclass
class MessageStartEvent:
    type: Literal["message_start"]
    message: Message


@dataclass
class MessageUpdateEvent:
    type: Literal["message_update"]
    message: AssistantMessage
    assistant_message_event: AssistantMessageEvent


@dataclass
class MessageEndEvent:
    type: Literal["message_end"]
    message: Message


@dataclass
class ToolExecutionStartEvent:
    type: Literal["tool_execution_start"]
    tool_call_id: str
    tool_name: str
    args: dict[str, object]


@dataclass
class ToolExecutionEndEvent:
    type: Literal["tool_execution_end"]
    tool_call_id: str
    tool_name: str
    result: AgentToolResult
    is_error: bool


type AgentEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionEndEvent
)

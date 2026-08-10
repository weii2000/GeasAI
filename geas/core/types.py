from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from geas.ai.types import (
    AssistantMessage,
    AssistantResponseEvent,
    ImageContent,
    Message,
    Model,
    TextContent,
    Tool,
    ToolCall,
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


type BeforeTurnHook = Callable[
    [AgentContext],
    Awaitable[AgentContext],
]

type BeforeToolCallHook = Callable[
    [ToolCall],
    Awaitable[bool],
]

type AfterToolCallHook = Callable[
    ["ToolExecutionEndEvent"],
    Awaitable[bool],
]

type AfterTurnHook = Callable[
    ["TurnEndEvent"],
    Awaitable[bool],
]


@dataclass
class AgentHooks:
    before_turn: list[BeforeTurnHook] = field(default_factory=list)
    after_turn: list[AfterTurnHook] = field(default_factory=list)
    before_tool_call: list[BeforeToolCallHook] = field(default_factory=list)
    after_tool_call: list[AfterToolCallHook] = field(default_factory=list)


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
    hooks: AgentHooks = field(default_factory=AgentHooks)


@dataclass
class AgentRunStartEvent:
    type: Literal["agent_start"]


@dataclass
class AgentRunEndEvent:
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
    assistant_response_event: AssistantResponseEvent


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


type AgentRunEvent = (
    AgentRunStartEvent
    | AgentRunEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionEndEvent
)

from dataclasses import dataclass
from typing import Literal


@dataclass
class TextContent:
    type: Literal["text"]
    text: str
    text_signature: str | None = None


@dataclass
class ThinkingContent:
    type: Literal["thinking"]
    thinking: str
    thinking_signature: str | None = None
    redacted: bool | None = None


@dataclass
class ImageContent:
    type: Literal["image"]
    data: str
    mime_type: str


@dataclass
class ToolCall:
    type: Literal["toolCall"]
    id: str
    name: str
    arguments: dict[str, object]
    thought_signature: str | None = None


type UserContent = str | list[TextContent | ImageContent]


@dataclass
class UserMessage:
    role: Literal["user"]
    content: UserContent
    timestamp: int


@dataclass
class UsageCost:
    input: float
    output: float
    cache_read: float
    cache_write: float
    total: float


@dataclass
class Usage:
    input: int
    output: int
    cache_read: int
    cache_write: int
    total_tokens: int
    cost: UsageCost
    cache_write_1h: int | None = None
    reasoning: int | None = None


type StopReason = Literal[
    "pending",
    "stop",
    "length",
    "toolUse",
    "error",
    "aborted",
]

type AssistantContent = TextContent | ThinkingContent | ToolCall


@dataclass
class AssistantMessage:
    role: Literal["assistant"]
    content: list[AssistantContent]
    api: str
    provider: str
    model: str
    usage: Usage
    stop_reason: StopReason
    timestamp: int
    response_model: str | None = None
    response_id: str | None = None
    error_message: str | None = None


type ToolResultContent = TextContent | ImageContent


@dataclass
class ToolResultMessage:
    role: Literal["toolResult"]
    tool_call_id: str
    tool_name: str
    content: list[ToolResultContent]
    is_error: bool
    timestamp: int
    details: object | None = None
    usage: Usage | None = None
    added_tool_names: list[str] | None = None


type Message = UserMessage | AssistantMessage | ToolResultMessage


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, object]


@dataclass
class Context:
    messages: list[Message]
    system_prompt: str | None = None
    tools: list[Tool] | None = None


type InputModality = Literal["text", "image"]

type ModelThinkingLevel = Literal[
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]


@dataclass
class ModelCost:
    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass
class Model:
    id: str
    name: str
    api: str
    provider: str
    base_url: str
    reasoning: bool
    input: list[InputModality]
    cost: ModelCost
    context_window: int
    max_tokens: int
    thinking_level_map: dict[ModelThinkingLevel, str | None] | None = None
    headers: dict[str, str] | None = None
    compat: dict[str, object] | None = None


@dataclass
class ResponseStartEvent:
    type: Literal["start"]
    partial: AssistantMessage


@dataclass
class TextStartEvent:
    type: Literal["text_start"]
    content_index: int
    partial: AssistantMessage


@dataclass
class TextDeltaEvent:
    type: Literal["text_delta"]
    content_index: int
    delta: str
    partial: AssistantMessage


@dataclass
class TextEndEvent:
    type: Literal["text_end"]
    content_index: int
    content: str
    partial: AssistantMessage


@dataclass
class ThinkingStartEvent:
    type: Literal["thinking_start"]
    content_index: int
    partial: AssistantMessage


@dataclass
class ThinkingDeltaEvent:
    type: Literal["thinking_delta"]
    content_index: int
    delta: str
    partial: AssistantMessage


@dataclass
class ThinkingEndEvent:
    type: Literal["thinking_end"]
    content_index: int
    content: str
    partial: AssistantMessage


@dataclass
class ToolCallStartEvent:
    type: Literal["toolcall_start"]
    content_index: int
    partial: AssistantMessage


@dataclass
class ToolCallDeltaEvent:
    type: Literal["toolcall_delta"]
    content_index: int
    delta: str
    partial: AssistantMessage


@dataclass
class ToolCallEndEvent:
    type: Literal["toolcall_end"]
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


type DoneReason = Literal["stop", "length", "toolUse"]
type ErrorReason = Literal["error", "aborted"]


@dataclass
class ResponseDoneEvent:
    type: Literal["done"]
    reason: DoneReason
    message: AssistantMessage


@dataclass
class ResponseErrorEvent:
    type: Literal["error"]
    reason: ErrorReason
    error: AssistantMessage


type AssistantResponseEvent = (
    ResponseStartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | ResponseDoneEvent
    | ResponseErrorEvent
)


@dataclass
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    api_key: str | None = None
    headers: dict[str, str | None] | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None

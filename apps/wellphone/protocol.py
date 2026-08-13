from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


TaskStatus = Literal["running", "waiting_for_phone", "completed", "failed"]


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: UUID | None = None
    session_id: UUID | None = None
    prompt: str = Field(min_length=1)
    device_context: str | None = Field(default=None, max_length=500)


class TaskResponse(BaseModel):
    id: str
    session_id: str
    prompt: str
    status: TaskStatus
    answer: str | None = None
    error: str | None = None


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    timestamp: str


class SessionResponse(BaseModel):
    id: str
    created_at: str
    updated_at: str
    messages: list[ConversationMessageResponse]


class ToolCallEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    call_id: str
    name: str
    arguments: dict[str, object]


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str = Field(min_length=1)
    result: dict[str, object]
    is_error: bool = False

    def for_model(self) -> str:
        return json.dumps(
            {**self.result, "ok": not self.is_error},
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ToolPollResponse(BaseModel):
    tool_call: ToolCallEnvelope | None

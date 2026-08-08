from dataclasses import replace
from typing import Literal

from geas.ai.event_stream import AssistantResponseStream
from geas.ai.providers.deepseek import DEEPSEEK_MODELS
from geas.ai.types import (
    AssistantMessage,
    Context,
    ResponseDoneEvent,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    UsageCost,
)
from geas.core.agent import Agent
from geas.core.types import AgentState
from geas.plan_agent.session import (
    PLAN_AGENT_MAX_TURNS,
    REVIEW_AGENT_MAX_TURNS,
    PlanSession,
)
from geas.plan_agent.skills import SkillRegistry


class ScriptedModel:
    def __init__(self, responses: list[AssistantMessage]) -> None:
        self._responses = iter(responses)
        self.contexts: list[Context] = []
        self.models: list[Model] = []

    def __call__(
        self,
        model: Model,
        context: Context,
        _options: StreamOptions | None = None,
    ) -> AssistantResponseStream:
        self.models.append(model)
        self.contexts.append(context)
        stream = AssistantResponseStream()
        message = next(self._responses)
        if message.stop_reason not in ("stop", "length", "toolUse"):
            raise ValueError("Scripted response must be a done message")
        stream.push(
            ResponseDoneEvent(
                type="done",
                reason=message.stop_reason,
                message=message,
            )
        )
        return stream


def make_session(
    plan_responses: list[AssistantMessage],
    review_responses: list[AssistantMessage] | None = None,
    skill_registry: SkillRegistry | None = None,
) -> tuple[PlanSession, ScriptedModel, ScriptedModel]:
    plan_model = ScriptedModel(plan_responses)
    review_model = ScriptedModel(review_responses or [])
    plan_agent = Agent(
        state=AgentState(model=DEEPSEEK_MODELS[0]),
        stream_function=plan_model,
        max_turns=PLAN_AGENT_MAX_TURNS,
    )
    review_agent = Agent(
        state=AgentState(
            model=replace(
                DEEPSEEK_MODELS[0],
                id="review-model",
                provider="review-test",
            )
        ),
        stream_function=review_model,
        max_turns=REVIEW_AGENT_MAX_TURNS,
    )
    return (
        PlanSession(plan_agent, review_agent, skill_registry),
        plan_model,
        review_model,
    )


def make_assistant(
    content: list[TextContent | ThinkingContent | ToolCall],
    stop_reason: Literal["stop", "toolUse"],
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=content,
        api="test",
        provider="test",
        model="test",
        usage=Usage(
            input=0,
            output=0,
            cache_read=0,
            cache_write=0,
            total_tokens=0,
            cost=UsageCost(
                input=0,
                output=0,
                cache_read=0,
                cache_write=0,
                total=0,
            ),
        ),
        stop_reason=stop_reason,
        timestamp=0,
    )


def make_tool_call(
    name: str,
    arguments: dict[str, object],
) -> ToolCall:
    return ToolCall(
        type="toolCall",
        id=f"{name}-call",
        name=name,
        arguments=arguments,
    )

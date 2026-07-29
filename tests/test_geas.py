import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Literal, cast

import pytest

from geas.ai.deepseek_models import DEEPSEEK_MODELS
from geas.ai.event_stream import AssistantMessageEventStream
from geas.ai.openai_completions import (
    _convert_messages,
    _reasoning_delta,
)
from geas.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from geas.core.agent import Agent
from geas.core.types import AgentState
from geas.plan_agent.session import PlanSession
from geas.plan_agent.types import (
    IssueSeverity,
    Phase,
    ReviewIssue,
    ReviewReport,
    Task,
)


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
    ) -> AssistantMessageEventStream:
        self.models.append(model)
        self.contexts.append(context)
        stream = AssistantMessageEventStream()
        message = next(self._responses)
        if message.stop_reason not in ("stop", "length", "toolUse"):
            raise ValueError("Scripted response must be a done message")
        stream.push(
            DoneEvent(
                type="done",
                reason=message.stop_reason,
                message=message,
            )
        )
        return stream


def test_plan_session_runs_plan_review_to_idle() -> None:
    session, plan_model, review_model = _session(
        plan_responses=[
            _assistant(
                [
                    ThinkingContent(
                        type="thinking",
                        thinking="先制定计划",
                    ),
                    _tool_call(
                        "update_plan",
                        {
                            "goal": "发布 Geas",
                            "description": "完成可运行的 Agent",
                            "acceptance_criterion": "可以完成规划和评审",
                            "tasks": [
                                {
                                    "title": "实现 Agent",
                                    "level": 1,
                                }
                            ],
                        },
                    ),
                    _tool_call("submit_plan", {}),
                ],
                "toolUse",
            ),
        ],
        review_responses=[
            _assistant(
                [
                    ThinkingContent(
                        type="thinking",
                        thinking="独立审查计划",
                    ),
                    _tool_call(
                        "update_review_report",
                        {
                            "summary": "计划可以执行",
                            "issues": [],
                        },
                    ),
                    _tool_call("approve_plan", {}),
                ],
                "toolUse",
            ),
        ],
    )

    asyncio.run(session.prompt("帮我规划 Geas"))

    assert session.phase is Phase.IDLE
    assert session.plan.goal == "发布 Geas"
    assert session.review_report == ReviewReport(
        summary="计划可以执行",
    )
    assert len(plan_model.contexts) == 1
    assert len(review_model.contexts) == 1
    plan_prompt = plan_model.contexts[0].system_prompt
    review_prompt = review_model.contexts[0].system_prompt
    assert plan_prompt is not None
    assert review_prompt is not None
    assert "PLAN 阶段" in plan_prompt
    assert "REVIEW 阶段" in review_prompt
    assert plan_model.models[0].provider == "deepseek"
    assert review_model.models[0].provider == "review-test"
    assert all(
        isinstance(message, UserMessage)
        for message in review_model.contexts[0].messages
    )
    assert any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, ThinkingContent)
            for block in message.content
        )
        for message in session.plan_agent.state.messages
    )
    assert not any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, ThinkingContent)
            and block.thinking == "先制定计划"
            for block in message.content
        )
        for message in session.review_agent.state.messages
    )

    plan_tools = {
        tool.name
        for tool in plan_model.contexts[0].tools or []
    }
    review_tools = {
        tool.name
        for tool in review_model.contexts[0].tools or []
    }
    assert {"web_search", "update_plan", "submit_plan"} == plan_tools
    assert "update_plan" not in review_tools
    assert {
        "web_search",
        "update_review_report",
        "request_change",
        "approve_plan",
    } == review_tools


def test_invalid_tool_arguments_do_not_update_plan() -> None:
    session, plan_model, _review_model = _session(
        plan_responses=[
            _assistant(
                [
                    ThinkingContent(
                        type="thinking",
                        thinking="继续在 PLAN 阶段修复参数",
                    ),
                    _tool_call(
                        "update_plan",
                        {
                            "goal": "缺少必要字段",
                        },
                    )
                ],
                "toolUse",
            ),
            _assistant(
                [TextContent(type="text", text="参数错误。")],
                "stop",
            ),
        ]
    )

    asyncio.run(session.prompt("创建计划"))

    assert session.plan.goal == ""
    tool_results = [
        message
        for message in plan_model.contexts[1].messages
        if isinstance(message, ToolResultMessage)
    ]
    assert tool_results[-1].is_error
    assert "Invalid arguments" in tool_results[-1].content[0].text
    assert any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, ThinkingContent)
            for block in message.content
        )
        for message in plan_model.contexts[1].messages
    )


def test_blocking_review_cannot_be_approved() -> None:
    session, _plan_model, _review_model = _session([])
    session.submit_plan()
    session.update_review_report(
        ReviewReport(
            summary="计划缺少关键内容",
            issues=[
                ReviewIssue(
                    description="没有验收标准",
                    evidence="acceptance_criterion 为空",
                    severity=IssueSeverity.BLOCKING,
                ),
            ],
        )
    )

    with pytest.raises(
        ValueError,
        match="blocking review issues",
    ):
        session.approve_plan()

    assert session.phase is Phase.REVIEW


def test_task_tree_rejects_skipped_levels() -> None:
    with pytest.raises(
        ValueError,
        match="one level below",
    ):
        Task(
            title="一级任务",
            level=1,
            subtasks=[
                Task(title="错误的三级任务", level=3),
            ],
        )


def test_openai_compat_preserves_reasoning_field() -> None:
    model = replace(
        DEEPSEEK_MODELS[0],
        provider="example",
        base_url="https://example.com/v1",
    )
    message = _assistant(
        [
            ThinkingContent(
                type="thinking",
                thinking="继续调用工具",
                thinking_signature="reasoning",
            ),
            _tool_call("example_tool", {}),
        ],
        "toolUse",
    )
    message.provider = "example"

    converted = _convert_messages(
        model,
        Context(messages=[message]),
    )
    converted_message = cast(dict[str, object], converted[0])

    assert converted_message["reasoning"] == "继续调用工具"
    assert "reasoning_content" not in converted_message


def test_deepseek_replays_empty_reasoning_content() -> None:
    message = _assistant(
        [_tool_call("example_tool", {})],
        "toolUse",
    )

    converted = _convert_messages(
        DEEPSEEK_MODELS[0],
        Context(messages=[message]),
    )
    converted_message = cast(dict[str, object], converted[0])

    assert converted_message["reasoning_content"] == ""


def test_reasoning_delta_uses_first_non_empty_field() -> None:
    delta = SimpleNamespace(
        reasoning_content="",
        reasoning="thinking",
        reasoning_text="duplicate",
    )

    assert _reasoning_delta(delta) == ("reasoning", "thinking")


def _session(
    plan_responses: list[AssistantMessage],
    review_responses: list[AssistantMessage] | None = None,
) -> tuple[PlanSession, ScriptedModel, ScriptedModel]:
    plan_model = ScriptedModel(plan_responses)
    review_model = ScriptedModel(review_responses or [])
    plan_agent = Agent(
        state=AgentState(model=DEEPSEEK_MODELS[0]),
        stream_function=plan_model,
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
    )
    return (
        PlanSession(plan_agent, review_agent),
        plan_model,
        review_model,
    )


def _assistant(
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


def _tool_call(
    name: str,
    arguments: dict[str, object],
) -> ToolCall:
    return ToolCall(
        type="toolCall",
        id=f"{name}-call",
        name=name,
        arguments=arguments,
    )

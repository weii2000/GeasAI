import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from geas.ai.event_stream import AssistantResponseStream
from geas.ai.apis.openai_completions import (
    _convert_messages,
    _reasoning_delta,
)
from geas.ai.providers import builtin_models
from geas.ai.providers.deepseek import DEEPSEEK_MODELS, _deepseek_cost
from geas.ai.types import (
    Context,
    ModelCost,
    ResponseErrorEvent,
    TextContent,
    ThinkingContent,
    ToolCall,
)
from geas.core.agent import Agent
from geas.core.types import (
    AgentContext,
    AgentHooks,
    AgentState,
    AgentTool,
    AgentToolResult,
    ToolExecutionEndEvent,
    TurnEndEvent,
)

from .helpers import ScriptedModel, make_assistant, make_tool_call


def test_builtin_models_registers_catalogs() -> None:
    models = builtin_models()

    assert set(models.get_providers()) == {
        "deepseek",
        "dashscope",
        "moonshot",
        "zai",
    }


def test_openai_compat_preserves_reasoning_field() -> None:
    model = replace(
        DEEPSEEK_MODELS[0],
        provider="example",
        base_url="https://example.com/v1",
    )
    message = make_assistant(
        [
            ThinkingContent(
                type="thinking",
                thinking="继续调用工具",
                thinking_signature="reasoning",
            ),
            make_tool_call("example_tool", {}),
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
    message = make_assistant(
        [make_tool_call("example_tool", {})],
        "toolUse",
    )

    converted = _convert_messages(
        DEEPSEEK_MODELS[0],
        Context(messages=[message]),
    )
    converted_message = cast(dict[str, object], converted[0])

    assert converted_message["reasoning_content"] == ""


def test_builtin_pricing_uses_latest_cny_rates() -> None:
    off_peak = _deepseek_cost(
        "deepseek-v4-flash",
        datetime(2026, 8, 16, 16, tzinfo=UTC),
    )
    peak = _deepseek_cost(
        "deepseek-v4-pro",
        datetime(2026, 8, 17, 6, tzinfo=UTC),
    )
    models = builtin_models()
    kimi = models.get_model("moonshot", "kimi-k3")
    qwen = models.get_model("dashscope", "qwen3.7-plus")
    glm = models.get_model("zai", "glm-5.2")

    assert (off_peak.input, off_peak.output, off_peak.cache_read) == (
        1.5,
        4.5,
        0.05,
    )
    assert (peak.input, peak.output, peak.cache_read) == (9.0, 27.0, 0.30)
    assert kimi is not None and kimi.cost == ModelCost(20, 100, 2, 0)
    assert qwen is not None and qwen.cost == ModelCost(2, 8, 0.4, 0)
    assert glm is not None and glm.cost == ModelCost(8, 28, 2, 0)


def test_reasoning_delta_uses_first_non_empty_field() -> None:
    delta = SimpleNamespace(
        reasoning_content="",
        reasoning="thinking",
        reasoning_text="duplicate",
    )

    assert _reasoning_delta(delta) == ("reasoning", "thinking")


def test_agent_hooks_run_in_lifecycle_and_registration_order() -> None:
    calls: list[str] = []

    async def before_turn_one(context: AgentContext) -> AgentContext:
        calls.append("before_turn_one")
        return context

    async def before_turn_two(context: AgentContext) -> AgentContext:
        calls.append("before_turn_two")
        return context

    async def before_tool_call(_tool_call: ToolCall) -> bool:
        calls.append("before_tool_call")
        return False

    async def execute_tool(
        _tool_call_id: str,
        _args: dict[str, object],
    ) -> AgentToolResult:
        calls.append("execute_tool")
        return AgentToolResult(
            content=[TextContent(type="text", text="done")]
        )

    async def after_tool_call(
        _event: ToolExecutionEndEvent,
    ) -> bool:
        calls.append("after_tool_call")
        return False

    async def after_turn(_event: TurnEndEvent) -> bool:
        calls.append("after_turn")
        return False

    stream = ScriptedModel(
        [
            make_assistant(
                [make_tool_call("example_tool", {})],
                "toolUse",
            ),
            make_assistant(
                [TextContent(type="text", text="finished")],
                "stop",
            ),
        ]
    )
    agent = Agent(
        state=AgentState(
            model=DEEPSEEK_MODELS[0],
            tools=[
                AgentTool(
                    name="example_tool",
                    description="Example tool",
                    parameters={"type": "object"},
                    execute=execute_tool,
                )
            ],
        ),
        stream_function=stream,
        max_turns=2,
        hooks=AgentHooks(
            before_turn=[before_turn_one, before_turn_two],
            after_turn=[after_turn],
            before_tool_call=[before_tool_call],
            after_tool_call=[after_tool_call],
        ),
    )

    asyncio.run(agent.prompt("run"))

    assert calls == [
        "before_turn_one",
        "before_turn_two",
        "before_tool_call",
        "execute_tool",
        "after_tool_call",
        "after_turn",
        "before_turn_one",
        "before_turn_two",
        "after_turn",
    ]


def test_failed_response_is_reported_and_not_replayed() -> None:
    failed = make_assistant(
        [make_tool_call("broken_tool", {})],
        "toolUse",
    )
    failed.stop_reason = "error"
    failed.error_message = "invalid tool arguments"

    def stream(*_args: object) -> AssistantResponseStream:
        events = AssistantResponseStream()
        events.push(
            ResponseErrorEvent(type="error", reason="error", error=failed)
        )
        return events

    agent = Agent(
        AgentState(model=DEEPSEEK_MODELS[0]),
        stream,
        max_turns=1,
    )
    with pytest.raises(RuntimeError, match="invalid tool arguments"):
        asyncio.run(agent.prompt("continue"))

    converted = _convert_messages(
        DEEPSEEK_MODELS[0],
        Context(messages=agent.state.messages),
    )
    assert [message["role"] for message in converted] == ["user"]

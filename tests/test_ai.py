from dataclasses import replace
from types import SimpleNamespace
from typing import cast

from geas.ai.openai_completions import (
    _convert_messages,
    _reasoning_delta,
)
from geas.ai.providers import builtin_models
from geas.ai.providers.deepseek import DEEPSEEK_MODELS
from geas.ai.types import Context, ThinkingContent

from .helpers import make_assistant, make_tool_call


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


def test_reasoning_delta_uses_first_non_empty_field() -> None:
    delta = SimpleNamespace(
        reasoning_content="",
        reasoning="thinking",
        reasoning_text="duplicate",
    )

    assert _reasoning_delta(delta) == ("reasoning", "thinking")

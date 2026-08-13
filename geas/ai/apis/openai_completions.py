import asyncio
import json
import os
import time
from typing import cast

from openai import AsyncOpenAI, omit
from openai.types.completion_usage import CompletionUsage
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from ..event_stream import AssistantResponseStream
from ..types import (
    AssistantMessage,
    Context,
    ResponseDoneEvent,
    DoneReason,
    ResponseErrorEvent,
    ImageContent,
    Model,
    ResponseStartEvent,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)

_REASONING_FIELDS = (
    "reasoning_content",
    "reasoning",
    "reasoning_text",
)


def _reasoning_delta(delta: object) -> tuple[str, str] | None:
    for field in _REASONING_FIELDS:
        value = getattr(delta, field, None)
        if isinstance(value, str) and value:
            return field, value
    return None


def _requires_reasoning_content(model: Model) -> bool:
    detected = (
        model.provider == "deepseek"
        or "deepseek.com" in model.base_url
    )
    override = (model.compat or {}).get(
        "requires_reasoning_content_on_assistant_messages"
    )
    return override if isinstance(override, bool) else detected


def _content_to_text(
    content: str | list[TextContent | ImageContent],
) -> str:
    if isinstance(content, str):
        return content

    texts: list[str] = []

    for block in content:
        if isinstance(block, ImageContent):
            raise NotImplementedError(
                "Image inputs are not implemented for openai-completions"
            )

        texts.append(block.text)

    return "".join(texts)


def _convert_messages(
    model: Model,
    context: Context,
) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = []

    if context.system_prompt is not None:
        messages.append({
            "role": "system",
            "content": context.system_prompt,
        })

    for message in context.messages:
        if isinstance(message, UserMessage):
            messages.append({
                "role": "user",
                "content": _content_to_text(message.content),
            })
            continue

        if isinstance(message, AssistantMessage):
            if message.stop_reason in ("error", "aborted"):
                continue

            text = "".join(
                block.text
                for block in message.content
                if isinstance(block, TextContent)
            )
            thinking_blocks = [
                block
                for block in message.content
                if isinstance(block, ThinkingContent)
            ]
            thinking = "\n".join(
                block.thinking for block in thinking_blocks
            )
            tool_calls = [
                block
                for block in message.content
                if isinstance(block, ToolCall)
            ]

            if not text and not thinking and not tool_calls:
                continue

            assistant_message: dict[str, object] = {
                "role": "assistant",
                "content": text or None,
            }

            signature = next(
                (
                    block.thinking_signature
                    for block in thinking_blocks
                    if block.thinking_signature
                ),
                None,
            )
            same_provider = message.provider == model.provider

            if thinking and same_provider and signature:
                assistant_message[signature] = thinking
            elif thinking and same_provider and _requires_reasoning_content(
                model
            ):
                assistant_message["reasoning_content"] = thinking
            elif thinking:
                assistant_message["content"] = "\n\n".join(
                    part
                    for part in (
                        f"<thinking>\n{thinking}\n</thinking>",
                        text,
                    )
                    if part
                )

            if (
                model.reasoning
                and _requires_reasoning_content(model)
            ):
                assistant_message.setdefault("reasoning_content", "")

            if tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(
                                tool_call.arguments,
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for tool_call in tool_calls
                ]

            messages.append(
                cast(ChatCompletionMessageParam, assistant_message)
            )
            continue

        if isinstance(message, ToolResultMessage):
            messages.append({
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": _content_to_text(message.content),
            })

    return messages


def _convert_tools(
    tools: list[Tool] | None,
) -> list[ChatCompletionToolParam] | None:
    if not tools:
        return None

    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def _parse_tool_arguments(arguments: str) -> dict[str, object]:
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Model returned invalid tool arguments") from error

    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be a JSON object")

    return cast(dict[str, object], value)


def _create_partial_message(model: Model) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
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
        stop_reason="pending",
        timestamp=int(time.time() * 1000),
    )


def _resolve_api_key(
    model: Model,
    options: StreamOptions | None,
) -> str:
    api_key = options.api_key if options is not None else None
    environment_variable = (
        f"{model.provider.upper().replace('-', '_')}_API_KEY"
    )
    api_key = api_key or os.getenv(environment_variable)

    if not api_key:
        raise ValueError(f"{environment_variable} is not set")

    return api_key


def _apply_usage(
    message: AssistantMessage,
    usage: CompletionUsage,
    model: Model,
) -> None:
    cache_read = (
        getattr(usage, "cached_tokens", 0)
        or getattr(usage, "prompt_cache_hit_tokens", 0)
        or 0
    )
    input_tokens = usage.prompt_tokens - cache_read
    output_tokens = usage.completion_tokens
    cost = model.current_cost()

    input_cost = input_tokens * cost.input / 1_000_000
    output_cost = output_tokens * cost.output / 1_000_000
    cache_read_cost = cache_read * cost.cache_read / 1_000_000

    details = usage.completion_tokens_details
    reasoning_tokens = details.reasoning_tokens if details else None

    message.usage = Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=0,
        total_tokens=usage.total_tokens,
        reasoning=reasoning_tokens,
        cost=UsageCost(
            input=input_cost,
            output=output_cost,
            cache_read=cache_read_cost,
            cache_write=0,
            total=input_cost + output_cost + cache_read_cost,
        ),
    )


def _map_stop_reason(reason: str | None) -> DoneReason:
    if reason == "length":
        return "length"
    if reason == "tool_calls":
        return "toolUse"
    if reason == "stop":
        return "stop"
    raise RuntimeError(f"Unexpected stop reason: {reason}")


def stream_openai_completions(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantResponseStream:
    """启动后台 OpenAI-compatible 调用，并立即返回可异步消费的响应事件流。"""
    output = AssistantResponseStream()
    task = asyncio.create_task(
        _run_stream(output, model, context, options)
    )
    output.set_producer_task(task)
    return output


async def _run_stream(
    output: AssistantResponseStream,
    model: Model,
    context: Context,
    options: StreamOptions | None,
) -> None:
    partial = _create_partial_message(model)
    output.push(ResponseStartEvent(type="start", partial=partial))

    thinking: ThinkingContent | None = None
    text: TextContent | None = None
    thinking_open = False
    text_open = False
    tool_calls: dict[int, ToolCall] = {}
    tool_arguments: dict[int, str] = {}
    tool_content_indexes: dict[int, int] = {}
    finish_reason: str | None = None

    try:
        async with AsyncOpenAI(
            api_key=_resolve_api_key(model, options),
            base_url=model.base_url,
        ) as client:
            response = await client.chat.completions.create(
                model=model.id,
                messages=_convert_messages(model, context),
                stream=True,
                stream_options={"include_usage": True},
                temperature=options.temperature if options else None,
                max_tokens=options.max_tokens if options else None,
                tools=_convert_tools(context.tools) or omit,
            )

            async for chunk in response:
                partial.response_id = chunk.id
                partial.response_model = chunk.model

                if chunk.usage is not None:
                    _apply_usage(partial, chunk.usage, model)

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                reasoning = _reasoning_delta(choice.delta)

                if reasoning is not None:
                    reasoning_field, reasoning_delta = reasoning
                    if thinking is None:
                        thinking = ThinkingContent(
                            type="thinking",
                            thinking="",
                            thinking_signature=reasoning_field,
                        )
                        partial.content.append(thinking)
                        thinking_open = True
                        output.push(
                            ThinkingStartEvent(
                                type="thinking_start",
                                content_index=len(partial.content) - 1,
                                partial=partial,
                            )
                        )

                    thinking.thinking += reasoning_delta
                    output.push(
                        ThinkingDeltaEvent(
                            type="thinking_delta",
                            content_index=partial.content.index(thinking),
                            delta=reasoning_delta,
                            partial=partial,
                        )
                    )

                text_delta = choice.delta.content

                if text_delta:
                    if thinking is not None and thinking_open:
                        output.push(
                            ThinkingEndEvent(
                                type="thinking_end",
                                content_index=partial.content.index(thinking),
                                content=thinking.thinking,
                                partial=partial,
                            )
                        )
                        thinking_open = False

                    if text is None:
                        text = TextContent(type="text", text="")
                        partial.content.append(text)
                        text_open = True
                        output.push(
                            TextStartEvent(
                                type="text_start",
                                content_index=len(partial.content) - 1,
                                partial=partial,
                            )
                        )

                    text.text += text_delta
                    output.push(
                        TextDeltaEvent(
                            type="text_delta",
                            content_index=partial.content.index(text),
                            delta=text_delta,
                            partial=partial,
                        )
                    )

                if choice.delta.tool_calls:
                    if thinking is not None and thinking_open:
                        output.push(
                            ThinkingEndEvent(
                                type="thinking_end",
                                content_index=partial.content.index(thinking),
                                content=thinking.thinking,
                                partial=partial,
                            )
                        )
                        thinking_open = False

                    if text is not None and text_open:
                        output.push(
                            TextEndEvent(
                                type="text_end",
                                content_index=partial.content.index(text),
                                content=text.text,
                                partial=partial,
                            )
                        )
                        text_open = False

                    for tool_delta in choice.delta.tool_calls:
                        tool_call = tool_calls.get(tool_delta.index)

                        if tool_call is None:
                            tool_call = ToolCall(
                                type="toolCall",
                                id=tool_delta.id or "",
                                name=(
                                    tool_delta.function.name
                                    if tool_delta.function
                                    and tool_delta.function.name
                                    else ""
                                ),
                                arguments={},
                            )
                            tool_calls[tool_delta.index] = tool_call
                            tool_arguments[tool_delta.index] = ""
                            partial.content.append(tool_call)
                            content_index = len(partial.content) - 1
                            tool_content_indexes[tool_delta.index] = (
                                content_index
                            )
                            output.push(
                                ToolCallStartEvent(
                                    type="toolcall_start",
                                    content_index=content_index,
                                    partial=partial,
                                )
                            )

                        if tool_delta.id:
                            tool_call.id = tool_delta.id

                        function = tool_delta.function
                        if function and function.name:
                            tool_call.name = function.name

                        arguments_delta = (
                            function.arguments
                            if function and function.arguments
                            else ""
                        )

                        if arguments_delta:
                            tool_arguments[tool_delta.index] += (
                                arguments_delta
                            )
                            output.push(
                                ToolCallDeltaEvent(
                                    type="toolcall_delta",
                                    content_index=tool_content_indexes[
                                        tool_delta.index
                                    ],
                                    delta=arguments_delta,
                                    partial=partial,
                                )
                            )

                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason

        if thinking is not None and thinking_open:
            output.push(
                ThinkingEndEvent(
                    type="thinking_end",
                    content_index=partial.content.index(thinking),
                    content=thinking.thinking,
                    partial=partial,
                )
            )

        if text is not None and text_open:
            output.push(
                TextEndEvent(
                    type="text_end",
                    content_index=partial.content.index(text),
                    content=text.text,
                    partial=partial,
                )
            )

        for tool_index in sorted(
            tool_calls,
            key=tool_content_indexes.__getitem__,
        ):
            tool_call = tool_calls[tool_index]
            tool_call.arguments = _parse_tool_arguments(
                tool_arguments[tool_index]
            )
            output.push(
                ToolCallEndEvent(
                    type="toolcall_end",
                    content_index=tool_content_indexes[tool_index],
                    tool_call=tool_call,
                    partial=partial,
                )
            )

        done_reason = _map_stop_reason(finish_reason)
        partial.stop_reason = done_reason
        output.push(
            ResponseDoneEvent(
                type="done",
                reason=done_reason,
                message=partial,
            )
        )
    except Exception as error:
        partial.stop_reason = "error"
        partial.error_message = str(error)
        output.push(
            ResponseErrorEvent(
                type="error",
                reason="error",
                error=partial,
            )
        )

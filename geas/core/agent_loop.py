import asyncio
import time

from jsonschema import ValidationError, validate

from geas.ai.model_registry import StreamFunction
from geas.ai.types import (
    Context,
    ResponseDoneEvent,
    ResponseErrorEvent,
    Message,
    ResponseStartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

from .event_stream import AgentRunStream
from .types import (
    AgentContext,
    AgentRunEndEvent,
    AgentLoopConfig,
    AgentRunStartEvent,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)


def agent_loop(
    prompts: list[Message],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_function: StreamFunction,
    max_turns: int,
) -> AgentRunStream:
    """启动一次后台 Agent 运行，并立即返回可异步消费的运行事件流。"""
    output = AgentRunStream()
    task = asyncio.create_task(
        _run_agent_loop_safely(
            output,
            prompts,
            context,
            config,
            stream_function,
            max_turns,
        )
    )
    output.set_producer_task(task)
    return output


async def _run_agent_loop_safely(
    output: AgentRunStream,
    prompts: list[Message],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_function: StreamFunction,
    max_turns: int,
) -> None:
    try:
        await _run_agent_loop(
            output,
            prompts,
            context,
            config,
            stream_function,
            max_turns,
        )
    except Exception as error:
        output.fail(error)


async def _execute_tool_call(
    tool_call: ToolCall,
    tools: list[AgentTool],
) -> tuple[AgentToolResult, bool]:
    tool = next(
        (
            tool
            for tool in tools
            if tool.name == tool_call.name
        ),
        None,
    )

    if tool is None:
        result = AgentToolResult(
            content=[
                TextContent(
                    type="text",
                    text=f'Tool "{tool_call.name}" not found',
                )
            ]
        )
        is_error = True
    else:
        try:
            validate(
                instance=tool_call.arguments,
                schema=tool.parameters,
            )
            result = await tool.execute(
                tool_call.id,
                tool_call.arguments,
            )
            is_error = False
        except ValidationError as error:
            result = AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            f'Invalid arguments for tool '
                            f'"{tool_call.name}": {error.message}'
                        ),
                    )
                ]
            )
            is_error = True
        except Exception as error:
            result = AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=str(error),
                    )
                ]
            )
            is_error = True

    return result, is_error


def _create_truncated_tool_result(
    tool_call: ToolCall,
) -> AgentToolResult:
    return AgentToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f'Tool call "{tool_call.name}" was not executed because '
                    "the model response hit the output token limit, so its "
                    "arguments may be truncated. Call the tool again with "
                    "complete arguments."
                ),
            )
        ],
    )


def _append_skipped_tool_results(
    output: AgentRunStream,
    tool_calls: list[ToolCall],
    current_messages: list[Message],
    new_messages: list[Message],
    tool_results: list[ToolResultMessage],
) -> None:
    for tool_call in tool_calls:
        tool_result = ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=[
                TextContent(
                    type="text",
                    text=(
                        f'Tool call "{tool_call.name}" was not executed '
                        "because the agent run was stopped."
                    ),
                )
            ],
            is_error=True,
            timestamp=int(time.time() * 1000),
        )
        current_messages.append(tool_result)
        new_messages.append(tool_result)
        tool_results.append(tool_result)
        output.push(
            MessageStartEvent(
                type="message_start",
                message=tool_result,
            )
        )
        output.push(
            MessageEndEvent(
                type="message_end",
                message=tool_result,
            )
        )


async def _run_agent_loop(
    output: AgentRunStream,
    prompts: list[Message],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_function: StreamFunction,
    max_turns: int,
) -> None:
    output.push(AgentRunStartEvent(type="agent_start"))
    output.push(TurnStartEvent(type="turn_start"))

    for prompt in prompts:
        output.push(
            MessageStartEvent(type="message_start", message=prompt)
        )
        output.push(MessageEndEvent(type="message_end", message=prompt))

    current_messages = [*context.messages, *prompts]
    new_messages = [*prompts]
    first_turn = True
    turn_count = 0

    while True:
        if turn_count >= max_turns:
            raise RuntimeError(
                f"Agent exceeded max turns: {max_turns}"
            )
        turn_count += 1

        if first_turn:
            first_turn = False
        else:
            output.push(TurnStartEvent(type="turn_start"))

        context = AgentContext(
            messages=[*current_messages],
            system_prompt=context.system_prompt,
            tools=[*context.tools],
        )
        for handler in config.hooks.before_turn:
            context = await handler(context)
        current_messages = [*context.messages]

        ai_context = Context(
            messages=[*current_messages],
            system_prompt=context.system_prompt,
            tools=list(context.tools) or None,
        )
        response = stream_function(config.model, ai_context, None)

        async for event in response:
            if isinstance(event, ResponseStartEvent):
                output.push(
                    MessageStartEvent(
                        type="message_start",
                        message=event.partial,
                    )
                )
            elif isinstance(event, ResponseDoneEvent):
                output.push(
                    MessageEndEvent(
                        type="message_end",
                        message=event.message,
                    )
                )
            elif isinstance(event, ResponseErrorEvent):
                output.push(
                    MessageEndEvent(
                        type="message_end",
                        message=event.error,
                    )
                )
            else:
                output.push(
                    MessageUpdateEvent(
                        type="message_update",
                        message=event.partial,
                        assistant_response_event=event,
                    )
                )

        assistant_message = await response.result()
        current_messages.append(assistant_message)
        new_messages.append(assistant_message)

        if assistant_message.stop_reason in ("error", "aborted"):
            tool_calls: list[ToolCall] = []
        else:
            tool_calls = [
                block
                for block in assistant_message.content
                if isinstance(block, ToolCall)
            ]
        tool_results: list[ToolResultMessage] = []
        stop_run = False

        for index, tool_call in enumerate(tool_calls):
            for handler in config.hooks.before_tool_call:
                if await handler(tool_call):
                    stop_run = True
                    break

            if stop_run:
                _append_skipped_tool_results(
                    output,
                    tool_calls[index:],
                    current_messages,
                    new_messages,
                    tool_results,
                )
                break

            output.push(
                ToolExecutionStartEvent(
                    type="tool_execution_start",
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                )
            )

            if assistant_message.stop_reason == "length":
                result = _create_truncated_tool_result(tool_call)
                is_error = True
            else:
                result, is_error = await _execute_tool_call(
                    tool_call,
                    context.tools,
                )

            tool_end = ToolExecutionEndEvent(
                type="tool_execution_end",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result,
                is_error=is_error,
            )
            output.push(tool_end)
            tool_result = ToolResultMessage(
                role="toolResult",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=result.content,
                is_error=is_error,
                timestamp=int(time.time() * 1000),
                details=result.details,
            )
            current_messages.append(tool_result)
            new_messages.append(tool_result)
            tool_results.append(tool_result)
            output.push(
                MessageStartEvent(
                    type="message_start",
                    message=tool_result,
                )
            )
            output.push(
                MessageEndEvent(
                    type="message_end",
                    message=tool_result,
                )
            )

            for handler in config.hooks.after_tool_call:
                if await handler(tool_end):
                    stop_run = True
                    break

            if stop_run:
                _append_skipped_tool_results(
                    output,
                    tool_calls[index + 1 :],
                    current_messages,
                    new_messages,
                    tool_results,
                )
                break

        turn_end = TurnEndEvent(
            type="turn_end",
            message=assistant_message,
            tool_results=tool_results,
        )
        output.push(turn_end)

        for handler in config.hooks.after_turn:
            if await handler(turn_end):
                stop_run = True
                break

        if stop_run or not tool_calls:
            break

    output.push(
        AgentRunEndEvent(
            type="agent_end",
            messages=new_messages,
        )
    )

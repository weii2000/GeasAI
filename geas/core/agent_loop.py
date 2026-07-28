import asyncio
import time

from jsonschema import ValidationError, validate

from geas.ai.models import StreamFunction
from geas.ai.types import (
    Context,
    DoneEvent,
    ErrorEvent,
    Message,
    StartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
)

from .event_stream import AgentEventStream
from .types import (
    AgentContext,
    AgentEndEvent,
    AgentLoopConfig,
    AgentStartEvent,
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
) -> AgentEventStream:
    output = AgentEventStream()
    task = asyncio.create_task(
        _run_agent_loop_safely(
            output,
            prompts,
            context,
            config,
            stream_function,
        )
    )
    output.set_producer_task(task)
    return output


async def _run_agent_loop_safely(
    output: AgentEventStream,
    prompts: list[Message],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_function: StreamFunction,
) -> None:
    try:
        await _run_agent_loop(
            output,
            prompts,
            context,
            config,
            stream_function,
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


async def _run_agent_loop(
    output: AgentEventStream,
    prompts: list[Message],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_function: StreamFunction,
) -> None:
    output.push(AgentStartEvent(type="agent_start"))
    output.push(TurnStartEvent(type="turn_start"))

    for prompt in prompts:
        output.push(
            MessageStartEvent(type="message_start", message=prompt)
        )
        output.push(MessageEndEvent(type="message_end", message=prompt))

    current_messages = [*context.messages, *prompts]
    new_messages = [*prompts]
    first_turn = True

    while True:
        if first_turn:
            first_turn = False
        else:
            output.push(TurnStartEvent(type="turn_start"))

        ai_context = Context(
            messages=current_messages,
            system_prompt=context.system_prompt,
            tools=list(context.tools) or None,
        )
        response = stream_function(config.model, ai_context)

        async for event in response:
            if isinstance(event, StartEvent):
                output.push(
                    MessageStartEvent(
                        type="message_start",
                        message=event.partial,
                    )
                )
            elif isinstance(event, DoneEvent):
                output.push(
                    MessageEndEvent(
                        type="message_end",
                        message=event.message,
                    )
                )
            elif isinstance(event, ErrorEvent):
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
                        assistant_message_event=event,
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

        for tool_call in tool_calls:
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

            output.push(
                ToolExecutionEndEvent(
                    type="tool_execution_end",
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    result=result,
                    is_error=is_error,
                )
            )
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

        output.push(
            TurnEndEvent(
                type="turn_end",
                message=assistant_message,
                tool_results=tool_results,
            )
        )

        if config.prepare_next_turn is not None:
            next_context = await config.prepare_next_turn(
                AgentContext(
                    messages=current_messages,
                    system_prompt=context.system_prompt,
                    tools=[*context.tools],
                )
            )
            if next_context is not None:
                context = next_context
                current_messages = [*next_context.messages]

        if not tool_calls:
            break

    output.push(
        AgentEndEvent(
            type="agent_end",
            messages=new_messages,
        )
    )

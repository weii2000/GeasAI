import time
from collections.abc import Callable

from geas.ai.model_registry import StreamFunction
from geas.ai.types import UserMessage

from .agent_loop import agent_loop
from .types import (
    AgentContext,
    AgentRunEvent,
    AgentLoopConfig,
    AgentState,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    PrepareNextTurn,
    ShouldStopAfterTurn,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)

type AgentRunListener = Callable[[AgentRunEvent], None]


class Agent:
    def __init__(
        self,
        state: AgentState,
        stream_function: StreamFunction,
        max_turns: int,
        prepare_next_turn: PrepareNextTurn | None = None,
        should_stop_after_turn: ShouldStopAfterTurn | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")

        self.state = state
        self._stream_function = stream_function
        self.max_turns = max_turns
        self.prepare_next_turn = prepare_next_turn
        self.should_stop_after_turn = should_stop_after_turn
        self._listeners: set[AgentRunListener] = set()

    def subscribe(
        self,
        listener: AgentRunListener,
    ) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def prompt(self, text: str) -> None:
        """处理一次用户提示，运行 Agent Loop 并持续更新状态。"""
        if self.state.is_streaming:
            raise RuntimeError("Agent is already processing a prompt")

        prompt = UserMessage(
            role="user",
            content=text,
            timestamp=int(time.time() * 1000),
        )
        context = AgentContext(
            messages=[*self.state.messages],
            system_prompt=self.state.system_prompt,
            tools=[*self.state.tools],
        )

        self.state.is_streaming = True
        self.state.error_message = None

        try:
            stream = agent_loop(
                prompts=[prompt],
                context=context,
                config=AgentLoopConfig(
                    model=self.state.model,
                    prepare_next_turn=self.prepare_next_turn,
                    should_stop_after_turn=self.should_stop_after_turn,
                ),
                stream_function=self._stream_function,
                max_turns=self.max_turns,
            )

            async for event in stream:
                self._process_event(event)

                for listener in tuple(self._listeners):
                    listener(event)

            if self.state.error_message is not None:
                raise RuntimeError(self.state.error_message)
        finally:
            self.state.is_streaming = False
            self.state.streaming_message = None
            self.state.pending_tool_calls.clear()

    def _process_event(self, event: AgentRunEvent) -> None:
        if isinstance(event, MessageStartEvent | MessageUpdateEvent):
            self.state.streaming_message = event.message
        elif isinstance(event, MessageEndEvent):
            self.state.streaming_message = None
            self.state.messages.append(event.message)
        elif isinstance(event, ToolExecutionStartEvent):
            self.state.pending_tool_calls.add(event.tool_call_id)
        elif isinstance(event, ToolExecutionEndEvent):
            self.state.pending_tool_calls.discard(event.tool_call_id)
        elif isinstance(event, TurnEndEvent):
            self.state.error_message = event.message.error_message

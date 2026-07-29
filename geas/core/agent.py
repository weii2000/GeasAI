import time
from collections.abc import Callable

from geas.ai.models import StreamFunction
from geas.ai.types import UserMessage

from .agent_loop import agent_loop
from .types import (
    AgentContext,
    AgentEvent,
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

type AgentListener = Callable[[AgentEvent], None]


class Agent:
    def __init__(
        self,
        state: AgentState,
        stream_function: StreamFunction,
        prepare_next_turn: PrepareNextTurn | None = None,
        should_stop_after_turn: ShouldStopAfterTurn | None = None,
    ) -> None:
        self.state = state
        self._stream_function = stream_function
        self.prepare_next_turn = prepare_next_turn
        self.should_stop_after_turn = should_stop_after_turn
        self._listeners: set[AgentListener] = set()

    def subscribe(
        self,
        listener: AgentListener,
    ) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    async def prompt(self, text: str) -> None:
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
            )

            async for event in stream:
                self._process_event(event)

                for listener in tuple(self._listeners):
                    listener(event)
        finally:
            self.state.is_streaming = False
            self.state.streaming_message = None
            self.state.pending_tool_calls.clear()

    def _process_event(self, event: AgentEvent) -> None:
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

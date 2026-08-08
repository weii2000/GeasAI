import asyncio
from collections.abc import AsyncIterator, Callable
from typing import cast

from .types import (
    AssistantMessage,
    AssistantResponseEvent,
    ResponseDoneEvent,
    ResponseErrorEvent,
)

_END = object()


class StreamCancelledError(Exception):
    pass


class EventStream[Event, Result]:
    def __init__(
        self,
        is_done: Callable[[Event], bool],
        get_result: Callable[[Event], Result],
    ) -> None:
        self._is_done = is_done
        self._get_result = get_result
        self._queue: asyncio.Queue[Event | object] = asyncio.Queue()
        self._result: Result | None = None
        self._result_ready = asyncio.Event()
        self._error: Exception | None = None
        self._producer_task: asyncio.Task[None] | None = None
        self._done = False

    def set_producer_task(
        self,
        task: asyncio.Task[None],
    ) -> None:
        self._producer_task = task

    def push(self, event: Event) -> None:
        if self._done:
            return

        self._queue.put_nowait(event)

        if self._is_done(event):
            self._finish(self._get_result(event))

    def _finish(self, result: Result) -> None:
        self._done = True
        self._result = result
        self._result_ready.set()
        self._queue.put_nowait(_END)

    def fail(self, error: Exception) -> None:
        if self._done:
            return

        self._done = True
        self._error = error
        self._result_ready.set()
        self._queue.put_nowait(_END)

    def cancel(self) -> None:
        if self._done:
            return

        if self._producer_task is not None:
            self._producer_task.cancel()

        self.fail(StreamCancelledError("Stream was cancelled"))

    async def __aiter__(self) -> AsyncIterator[Event]:
        try:
            while True:
                event = await self._queue.get()

                if event is _END:
                    if self._error is not None:
                        raise self._error
                    return

                yield cast(Event, event)
        finally:
            if not self._done:
                self.cancel()

    async def result(self) -> Result:
        await self._result_ready.wait()

        if self._error is not None:
            raise self._error

        assert self._result is not None
        return self._result


def _is_response_done(event: AssistantResponseEvent) -> bool:
    return isinstance(event, ResponseDoneEvent | ResponseErrorEvent)


def _get_response_result(
    event: AssistantResponseEvent,
) -> AssistantMessage:
    if isinstance(event, ResponseDoneEvent):
        return event.message
    if isinstance(event, ResponseErrorEvent):
        return event.error
    raise ValueError("Assistant response stream has not finished")


class AssistantResponseStream(
    EventStream[AssistantResponseEvent, AssistantMessage]
):
    def __init__(self) -> None:
        super().__init__(_is_response_done, _get_response_result)

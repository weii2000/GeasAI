from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from .protocol import ToolCallEnvelope, ToolResultEnvelope


@dataclass
class TaskChannel:
    available: asyncio.Condition = field(default_factory=asyncio.Condition)
    queued: deque[ToolCallEnvelope] = field(default_factory=deque)
    pending: dict[str, asyncio.Future[ToolResultEnvelope]] = field(
        default_factory=dict
    )
    inflight: ToolCallEnvelope | None = None
    completed: set[str] = field(default_factory=set)
    closed: bool = False


class ToolBroker:
    def __init__(self, result_timeout: float = 180.0) -> None:
        self._channels: dict[str, TaskChannel] = {}
        self._result_timeout = result_timeout

    def create_task(self, task_id: str) -> None:
        if task_id in self._channels:
            raise ValueError(f"task already exists: {task_id}")
        self._channels[task_id] = TaskChannel()

    def remove_task(self, task_id: str) -> None:
        channel = self._channels.pop(task_id, None)
        if channel is None:
            return
        for future in channel.pending.values():
            if not future.done():
                future.cancel()

    async def close_task(self, task_id: str) -> None:
        channel = self._channels.get(task_id)
        if channel is None:
            return
        channel.closed = True
        channel.queued.clear()
        async with channel.available:
            channel.available.notify_all()

    async def dispatch(
        self,
        task_id: str,
        call_id: str,
        name: str,
        arguments: dict[str, object],
    ) -> ToolResultEnvelope:
        channel = self._require_channel(task_id)
        if channel.closed:
            raise ValueError(f"task is closed: {task_id}")
        if call_id in channel.pending or call_id in channel.completed:
            raise ValueError(f"duplicate tool call: {call_id}")

        future = asyncio.get_running_loop().create_future()
        channel.pending[call_id] = future
        async with channel.available:
            channel.queued.append(
                ToolCallEnvelope(
                    task_id=task_id,
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                )
            )
            channel.available.notify_all()

        try:
            async with asyncio.timeout(self._result_timeout):
                return await future
        except TimeoutError as error:
            raise TimeoutError(
                f'phone did not finish tool "{name}" within '
                f"{self._result_timeout:g} seconds"
            ) from error
        finally:
            channel.pending.pop(call_id, None)
            channel.queued = deque(
                call for call in channel.queued if call.call_id != call_id
            )
            if channel.inflight and channel.inflight.call_id == call_id:
                channel.inflight = None

    async def next_call(
        self,
        task_id: str,
        wait_seconds: float = 5.0,
    ) -> ToolCallEnvelope | None:
        channel = self._require_channel(task_id)
        if channel.inflight:
            return channel.inflight
        if channel.queued:
            channel.inflight = channel.queued.popleft()
            return channel.inflight
        if channel.closed:
            return None

        async with channel.available:
            try:
                async with asyncio.timeout(wait_seconds):
                    await channel.available.wait_for(
                        lambda: (
                            channel.inflight is not None
                            or bool(channel.queued)
                            or channel.closed
                        )
                    )
            except TimeoutError:
                return None
            if channel.inflight:
                return channel.inflight
            if channel.closed:
                return None
            channel.inflight = channel.queued.popleft()
            return channel.inflight

    def submit_result(
        self,
        task_id: str,
        result: ToolResultEnvelope,
    ) -> None:
        channel = self._require_channel(task_id)
        if result.call_id in channel.completed:
            return
        future = channel.pending.get(result.call_id)
        if future is None:
            raise ValueError(f"unknown or expired tool call: {result.call_id}")
        if future.done():
            raise ValueError(f"expired tool call: {result.call_id}")
        future.set_result(result)
        channel.completed.add(result.call_id)
        if channel.inflight and channel.inflight.call_id == result.call_id:
            channel.inflight = None

    def has_queued_call(self, task_id: str) -> bool:
        channel = self._require_channel(task_id)
        return bool(channel.queued or channel.inflight)

    def _require_channel(self, task_id: str) -> TaskChannel:
        try:
            return self._channels[task_id]
        except KeyError as error:
            raise KeyError(f"unknown task: {task_id}") from error

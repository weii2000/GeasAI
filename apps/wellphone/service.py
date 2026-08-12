from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import asdict, dataclass

from geas.ai.model_registry import StreamFunction
from geas.ai.types import Model

from .agent import create_phone_agent, final_text
from .broker import ToolBroker
from .protocol import TaskStatus


@dataclass
class TaskRecord:
    id: str
    prompt: str
    status: TaskStatus = "running"
    answer: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class WellphoneService:
    def __init__(
        self,
        model: Model,
        stream_function: StreamFunction,
        tool_timeout: float = 180.0,
    ) -> None:
        self.model = model
        self.stream_function = stream_function
        self.broker = ToolBroker(result_timeout=tool_timeout)
        self.tasks: dict[str, TaskRecord] = {}
        self._runs: dict[str, asyncio.Task[None]] = {}

    def create_task(
        self,
        prompt: str,
        task_id: str | None = None,
    ) -> TaskRecord:
        task_id = task_id or uuid.uuid4().hex
        existing = self.tasks.get(task_id)
        if existing is not None:
            if existing.prompt != prompt:
                raise ValueError("task id already belongs to another prompt")
            return existing

        record = TaskRecord(id=task_id, prompt=prompt)
        self.tasks[task_id] = record
        self.broker.create_task(task_id)
        run = asyncio.create_task(self._run(record))
        self._runs[task_id] = run
        run.add_done_callback(lambda _: self._runs.pop(task_id, None))
        return record

    def cancel_task(self, task_id: str) -> None:
        record = self.require_task(task_id)
        run = self._runs.get(task_id)
        if run is None or run.done():
            if record.status == "completed":
                raise ValueError("completed task cannot be cancelled")
            return
        run.cancel()
        self.broker.remove_task(task_id)
        record.error = "task cancelled"
        record.status = "failed"

    def require_task(self, task_id: str) -> TaskRecord:
        try:
            return self.tasks[task_id]
        except KeyError as error:
            raise KeyError(f"unknown task: {task_id}") from error

    async def close(self) -> None:
        runs = list(self._runs.values())
        for run in runs:
            run.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)
        for task_id in list(self.tasks):
            self.broker.remove_task(task_id)

    async def _run(self, record: TaskRecord) -> None:
        def on_waiting(waiting: bool) -> None:
            if record.status not in ("completed", "failed"):
                record.status = "waiting_for_phone" if waiting else "running"

        try:
            agent = create_phone_agent(
                record.id,
                self.broker,
                on_waiting,
                self.model,
                self.stream_function,
            )
            await agent.prompt(record.prompt)
            record.answer = final_text(agent)
            record.status = "completed"
        except asyncio.CancelledError:
            record.error = "task cancelled"
            record.status = "failed"
            raise
        except Exception as error:
            traceback.print_exc()
            record.error = str(error)
            record.status = "failed"
        finally:
            await self.broker.close_task(record.id)

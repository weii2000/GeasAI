from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from geas.ai.model_registry import StreamFunction
from geas.ai.types import Model

from .broker import ToolBroker
from .observability import log_event
from .protocol import TaskStatus
from .session import SessionStore, WellphoneSession, new_session_id


@dataclass
class TaskRecord:
    id: str
    session_id: str
    device_id: str
    prompt: str
    device_context: str | None = None
    status: TaskStatus = "running"
    answer: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("device_id")
        data.pop("device_context")
        return data


class WellphoneService:
    def __init__(
        self,
        model: Model,
        stream_function: StreamFunction,
        tool_timeout: float = 180.0,
        sessions_root: Path | None = None,
    ) -> None:
        self.model = model
        self.stream_function = stream_function
        self.broker = ToolBroker(result_timeout=tool_timeout)
        self.store = SessionStore(sessions_root)
        self.sessions: dict[str, WellphoneSession] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self._runs: dict[str, asyncio.Task[None]] = {}
        for snapshot in self.store.load_all():
            self.sessions[snapshot.id] = self._build_session(
                snapshot.id,
                snapshot.device_id,
                messages=snapshot.messages,
                created_at=snapshot.created_at,
                updated_at=snapshot.updated_at,
            )

    def create_task(
        self,
        prompt: str,
        device_id: str,
        task_id: str | None = None,
        session_id: str | None = None,
        device_context: str | None = None,
    ) -> TaskRecord:
        task_id = task_id or uuid.uuid4().hex
        existing = self.tasks.get(task_id)
        if existing is not None:
            if existing.device_id != device_id:
                raise KeyError(f"unknown task: {task_id}")
            if (
                existing.prompt != prompt
                or session_id is not None
                and existing.session_id != session_id
            ):
                raise ValueError("task id already belongs to another prompt")
            log_event(
                "task.reused",
                task_id=existing.id,
                session_id=existing.session_id,
                status=existing.status,
            )
            return existing

        if session_id is None:
            session = self._build_session(new_session_id(), device_id)
            self.sessions[session.id] = session
            self.store.save(session)
        else:
            session = self.require_session(session_id, device_id)
        session.reserve(task_id)

        record = TaskRecord(
            id=task_id,
            session_id=session.id,
            device_id=device_id,
            prompt=prompt,
            device_context=device_context,
        )
        self.tasks[task_id] = record
        self.broker.create_task(task_id)
        log_event(
            "task.created",
            task_id=record.id,
            session_id=record.session_id,
        )
        run = asyncio.create_task(self._run(record, perf_counter()))
        self._runs[task_id] = run
        run.add_done_callback(
            lambda _: self._finish_run(task_id, session)
        )
        return record

    def cancel_task(self, task_id: str, device_id: str) -> None:
        record = self.require_task(task_id, device_id)
        run = self._runs.get(task_id)
        if run is None or run.done():
            if record.status == "cancelled":
                return
            raise ValueError(f"{record.status} task cannot be cancelled")
        record.status = "cancelled"
        record.error = None
        log_event(
            "task.cancel_requested",
            task_id=record.id,
            session_id=record.session_id,
        )
        run.cancel()
        self.broker.remove_task(task_id)

    def require_task(self, task_id: str, device_id: str) -> TaskRecord:
        try:
            record = self.tasks[task_id]
        except KeyError as error:
            raise KeyError(f"unknown task: {task_id}") from error
        if record.device_id != device_id:
            raise KeyError(f"unknown task: {task_id}")
        return record

    def require_session(
        self,
        session_id: str,
        device_id: str,
    ) -> WellphoneSession:
        try:
            session = self.sessions[session_id]
        except KeyError as error:
            raise KeyError(f"unknown session: {session_id}") from error
        if session.device_id != device_id:
            raise KeyError(f"unknown session: {session_id}")
        return session

    async def close(self) -> None:
        runs = list(self._runs.values())
        for run in runs:
            run.cancel()
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)
        for task_id in list(self.tasks):
            self.broker.remove_task(task_id)

    async def _run(self, record: TaskRecord, started: float) -> None:
        session = self.sessions[record.session_id]
        error_type: str | None = None
        try:
            record.answer = await session.prompt(
                record.id,
                record.prompt,
                record.device_context,
            )
            record.status = "completed"
        except asyncio.CancelledError:
            record.error = None
            record.status = "cancelled"
            raise
        except Exception as error:
            error_type = type(error).__name__
            record.error = str(error)
            record.status = "failed"
        finally:
            try:
                self.store.save(session)
            finally:
                # ponytail: retain the closed channel for idempotent HTTP retries;
                # evict it with its TaskRecord if terminal-task TTL cleanup is added.
                await self.broker.close_task(record.id)
                log_event(
                    "task.finished",
                    task_id=record.id,
                    session_id=record.session_id,
                    status=record.status,
                    duration_ms=round((perf_counter() - started) * 1000),
                    error_type=error_type,
                )

    def _build_session(
        self,
        session_id: str,
        device_id: str,
        **saved: object,
    ) -> WellphoneSession:
        return WellphoneSession(
            session_id,
            device_id,
            self.model,
            self.stream_function,
            self.broker,
            self._set_task_status,
            **saved,
        )

    def _set_task_status(self, task_id: str, status: TaskStatus) -> None:
        record = self.tasks.get(task_id)
        if record is not None and record.status not in (
            "completed",
            "failed",
            "cancelled",
        ):
            record.status = status

    def _finish_run(
        self,
        task_id: str,
        session: WellphoneSession,
    ) -> None:
        self._runs.pop(task_id, None)
        session.release(task_id)

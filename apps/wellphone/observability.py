from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import TextIO

from geas.ai.types import AssistantMessage
from geas.core.types import (
    AgentRunEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
)


_LOGGER = logging.getLogger("wellphone")
type LogValue = str | int | float | bool | None


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self) -> TextIO:
        stream = super()._open()
        Path(self.baseFilename).chmod(0o600)
        return stream


def configure_logging(path: Path | None = None) -> None:
    if not _LOGGER.handlers:
        formatter = logging.Formatter("%(message)s")
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        _LOGGER.addHandler(console)

        log_path = path or (
            Path.home() / ".geas" / "wellphone" / "logs" / "wellphone.jsonl"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.chmod(0o700)
        file_handler = _PrivateRotatingFileHandler(
            log_path,
            maxBytes=5_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _LOGGER.addHandler(file_handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def log_event(event: str, **fields: LogValue) -> None:
    _LOGGER.info(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "event": event,
                **fields,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


class RunTracer:
    """Translate Agent events into content-free, task-scoped trace events."""

    def __init__(self, task_id: str, session_id: str) -> None:
        self.task_id = task_id
        self.session_id = session_id
        self.turn = 0
        self._turn_started: float | None = None
        self._tools: dict[str, tuple[str, float]] = {}

    def __call__(self, event: AgentRunEvent) -> None:
        if isinstance(event, TurnStartEvent):
            self.turn += 1
            self._turn_started = perf_counter()
        elif (
            isinstance(event, MessageEndEvent)
            and isinstance(event.message, AssistantMessage)
        ):
            usage = event.message.usage
            started = self._turn_started or perf_counter()
            log_event(
                "model.finished",
                task_id=self.task_id,
                session_id=self.session_id,
                turn=self.turn,
                provider=event.message.provider,
                model=event.message.model,
                stop_reason=event.message.stop_reason,
                duration_ms=round((perf_counter() - started) * 1000),
                input_tokens=usage.input,
                output_tokens=usage.output,
                cache_read_tokens=usage.cache_read,
                cache_write_tokens=usage.cache_write,
                reasoning_tokens=usage.reasoning or 0,
                total_tokens=usage.total_tokens,
                cost_rmb=usage.cost.total,
            )
            self._turn_started = None
        elif isinstance(event, ToolExecutionStartEvent):
            self._tools[event.tool_call_id] = (event.tool_name, perf_counter())
            log_event(
                "agent.tool.started",
                task_id=self.task_id,
                session_id=self.session_id,
                tool=event.tool_name,
                call_id=event.tool_call_id,
            )
        elif isinstance(event, ToolExecutionEndEvent):
            _, started = self._tools.pop(
                event.tool_call_id,
                (event.tool_name, perf_counter()),
            )
            log_event(
                "agent.tool.finished",
                task_id=self.task_id,
                session_id=self.session_id,
                tool=event.tool_name,
                call_id=event.tool_call_id,
                status="error" if event.is_error else "completed",
                duration_ms=round((perf_counter() - started) * 1000),
            )

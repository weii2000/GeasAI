import asyncio
import json
import logging
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.wellphone.agent import _youtube_result
from apps.wellphone.broker import ToolBroker
from apps.wellphone.protocol import ToolResultEnvelope
from apps.wellphone.server import create_app
from apps.wellphone.service import WellphoneService
from geas.ai.event_stream import AssistantResponseStream
from geas.ai.providers.deepseek import DEEPSEEK_MODELS
from geas.ai.types import ResponseErrorEvent, TextContent

from .helpers import ScriptedModel, make_assistant, make_tool_call


PHOTO_SEARCH = {
    "start": "2026-08-01T00:00:00+01:00",
    "end": "2026-08-02T00:00:00+01:00",
    "media_type": "image",
    "include_screenshots": False,
}


def make_service(
    root: Path,
    stream: ScriptedModel | None = None,
    *,
    tool_timeout: float = 1,
) -> WellphoneService:
    return WellphoneService(
        DEEPSEEK_MODELS[0],
        stream or ScriptedModel([]),
        tool_timeout=tool_timeout,
        sessions_root=root,
    )


def test_broker_redelivers_call_and_accepts_duplicate_result() -> None:
    async def run() -> None:
        broker = ToolBroker(result_timeout=1)
        broker.create_task("task-1")
        waiting = asyncio.create_task(
            broker.dispatch("task-1", "call-1", "search_photos", {"limit": 5})
        )

        first = await broker.next_call("task-1", wait_seconds=0.1)
        assert first is not None
        assert await broker.next_call("task-1", wait_seconds=0.1) == first

        result = ToolResultEnvelope(call_id="call-1", result={"count": 2})
        broker.submit_result("task-1", result)
        broker.submit_result("task-1", result)

        assert await waiting == result
        assert await broker.next_call("task-1", wait_seconds=0.01) is None

    asyncio.run(run())


def test_broker_times_out_and_rejects_late_result() -> None:
    async def run() -> None:
        broker = ToolBroker(result_timeout=0.01)
        broker.create_task("task-1")

        with pytest.raises(TimeoutError, match="phone did not finish tool"):
            await broker.dispatch("task-1", "call-1", "search_photos", {})

        with pytest.raises(ValueError, match="unknown or expired tool call"):
            broker.submit_result(
                "task-1",
                ToolResultEnvelope(call_id="call-1", result={"count": 1}),
            )

    asyncio.run(run())


def test_task_creation_is_idempotent_and_session_is_single_run(tmp_path: Path) -> None:
    async def run() -> None:
        service = make_service(tmp_path)
        device_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        first = service.create_task("organize photos", device_id, task_id)
        assert service.create_task("organize photos", device_id, task_id) is first

        with pytest.raises(ValueError, match="another prompt"):
            service.create_task("different prompt", device_id, task_id)
        with pytest.raises(KeyError, match="unknown task"):
            service.require_task(task_id, str(uuid.uuid4()))
        with pytest.raises(ValueError, match="session is already running"):
            service.create_task(
                "second run",
                device_id,
                str(uuid.uuid4()),
                first.session_id,
            )

        running = service._runs[task_id]
        service.cancel_task(task_id, device_id)
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0)
        assert first.status == "cancelled"
        assert first.error is None
        assert service.sessions[first.session_id].active_task_id is None
        await service.close()

    asyncio.run(run())


def test_tool_call_survives_poll_retry_and_run_closes_broker(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="wellphone")

    async def run() -> None:
        stream = ScriptedModel(
            [
                make_assistant(
                    [make_tool_call("search_photos", PHOTO_SEARCH)],
                    "toolUse",
                ),
                make_assistant(
                    [TextContent(type="text", text="整理完成")],
                    "stop",
                ),
            ]
        )
        service = make_service(tmp_path, stream)
        device_id = str(uuid.uuid4())
        record = service.create_task("organize photos", device_id, str(uuid.uuid4()))
        running = service._runs[record.id]

        async with asyncio.timeout(1):
            while record.status != "waiting_for_phone":
                await asyncio.sleep(0)

        first = await service.broker.next_call(record.id, wait_seconds=0.1)
        assert first is not None
        assert await service.broker.next_call(record.id, wait_seconds=0.1) == first
        result = ToolResultEnvelope(call_id=first.call_id, result={"count": 0})
        service.broker.submit_result(record.id, result)
        service.broker.submit_result(record.id, result)

        await running
        await asyncio.sleep(0)
        assert record.status == "completed"
        assert record.answer == "整理完成"
        with pytest.raises(ValueError, match="completed task cannot be cancelled"):
            service.cancel_task(record.id, device_id)
        assert await service.broker.next_call(record.id, wait_seconds=0.01) is None
        service.broker.submit_result(record.id, result)
        with pytest.raises(ValueError, match="task is closed"):
            await service.broker.dispatch(
                record.id,
                "new-call",
                "search_photos",
                PHOTO_SEARCH,
            )

        restored = make_service(tmp_path)
        messages = restored.require_session(record.session_id, device_id).messages
        assert [message.content for message in messages] == [
            "organize photos",
            "整理完成",
        ]
        await service.close()
        with pytest.raises(KeyError, match="unknown task"):
            await service.broker.next_call(record.id, wait_seconds=0.01)
        await restored.close()

    asyncio.run(run())

    events = [json.loads(record.message) for record in caplog.records]
    by_name = {event["event"]: event for event in events}
    assert by_name["task.created"]["session_id"]
    assert by_name["tool.dispatched"]["call_id"]
    assert by_name["tool.finished"]["status"] == "completed"
    assert by_name["task.finished"]["status"] == "completed"
    assert by_name["task.finished"]["duration_ms"] >= 0
    assert "organize photos" not in "\n".join(
        record.message for record in caplog.records
    )


def test_cancel_while_waiting_for_phone_unblocks_agent(tmp_path: Path) -> None:
    async def run() -> None:
        stream = ScriptedModel(
            [
                make_assistant(
                    [make_tool_call("search_photos", PHOTO_SEARCH)],
                    "toolUse",
                )
            ]
        )
        service = make_service(tmp_path, stream)
        device_id = str(uuid.uuid4())
        record = service.create_task("organize photos", device_id, str(uuid.uuid4()))
        running = service._runs[record.id]

        async with asyncio.timeout(1):
            while record.status != "waiting_for_phone":
                await asyncio.sleep(0)

        service.cancel_task(record.id, device_id)
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0)

        assert running.cancelled()
        assert record.status == "cancelled"
        assert record.error is None
        assert service.sessions[record.session_id].active_task_id is None
        with pytest.raises(KeyError, match="unknown task"):
            await service.broker.next_call(record.id, wait_seconds=0.01)
        await service.close()

    asyncio.run(run())


def test_cancel_during_model_response_unblocks_agent(tmp_path: Path) -> None:
    def blocking_stream(*_args: object) -> AssistantResponseStream:
        return AssistantResponseStream()

    async def run() -> None:
        service = WellphoneService(
            DEEPSEEK_MODELS[0],
            blocking_stream,
            sessions_root=tmp_path,
        )
        device_id = str(uuid.uuid4())
        record = service.create_task("hello", device_id, str(uuid.uuid4()))
        running = service._runs[record.id]

        async with asyncio.timeout(1):
            while not service.sessions[record.session_id].agent.state.is_streaming:
                await asyncio.sleep(0)

        service.cancel_task(record.id, device_id)
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0)

        assert running.cancelled()
        assert record.status == "cancelled"
        assert record.error is None
        assert service.sessions[record.session_id].active_task_id is None
        await service.close()

    asyncio.run(run())


def test_model_error_reaches_task_record(tmp_path: Path) -> None:
    failed = make_assistant(
        [TextContent(type="text", text="provider failed")],
        "stop",
    )
    failed.stop_reason = "error"
    failed.error_message = "provider unavailable"

    def failing_stream(*_args: object) -> AssistantResponseStream:
        stream = AssistantResponseStream()
        stream.push(ResponseErrorEvent(type="error", reason="error", error=failed))
        return stream

    async def run() -> None:
        service = WellphoneService(
            DEEPSEEK_MODELS[0],
            failing_stream,
            sessions_root=tmp_path,
        )
        device_id = str(uuid.uuid4())
        record = service.create_task("hello", device_id, str(uuid.uuid4()))
        running = service._runs[record.id]

        await running
        await asyncio.sleep(0)

        assert record.status == "failed"
        assert record.error == "provider unavailable"
        with pytest.raises(ValueError, match="failed task cannot be cancelled"):
            service.cancel_task(record.id, device_id)
        assert service.sessions[record.session_id].active_task_id is None
        await service.close()

    asyncio.run(run())


def test_session_history_is_reused_and_persisted(tmp_path: Path) -> None:
    async def run() -> None:
        stream = ScriptedModel(
            [
                make_assistant([TextContent(type="text", text="answer 1")], "stop"),
                make_assistant([TextContent(type="text", text="answer 2")], "stop"),
            ]
        )
        service = make_service(tmp_path, stream)
        device_id = str(uuid.uuid4())

        first = service.create_task("first", device_id, str(uuid.uuid4()))
        await service._runs[first.id]
        await asyncio.sleep(0)
        second = service.create_task(
            "second",
            device_id,
            str(uuid.uuid4()),
            first.session_id,
        )
        await service._runs[second.id]
        await asyncio.sleep(0)

        assert "answer 1" in (stream.contexts[1].system_prompt or "")
        await service.close()

        restored = make_service(tmp_path)
        messages = restored.require_session(first.session_id, device_id).messages
        assert [message.content for message in messages] == [
            "first",
            "answer 1",
            "second",
            "answer 2",
        ]
        await restored.close()

    asyncio.run(run())


def test_http_retry_returns_same_task_and_hides_other_devices(tmp_path: Path) -> None:
    def blocking_stream(*_args: object) -> AssistantResponseStream:
        return AssistantResponseStream()

    service = WellphoneService(
        DEEPSEEK_MODELS[0],
        blocking_stream,
        sessions_root=tmp_path,
    )
    device_id = str(uuid.uuid4())
    other_device_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    request = {"id": task_id, "prompt": "organize photos"}

    with TestClient(create_app(service)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        first = client.post(
            "/tasks",
            headers={"X-Wellphone-Device-ID": device_id},
            json=request,
        )
        retried = client.post(
            "/tasks",
            headers={"X-Wellphone-Device-ID": device_id},
            json=request,
        )
        hidden = client.get(
            f"/tasks/{task_id}",
            headers={"X-Wellphone-Device-ID": other_device_id},
        )
        cancelled = client.delete(
            f"/tasks/{task_id}",
            headers={"X-Wellphone-Device-ID": device_id},
        )
        cancelled_again = client.delete(
            f"/tasks/{task_id}",
            headers={"X-Wellphone-Device-ID": device_id},
        )
        cancelled_task = client.get(
            f"/tasks/{task_id}",
            headers={"X-Wellphone-Device-ID": device_id},
        )
        missing = client.get(
            "/tasks/missing",
            headers={"X-Wellphone-Device-ID": device_id},
        )
        invalid = client.post(
            "/tasks",
            headers={"X-Wellphone-Device-ID": device_id},
            json={"id": "not-a-uuid", "prompt": ""},
        )

        assert first.status_code == retried.status_code == 202
        assert first.json() == retried.json()
        assert hidden.status_code == 404
        assert hidden.json() == {"error": f"unknown task: {task_id}"}
        assert cancelled.json() == cancelled_again.json() == {"cancelled": True}
        assert cancelled_task.json()["status"] == "cancelled"
        assert cancelled_task.json()["error"] is None
        assert missing.status_code == 404
        assert invalid.status_code == 422
        assert invalid.json() == {"error": "request validation failed"}


def test_youtube_result_validates_external_json() -> None:
    result = _youtube_result(
        {
            "items": [
                {
                    "id": {"videoId": "abcdefghijk"},
                    "snippet": {
                        "title": "Agents &amp; Tools",
                        "channelTitle": "Geas",
                        "publishedAt": "2026-08-13T00:00:00Z",
                        "thumbnails": {
                            "medium": {"url": "https://example.test/a.jpg"}
                        },
                    },
                }
            ]
        }
    )
    videos = result["videos"]
    assert isinstance(videos, list)
    assert result["count"] == 1
    assert videos[0]["title"] == "Agents & Tools"
    with pytest.raises(RuntimeError, match="无效数据"):
        _youtube_result({"items": "invalid"})

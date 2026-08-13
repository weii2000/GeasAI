from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from geas.ai.event_stream import AssistantResponseStream
from geas.ai.model_registry import StreamFunction
from geas.ai.providers import builtin_models
from geas.ai.types import (
    AssistantMessage,
    Context,
    Model,
    ResponseDoneEvent,
    StreamOptions,
    TextContent,
    Usage,
    UsageCost,
)

from .agent import _youtube_result
from .broker import ToolBroker
from .protocol import ToolResultEnvelope
from .server import create_app
from .service import TaskRecord, WellphoneService
from .session import ConversationMessage


def make_service(
    root: Path | None = None,
    stream_function: StreamFunction | None = None,
) -> WellphoneService:
    models = builtin_models()
    model = models.get_model("zai", "glm-5.2")
    assert model is not None
    return WellphoneService(
        model,
        stream_function or models.stream,
        sessions_root=root,
    )


def scripted_stream(
    contexts: list[Context],
) -> StreamFunction:
    def stream(
        _model: Model,
        context: Context,
        _options: StreamOptions | None = None,
    ) -> AssistantResponseStream:
        contexts.append(context)
        message = AssistantMessage(
            role="assistant",
            content=[
                TextContent(type="text", text=f"answer {len(contexts)}")
            ],
            api="test",
            provider="test",
            model="test",
            usage=Usage(
                input=0,
                output=0,
                cache_read=0,
                cache_write=0,
                total_tokens=0,
                cost=UsageCost(0, 0, 0, 0, 0),
            ),
            stop_reason="stop",
            timestamp=0,
        )
        response = AssistantResponseStream()
        response.push(ResponseDoneEvent(type="done", reason="stop", message=message))
        return response

    return stream


async def check_broker_round_trip() -> None:
    broker = ToolBroker(result_timeout=1)
    broker.create_task("task-1")
    waiting = asyncio.create_task(
        broker.dispatch(
            "task-1",
            "call-1",
            "search_photos",
            {"start": "2026-08-01T00:00:00+01:00"},
        )
    )

    call = await broker.next_call("task-1", wait_seconds=0.1)
    assert call is not None
    assert call.name == "search_photos"
    assert call.arguments["start"] == "2026-08-01T00:00:00+01:00"
    assert await broker.next_call("task-1", wait_seconds=0.1) == call

    broker.submit_result(
        "task-1",
        ToolResultEnvelope(
            call_id="call-1",
            result={"count": 2},
        ),
    )
    broker.submit_result(
        "task-1",
        ToolResultEnvelope(call_id="call-1", result={"count": 2}),
    )
    result = await waiting
    assert result.result == {"count": 2}
    assert result.for_model() == '{"count":2,"ok":true}'

    expiring = ToolBroker(result_timeout=0.01)
    expiring.create_task("task-2")
    try:
        await expiring.dispatch("task-2", "call-2", "search_photos", {})
    except TimeoutError:
        pass
    else:
        raise AssertionError("missing phone result must time out")
    assert await expiring.next_call("task-2", wait_seconds=0.01) is None

    await broker.close_task("task-1")
    assert await broker.next_call("task-1", wait_seconds=0.01) is None
    broker.submit_result(
        "task-1",
        ToolResultEnvelope(call_id="call-1", result={"count": 2}),
    )


async def check_idempotent_task_creation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = make_service(root)
        task_id = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        first = service.create_task("organize photos", device_id, task_id)
        assert service.create_task("organize photos", device_id, task_id) is first
        try:
            service.create_task("different prompt", device_id, task_id)
        except ValueError:
            pass
        else:
            raise AssertionError("a task id must not be reused for another prompt")
        try:
            service.require_task(task_id, str(uuid.uuid4()))
        except KeyError:
            pass
        else:
            raise AssertionError("another device must not read this task")
        service.cancel_task(task_id, device_id)
        await asyncio.gather(*service._runs.values(), return_exceptions=True)
        await asyncio.sleep(0)
        assert first.status == "failed"
        follow_up_id = str(uuid.uuid4())
        follow_up = service.create_task(
            "follow up",
            device_id,
            follow_up_id,
            first.session_id,
        )
        assert follow_up.session_id == first.session_id
        service.cancel_task(follow_up_id, device_id)
        await asyncio.gather(*service._runs.values(), return_exceptions=True)
        await asyncio.sleep(0)
        session = service.require_session(first.session_id, device_id)
        session.messages.append(
            ConversationMessage(
                id=uuid.uuid4().hex,
                role="user",
                content="persist me",
                timestamp="2026-08-13T12:00:00+00:00",
            )
        )
        service.store.save(session)
        await service.close()

        restored = make_service(root)
        session = restored.require_session(first.session_id, device_id)
        assert session.id == first.session_id
        assert session.messages[-1].content == "persist me"
        await restored.close()


async def check_conversation_session() -> None:
    with tempfile.TemporaryDirectory() as directory:
        contexts: list[Context] = []
        root = Path(directory)
        service = make_service(root, scripted_stream(contexts))
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

        session = service.require_session(first.session_id, device_id)
        assert [message.content for message in session.messages] == [
            "first",
            "answer 1",
            "second",
            "answer 2",
        ]
        assert "answer 1" in (contexts[1].system_prompt or "")
        assert session.agent.state.messages == []
        await service.close()

        restored = make_service(root, scripted_stream([]))
        assert len(
            restored.require_session(first.session_id, device_id).messages
        ) == 4
        await restored.close()


def check_fastapi_boundary() -> None:
    with tempfile.TemporaryDirectory() as directory:
        service = make_service(Path(directory), scripted_stream([]))
        task_id = str(uuid.uuid4())
        session_id = uuid.uuid4().hex
        device_id = str(uuid.uuid4())
        headers = {"X-Wellphone-Device-ID": device_id}
        service.tasks[task_id] = TaskRecord(
            id=task_id,
            session_id=session_id,
            device_id=device_id,
            prompt="organize photos",
        )
        service.sessions[session_id] = service._build_session(
            session_id,
            device_id,
        )
        service.broker.create_task(task_id)

        with TestClient(create_app(service)) as client:
            assert client.get("/health").json() == {"status": "ok"}

            created = client.post(
                "/tasks",
                headers=headers,
                json={"id": task_id, "prompt": "organize photos"},
            )
            assert created.status_code == 202
            assert created.json() == {
                "id": task_id,
                "session_id": session_id,
                "prompt": "organize photos",
                "status": "running",
                "answer": None,
                "error": None,
            }

            session = client.get(
                f"/sessions/{session_id}",
                headers=headers,
            )
            assert session.status_code == 200
            assert session.json()["messages"] == []

            follow_up_id = str(uuid.uuid4())
            follow_up = client.post(
                "/tasks",
                headers=headers,
                json={
                    "id": follow_up_id,
                    "session_id": session_id,
                    "prompt": "follow up",
                },
            )
            assert follow_up.status_code == 202
            assert follow_up.json()["session_id"] == session_id

            missing = client.get("/tasks/missing", headers=headers)
            assert missing.status_code == 404
            assert missing.json() == {"error": "unknown task: missing"}

            invalid = client.post(
                "/tasks",
                headers=headers,
                json={"id": "not-a-uuid", "prompt": ""},
            )
            assert invalid.status_code == 422
            assert invalid.json() == {"error": "request validation failed"}


def check_youtube_result() -> None:
    result = _youtube_result(
        {
            "items": [
                {
                    "id": {"videoId": "abcdefghijk"},
                    "snippet": {
                        "title": "Agents &amp; Tools",
                        "channelTitle": "Geas",
                        "publishedAt": "2026-08-13T00:00:00Z",
                        "thumbnails": {"medium": {"url": "https://example.test/a.jpg"}},
                    },
                }
            ]
        }
    )
    assert result["count"] == 1
    assert result["videos"][0]["title"] == "Agents & Tools"


def main() -> None:
    async def check_all() -> None:
        await check_broker_round_trip()
        await check_idempotent_task_creation()
        await check_conversation_session()

    asyncio.run(check_all())
    check_fastapi_boundary()
    check_youtube_result()
    print("Wellphone protocol self-check passed")


if __name__ == "__main__":
    main()

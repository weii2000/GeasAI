from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient

from geas.ai.providers import builtin_models

from .broker import ToolBroker
from .protocol import ToolResultEnvelope
from .server import create_app
from .service import TaskRecord, WellphoneService


def make_service() -> WellphoneService:
    models = builtin_models()
    model = models.get_model("zai", "glm-5.2")
    assert model is not None
    return WellphoneService(model, models.stream)


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
    service = make_service()
    task_id = str(uuid.uuid4())
    first = service.create_task("organize photos", task_id)
    assert service.create_task("organize photos", task_id) is first
    try:
        service.create_task("different prompt", task_id)
    except ValueError:
        pass
    else:
        raise AssertionError("a task id must not be reused for another prompt")
    service.cancel_task(task_id)
    await asyncio.sleep(0)
    assert first.status == "failed"
    await service.close()


def check_fastapi_boundary() -> None:
    service = make_service()
    task_id = str(uuid.uuid4())
    service.tasks[task_id] = TaskRecord(
        id=task_id,
        prompt="organize photos",
    )
    service.broker.create_task(task_id)

    with TestClient(create_app(service)) as client:
        assert client.get("/health").json() == {"status": "ok"}

        created = client.post(
            "/tasks",
            json={"id": task_id, "prompt": "organize photos"},
        )
        assert created.status_code == 202
        assert created.json() == {
            "id": task_id,
            "prompt": "organize photos",
            "status": "running",
            "answer": None,
            "error": None,
        }

        missing = client.get("/tasks/missing")
        assert missing.status_code == 404
        assert missing.json() == {"error": "unknown task: missing"}

        invalid = client.post(
            "/tasks",
            json={"id": "not-a-uuid", "prompt": ""},
        )
        assert invalid.status_code == 422
        assert invalid.json() == {"error": "request validation failed"}


def main() -> None:
    async def check_all() -> None:
        await check_broker_round_trip()
        await check_idempotent_task_creation()

    asyncio.run(check_all())
    check_fastapi_boundary()
    print("Wellphone protocol self-check passed")


if __name__ == "__main__":
    main()

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .protocol import (
    CreateTaskRequest,
    SessionResponse,
    TaskResponse,
    ToolPollResponse,
    ToolResultEnvelope,
)
from .service import WellphoneService


DeviceID = Annotated[UUID, Header(alias="X-Wellphone-Device-ID")]


def create_app(service: WellphoneService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await service.close()

    app = FastAPI(title="Wellphone Agent Server", lifespan=lifespan)

    @app.exception_handler(KeyError)
    async def not_found(_: Request, error: KeyError) -> JSONResponse:
        message = error.args[0] if error.args else "not found"
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": str(message)},
        )

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": str(error)},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _: Request,
        __: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"error": "request validation failed"},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/tasks",
        response_model=TaskResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_task(
        request: CreateTaskRequest,
        device_id: DeviceID,
    ) -> dict[str, object]:
        record = service.create_task(
            request.prompt,
            str(device_id),
            str(request.id) if request.id is not None else None,
            (
                request.session_id.hex
                if request.session_id is not None
                else None
            ),
            request.device_context,
        )
        return record.to_dict()

    @app.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(
        task_id: str,
        device_id: DeviceID,
    ) -> dict[str, object]:
        return service.require_task(task_id, str(device_id)).to_dict()

    @app.get("/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(
        session_id: str,
        device_id: DeviceID,
    ) -> dict[str, object]:
        return service.require_session(session_id, str(device_id)).to_dict()

    @app.get(
        "/tasks/{task_id}/next-tool",
        response_model=ToolPollResponse,
    )
    async def next_tool(
        task_id: str,
        device_id: DeviceID,
    ) -> ToolPollResponse:
        service.require_task(task_id, str(device_id))
        call = await service.broker.next_call(task_id)
        return ToolPollResponse(tool_call=call)

    @app.post("/tasks/{task_id}/tool-result")
    async def submit_result(
        task_id: str,
        result: ToolResultEnvelope,
        device_id: DeviceID,
    ) -> dict[str, bool]:
        service.require_task(task_id, str(device_id))
        service.broker.submit_result(task_id, result)
        return {"accepted": True}

    @app.delete("/tasks/{task_id}")
    async def cancel_task(
        task_id: str,
        device_id: DeviceID,
    ) -> dict[str, bool]:
        service.cancel_task(task_id, str(device_id))
        return {"cancelled": True}

    return app

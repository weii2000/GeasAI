import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import httpx2

from geas.ai.types import TextContent
from geas.integrations.mcp import MCPRegistry

from .types import Plan, Task


PLANWISE_SERVER_NAME = "planwise"


@dataclass
class PlanWiseAuth:
    base_url: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)

    async def get_access_token(self) -> str:
        if time.time() < _expires_at(self.access_token) - 30:
            return self.access_token

        async with httpx2.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            cookies={"refreshToken": self.refresh_token},
        ) as client:
            response = await client.post("/api/auth/refresh")
        self.access_token, self.refresh_token = _tokens(response)
        return self.access_token


async def login_planwise(
    mcp_url: str,
    username: str,
    password: str,
) -> PlanWiseAuth:
    """Log in to PlanWise and keep the tokens needed by MCP."""
    if not username or not password:
        raise ValueError("PlanWise username and password are required")

    parsed = urlsplit(mcp_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    async with httpx2.AsyncClient(base_url=base_url, timeout=10.0) as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )

    access_token, refresh_token = _tokens(response)
    return PlanWiseAuth(base_url, access_token, refresh_token)


def _tokens(response: httpx2.Response) -> tuple[str, str]:
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if response.is_error:
        message = body.get("message") if isinstance(body, dict) else None
        raise ValueError(
            message if isinstance(message, str) else "PlanWise login failed"
        )
    data = body.get("data") if isinstance(body, dict) else None
    token = data.get("accessToken") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError("PlanWise response has no access token")
    refresh_token = response.cookies.get("refreshToken")
    if not refresh_token:
        raise ValueError("PlanWise response has no refresh token")
    return token, refresh_token


def _expires_at(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expires_at = json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid PlanWise access token") from error
    if not isinstance(expires_at, int):
        raise ValueError("Invalid PlanWise access token expiry")
    return expires_at


@dataclass(frozen=True)
class PlanPublication:
    plan_id: int
    plan_title: str
    created_task_count: int


async def publish_plan(
    registry: MCPRegistry,
    session_id: str,
    plan: Plan,
) -> PlanPublication:
    result = await registry.call(
        PLANWISE_SERVER_NAME,
        "create_plan",
        _create_plan_payload(session_id, plan),
    )
    data = result.details
    if not isinstance(data, dict):
        text = next(
            (
                block.text
                for block in result.content
                if isinstance(block, TextContent)
            ),
            "",
        )
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError("Invalid PlanWise response") from error

    plan_id = data.get("plan_id")
    plan_title = data.get("plan_title")
    created_task_count = data.get("created_task_count")
    if (
        not isinstance(plan_id, int)
        or isinstance(plan_id, bool)
        or not isinstance(plan_title, str)
        or not isinstance(created_task_count, int)
        or isinstance(created_task_count, bool)
    ):
        raise ValueError("Invalid PlanWise response")
    return PlanPublication(plan_id, plan_title, created_task_count)


def _create_plan_payload(
    session_id: str,
    plan: Plan,
) -> dict[str, object]:
    if not plan.title.strip() or not plan.goal.strip() or not plan.tasks:
        raise ValueError("Plan title, goal, and tasks are required")
    return {
        "idempotency_key": session_id,
        "plan": {
            "title": plan.title,
            "goal": plan.goal,
            "description": plan.description,
            "tasks": [_task_payload(task) for task in plan.tasks],
        },
    }


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "title": task.title,
        "level": task.level,
        "status": task.status.value,
        "acceptance_criteria": task.acceptance_criteria,
        "start_time": _isoformat(task.start_time),
        "due_time": _isoformat(task.due_time),
        "subtasks": [_task_payload(subtask) for subtask in task.subtasks],
    }


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None

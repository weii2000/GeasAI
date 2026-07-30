import json
from dataclasses import dataclass
from datetime import datetime

from geas.ai.types import TextContent
from geas.mcp import MCPRegistry
from geas.plan_agent.types import Plan, Task


PLANWISE_SERVER_NAME = "planwise"


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

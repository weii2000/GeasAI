import asyncio
from datetime import datetime
from types import SimpleNamespace

import geas.mcp
from geas.mcp import (
    MCPRegistry,
    MCPServerConfig,
    create_mcp_call_tool,
)
from geas.plan_agent.types import Plan, Task
from geas.actions.publish_plan import publish_plan
from mcp.types import TextContent


def test_mcp_connects_lazily_and_reuses_client(monkeypatch) -> None:
    class FakeHTTPClient:
        instances: list["FakeHTTPClient"] = []

        def __init__(self, **options: object) -> None:
            self.options = options
            self.closed = False
            self.instances.append(self)

        async def __aenter__(self) -> "FakeHTTPClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.closed = True

    class FakeClient:
        instances: list["FakeClient"] = []

        def __init__(self, transport: object) -> None:
            self.transport = transport
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.closed = False
            self.instances.append(self)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.closed = True

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object],
        ) -> object:
            self.calls.append((name, arguments))
            return SimpleNamespace(
                content=[TextContent(text="created")],
                structured_content={"id": 1},
                is_error=False,
            )

    monkeypatch.setattr(geas.mcp.httpx2, "AsyncClient", FakeHTTPClient)
    monkeypatch.setattr(
        geas.mcp,
        "streamable_http_client",
        lambda url, http_client: (url, http_client),
    )
    monkeypatch.setattr(geas.mcp, "Client", FakeClient)
    registry = MCPRegistry(
        {
            "tasks": MCPServerConfig(
                url="https://tasks.example/mcp",
            )
        }
    )
    registry.set_token("tasks", "secret")
    tool = create_mcp_call_tool(registry)

    async def run() -> None:
        async with registry:
            assert FakeClient.instances == []
            assert FakeHTTPClient.instances == []
            for _ in range(2):
                result = await tool.execute(
                    "call-id",
                    {
                        "server": "tasks",
                        "tool": "create_task",
                        "arguments": {"title": "Build Geas"},
                    },
                )
                assert result.content[0].text == "created"

        assert FakeClient.instances[0].closed
        assert FakeHTTPClient.instances[0].closed

    asyncio.run(run())

    assert len(FakeClient.instances) == 1
    assert len(FakeClient.instances[0].calls) == 2
    assert FakeHTTPClient.instances[0].options["headers"] == {
        "Authorization": "Bearer secret",
    }


def test_planwise_create_plan_payload_is_deterministic() -> None:
    class FakeRegistry:
        calls: list[tuple[str, str, dict[str, object]]] = []

        async def call(
            self,
            server: str,
            tool: str,
            arguments: dict[str, object],
        ) -> object:
            self.calls.append((server, tool, arguments))
            return SimpleNamespace(
                content=[],
                details={
                    "plan_id": 123,
                    "plan_title": "发布 Geas",
                    "created_task_count": 1,
                },
            )

    registry = FakeRegistry()
    plan = Plan(
        title="发布 Geas",
        goal="完成 Agent",
        description="实现并测试",
        acceptance_criterion="测试通过",
        constraints=["只使用 Python"],
        tasks=[
            Task(
                title="实现 MCP",
                level=1,
                acceptance_criteria="端到端调用成功",
                start_time=datetime.fromisoformat(
                    "2026-08-01T09:00:00+01:00"
                ),
            )
        ],
    )

    async def run() -> None:
        for _ in range(2):
            publication = await publish_plan(  # type: ignore[arg-type]
                registry,
                "same-session-id",
                plan,
            )
            assert publication.plan_id == 123

    asyncio.run(run())

    first = registry.calls[0]
    assert registry.calls == [first, first]
    assert first[:2] == ("planwise", "create_plan")
    payload = first[2]
    assert payload["idempotency_key"] == "same-session-id"
    remote_plan = payload["plan"]
    assert isinstance(remote_plan, dict)
    assert "acceptance_criterion" not in remote_plan
    assert "constraints" not in remote_plan
    tasks = remote_plan["tasks"]
    assert isinstance(tasks, list)
    assert tasks[0]["start_time"] == "2026-08-01T09:00:00+01:00"

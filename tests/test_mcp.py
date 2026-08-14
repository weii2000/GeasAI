import asyncio
from datetime import datetime
import re
from types import SimpleNamespace

import apps.blueprint.planwise
import apps.blueprint.rpc
import geas.integrations.mcp
import httpx2
import pytest
from geas.integrations.mcp import (
    MCPRegistry,
    MCPServerConfig,
    create_mcp_tools,
)
from apps.blueprint.planwise import login_planwise, publish_plan
from apps.blueprint.types import Plan, Task
from mcp.types import (
    CallToolResult,
    ImageContent,
    Prompt,
    Resource,
    ResourceTemplate,
    TextContent,
    Tool,
)


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
            self.server_capabilities = SimpleNamespace(
                tools=object(),
                resources=None,
                prompts=None,
            )
            self.instances.append(self)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.closed = True

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object],
        ) -> CallToolResult:
            self.calls.append((name, arguments))
            return CallToolResult(
                content=[TextContent(text="created")],
                structured_content={"id": 1},
                is_error=False,
            )

        async def list_tools(self, *, cursor: str | None) -> object:
            assert cursor is None
            return SimpleNamespace(
                tools=[
                    Tool(
                        name="create_task",
                        description="Create a task",
                        input_schema={
                            "type": "object",
                            "properties": {"title": {"type": "string"}},
                            "required": ["title"],
                        },
                    )
                ],
                next_cursor=None,
            )

    monkeypatch.setattr(
        geas.integrations.mcp.httpx2,
        "AsyncClient",
        FakeHTTPClient,
    )
    monkeypatch.setattr(
        geas.integrations.mcp,
        "streamable_http_client",
        lambda url, http_client: (url, http_client),
    )
    monkeypatch.setattr(geas.integrations.mcp, "Client", FakeClient)
    registry = MCPRegistry(
        {
            "tasks": MCPServerConfig(
                url="https://tasks.example/mcp",
            )
        }
    )
    registry.set_token("tasks", "secret")

    async def run() -> None:
        async with registry:
            assert FakeClient.instances == []
            assert FakeHTTPClient.instances == []
            tools = await create_mcp_tools(registry)
            assert [tool.name for tool in tools] == [
                "mcp__tasks__create_task"
            ]
            tool = tools[0]
            assert tool.description == "Create a task"
            assert tool.parameters["required"] == ["title"]
            for _ in range(2):
                result = await tool.execute(
                    "call-id",
                    {"title": "Build Geas"},
                )
                assert [block.text for block in result.content] == [
                    "created",
                    '{"id": 1}',
                ]
                assert result.details == {"id": 1}

        assert FakeClient.instances[0].closed
        assert FakeHTTPClient.instances[0].closed

    asyncio.run(run())

    assert len(FakeClient.instances) == 1
    assert len(FakeClient.instances[0].calls) == 2
    auth = FakeHTTPClient.instances[0].options["auth"]
    request = httpx2.Request("POST", "https://tasks.example/mcp")
    assert list(auth.auth_flow(request))[0].headers["Authorization"] == (
        "Bearer secret"
    )
    registry.set_token("tasks", "new-secret")
    request = httpx2.Request("POST", "https://tasks.example/mcp")
    assert list(auth.auth_flow(request))[0].headers["Authorization"] == (
        "Bearer new-secret"
    )


def test_mcp_discovers_all_capabilities_with_pagination() -> None:
    class FakeClient:
        server_capabilities = SimpleNamespace(
            tools=object(),
            resources=object(),
            prompts=object(),
        )

        async def list_tools(self, *, cursor: str | None) -> object:
            return SimpleNamespace(
                tools=[
                    Tool(
                        name="first" if cursor is None else "second",
                        input_schema={"type": "object"},
                    )
                ],
                next_cursor="tools-2" if cursor is None else None,
            )

        async def list_resources(self, *, cursor: str | None) -> object:
            return SimpleNamespace(
                resources=[
                    Resource(
                        name="first" if cursor is None else "second",
                        uri=f"file:///{cursor or 'resources-1'}",
                    )
                ],
                next_cursor="resources-2" if cursor is None else None,
            )

        async def list_resource_templates(
            self,
            *,
            cursor: str | None,
        ) -> object:
            return SimpleNamespace(
                resource_templates=[
                    ResourceTemplate(
                        name="first" if cursor is None else "second",
                        uri_template=f"notes:///{cursor or 'templates-1'}/{{id}}",
                    )
                ],
                next_cursor="templates-2" if cursor is None else None,
            )

        async def list_prompts(self, *, cursor: str | None) -> object:
            return SimpleNamespace(
                prompts=[
                    Prompt(name="first" if cursor is None else "second")
                ],
                next_cursor="prompts-2" if cursor is None else None,
            )

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, object] | None,
        ) -> CallToolResult:
            return CallToolResult(content=[TextContent(text=name)])

        async def read_resource(self, uri: str) -> object:
            return SimpleNamespace(contents=[uri])

        async def get_prompt(
            self,
            name: str,
            arguments: dict[str, str] | None,
        ) -> object:
            return SimpleNamespace(messages=[name, arguments])

    registry = MCPRegistry({"catalog": MCPServerConfig("https://example/mcp")})
    registry._clients["catalog"] = FakeClient()  # type: ignore[assignment]

    async def run() -> None:
        assert [tool.name for tool in await registry.list_tools("catalog")] == [
            "first",
            "second",
        ]
        assert [
            resource.name
            for resource in await registry.list_resources("catalog")
        ] == ["first", "second"]
        assert [
            template.name
            for template in await registry.list_resource_templates("catalog")
        ] == ["first", "second"]
        assert [
            prompt.name for prompt in await registry.list_prompts("catalog")
        ] == ["first", "second"]
        assert (
            await registry.call_tool("catalog", "run", {"value": 1})
        ).content[0].text == "run"
        assert (
            await registry.read_resource("catalog", "file:///one")
        ).contents == ["file:///one"]
        assert (
            await registry.get_prompt("catalog", "draft", {"tone": "short"})
        ).messages == ["draft", {"tone": "short"}]

    asyncio.run(run())


def test_mcp_unsupported_capabilities_are_explicit() -> None:
    class FakeClient:
        server_capabilities = SimpleNamespace(
            tools=None,
            resources=None,
            prompts=None,
        )

    registry = MCPRegistry({"empty": MCPServerConfig("https://example/mcp")})
    registry._clients["empty"] = FakeClient()  # type: ignore[assignment]

    async def run() -> None:
        assert await registry.list_tools("empty") == []
        assert await registry.list_resources("empty") == []
        assert await registry.list_resource_templates("empty") == []
        assert await registry.list_prompts("empty") == []
        with pytest.raises(RuntimeError, match="does not support tools"):
            await registry.call_tool("empty", "run")
        with pytest.raises(RuntimeError, match="does not support resources"):
            await registry.read_resource("empty", "file:///one")
        with pytest.raises(RuntimeError, match="does not support prompts"):
            await registry.get_prompt("empty", "draft")

    asyncio.run(run())


def test_mcp_tool_adapter_rejects_invalid_remote_contracts() -> None:
    class FakeRegistry:
        servers = {"one": object()}

        def __init__(self) -> None:
            self.tools: list[Tool] = []
            self.result = CallToolResult(content=[])

        async def list_tools(self, _server: str) -> list[Tool]:
            return self.tools

        async def call_tool(
            self,
            _server: str,
            _tool: str,
            _arguments: dict[str, object],
        ) -> CallToolResult:
            return self.result

    registry = FakeRegistry()

    async def run() -> None:
        registry.tools = [
            Tool(name="bad", input_schema={"type": "string"})
        ]
        with pytest.raises(ValueError, match="invalid input schema"):
            await create_mcp_tools(registry)  # type: ignore[arg-type]

        registry.tools = [
            Tool(
                name="malformed",
                input_schema={"type": "object", "required": "name"},
            )
        ]
        with pytest.raises(ValueError, match="invalid input schema"):
            await create_mcp_tools(registry)  # type: ignore[arg-type]

        registry.tools = [
            Tool(name="a.b", input_schema={"type": "object"}),
            Tool(name="a/b", input_schema={"type": "object"}),
        ]
        with pytest.raises(ValueError, match="Duplicate MCP agent tool name"):
            await create_mcp_tools(registry)  # type: ignore[arg-type]

        registry.tools = [
            Tool(name="x" * 100, input_schema={"type": "object"})
        ]
        first = await create_mcp_tools(registry)  # type: ignore[arg-type]
        second = await create_mcp_tools(registry)  # type: ignore[arg-type]
        assert first[0].name == second[0].name
        assert len(first[0].name) <= 64
        assert re.fullmatch(r"[A-Za-z0-9_-]+", first[0].name)

        registry.result = CallToolResult(
            content=[
                ImageContent(data="AA==", mime_type="image/png")
            ]
        )
        with pytest.raises(RuntimeError, match="unsupported image content"):
            await first[0].execute("call-id", {})

        registry.result = CallToolResult(
            content=[TextContent(text="remote failure")],
            is_error=True,
        )
        with pytest.raises(RuntimeError, match="remote failure"):
            await first[0].execute("call-id", {})

    asyncio.run(run())


def test_blueprint_discovers_current_tools_except_planwise(monkeypatch) -> None:
    servers = {
        "tasks": MCPServerConfig("https://tasks.example/mcp"),
        "planwise": MCPServerConfig("https://planwise.example/mcp"),
    }
    discovered: list[list[str]] = []
    received_tools: list[object] = []

    class FakeRegistry:
        def __init__(self, configured: object) -> None:
            assert configured == servers

        async def __aenter__(self) -> "FakeRegistry":
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

    async def discover(
        _registry: object,
        allowed_servers: list[str],
    ) -> list[object]:
        discovered.append(allowed_servers)
        return ["dynamic-tool"]

    class FakeRPCServer:
        def __init__(
            self,
            _models: object,
            _registry: object,
            _skills_root: object,
            tools: list[object],
        ) -> None:
            received_tools.extend(tools)

        def close(self) -> None:
            pass

    async def serve(_server: object) -> None:
        pass

    monkeypatch.setattr(apps.blueprint.rpc, "load_project_env", lambda: None)
    monkeypatch.setattr(apps.blueprint.rpc, "builtin_models", object)
    monkeypatch.setattr(apps.blueprint.rpc, "load_mcp_servers", lambda: servers)
    monkeypatch.setattr(apps.blueprint.rpc, "MCPRegistry", FakeRegistry)
    monkeypatch.setattr(apps.blueprint.rpc, "create_mcp_tools", discover)
    monkeypatch.setattr(apps.blueprint.rpc, "RPCServer", FakeRPCServer)
    monkeypatch.setattr(apps.blueprint.rpc, "_serve", serve)

    asyncio.run(apps.blueprint.rpc.main())

    assert discovered == [["tasks"]]
    assert received_tools == ["dynamic-tool"]


def test_planwise_create_plan_payload_is_deterministic() -> None:
    class FakeRegistry:
        calls: list[tuple[str, str, dict[str, object]]] = []

        async def call_tool(
            self,
            server: str,
            tool: str,
            arguments: dict[str, object],
        ) -> CallToolResult:
            self.calls.append((server, tool, arguments))
            return CallToolResult(
                content=[],
                structured_content={
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


def test_planwise_login_returns_access_token(monkeypatch) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/auth/login":
            assert request.content == b'{"username":"wei","password":"secret"}'
            token = "x.eyJleHAiOjB9.x"
            refresh_token = "refresh-1"
        else:
            assert request.url.path == "/api/auth/refresh"
            assert request.headers["Cookie"] == "refreshToken=refresh-1"
            token = "x.eyJleHAiOjQxMDI0NDQ4MDB9.x"
            refresh_token = "refresh-2"
        return httpx2.Response(
            200,
            json={"data": {"accessToken": token}},
            headers={
                "set-cookie": (
                    f"refreshToken={refresh_token}; Path=/api/auth; HttpOnly"
                )
            },
        )

    client_type = httpx2.AsyncClient
    transport = httpx2.MockTransport(handle)
    monkeypatch.setattr(
        apps.blueprint.planwise.httpx2,
        "AsyncClient",
        lambda **options: client_type(transport=transport, **options),
    )
    auth = asyncio.run(
        login_planwise(
            "http://127.0.0.1:8000/mcp",
            "wei",
            "secret",
        )
    )
    assert asyncio.run(auth.get_access_token()) == (
        "x.eyJleHAiOjQxMDI0NDQ4MDB9.x"
    )
    assert auth.refresh_token == "refresh-2"

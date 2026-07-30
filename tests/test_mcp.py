import asyncio
from types import SimpleNamespace

import geas.mcp
from geas.mcp import (
    MCPRegistry,
    MCPServerConfig,
    create_mcp_call_tool,
)
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

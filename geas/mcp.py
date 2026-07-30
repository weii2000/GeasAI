import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent as MCPTextContent

from geas.ai.types import TextContent
from geas.core.types import AgentTool, AgentToolResult


@dataclass(frozen=True)
class MCPServerConfig:
    url: str
    token: str | None = field(default=None, repr=False)


class MCPRegistry:
    def __init__(self, servers: dict[str, MCPServerConfig]) -> None:
        self.servers = dict(servers)
        self._clients: dict[str, Client] = {}
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "MCPRegistry":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        assert self._stack is not None
        await self._stack.aclose()
        self._stack = None
        self._clients.clear()

    def set_token(self, server: str, token: str) -> None:
        if not token:
            raise ValueError("MCP token cannot be empty")
        if server in self._clients:
            raise RuntimeError(
                f'MCP server "{server}" is already connected'
            )
        try:
            config = self.servers[server]
        except KeyError as error:
            raise KeyError(f'Unknown MCP server: "{server}"') from error
        self.servers[server] = replace(config, token=token)

    async def call(
        self,
        server: str,
        tool: str,
        arguments: dict[str, object],
    ) -> AgentToolResult:
        client = await self._client(server)
        result = await client.call_tool(tool, arguments)
        text = "\n".join(
            block.text
            for block in result.content
            if isinstance(block, MCPTextContent)
        )

        if not text and result.structured_content is not None:
            text = json.dumps(result.structured_content, ensure_ascii=False)

        if result.is_error:
            raise RuntimeError(text or f'MCP tool "{tool}" failed')

        return AgentToolResult(
            content=[TextContent(type="text", text=text)],
            details=result.structured_content,
        )

    async def _client(self, server: str) -> Client:
        if server in self._clients:
            return self._clients[server]
        if self._stack is None:
            raise RuntimeError("MCP registry is not running")

        try:
            config = self.servers[server]
        except KeyError as error:
            raise KeyError(f'Unknown MCP server: "{server}"') from error

        if config.token is None:
            client = await self._stack.enter_async_context(
                Client(config.url)
            )
        else:
            http_client = await self._stack.enter_async_context(
                httpx2.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {config.token}",
                    },
                    timeout=httpx2.Timeout(30.0, read=300.0),
                    follow_redirects=True,
                )
            )
            transport = streamable_http_client(
                config.url,
                http_client=http_client,
            )
            client = await self._stack.enter_async_context(
                Client(transport)
            )
        self._clients[server] = client
        return client


def create_mcp_call_tool(registry: MCPRegistry) -> AgentTool:
    async def execute(
        _tool_call_id: str,
        arguments: dict[str, object],
    ) -> AgentToolResult:
        tool_arguments = arguments["arguments"]
        if not isinstance(tool_arguments, dict):
            raise TypeError("MCP tool arguments must be an object")

        return await registry.call(
            server=str(arguments["server"]),
            tool=str(arguments["tool"]),
            arguments=tool_arguments,
        )

    return AgentTool(
        name="mcp_call",
        description=(
            "Call a tool from a configured MCP server. Read the relevant "
            "skill first for the server, tool, and argument contract."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "enum": list(registry.servers),
                },
                "tool": {"type": "string", "minLength": 1},
                "arguments": {"type": "object"},
            },
            "required": ["server", "tool", "arguments"],
            "additionalProperties": False,
        },
        execute=execute,
    )

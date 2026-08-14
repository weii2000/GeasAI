import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace

import httpx2
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    Prompt,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextContent as MCPTextContent,
    Tool as MCPTool,
)

from geas.ai.types import TextContent
from geas.core.types import AgentTool, AgentToolResult


_MAX_TOOL_NAME_LENGTH = 64


@dataclass(frozen=True)
class MCPServerConfig:
    url: str
    token: str | None = field(default=None, repr=False)


class _BearerAuth(httpx2.Auth):
    def __init__(self, registry: "MCPRegistry", server: str) -> None:
        self.registry = registry
        self.server = server

    def auth_flow(
        self,
        request: httpx2.Request,
    ) -> Iterator[httpx2.Request]:
        token = self.registry.servers[self.server].token
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request


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
        try:
            config = self.servers[server]
        except KeyError as error:
            raise KeyError(f'Unknown MCP server: "{server}"') from error
        self.servers[server] = replace(config, token=token)

    async def list_tools(self, server: str) -> list[MCPTool]:
        client = await self._client(server)
        if client.server_capabilities.tools is None:
            return []

        tools: list[MCPTool] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise RuntimeError(f'MCP server "{server}" repeated a cursor')
            seen_cursors.add(cursor)

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, object] | None = None,
    ) -> CallToolResult:
        client = await self._client(server)
        self._require_capability(client, server, "tools")
        return await client.call_tool(tool, arguments)

    async def list_resources(self, server: str) -> list[Resource]:
        client = await self._client(server)
        if client.server_capabilities.resources is None:
            return []

        resources: list[Resource] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await client.list_resources(cursor=cursor)
            resources.extend(page.resources)
            cursor = page.next_cursor
            if cursor is None:
                return resources
            if cursor in seen_cursors:
                raise RuntimeError(f'MCP server "{server}" repeated a cursor')
            seen_cursors.add(cursor)

    async def list_resource_templates(
        self,
        server: str,
    ) -> list[ResourceTemplate]:
        client = await self._client(server)
        if client.server_capabilities.resources is None:
            return []

        templates: list[ResourceTemplate] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await client.list_resource_templates(cursor=cursor)
            templates.extend(page.resource_templates)
            cursor = page.next_cursor
            if cursor is None:
                return templates
            if cursor in seen_cursors:
                raise RuntimeError(f'MCP server "{server}" repeated a cursor')
            seen_cursors.add(cursor)

    async def read_resource(
        self,
        server: str,
        uri: str,
    ) -> ReadResourceResult:
        client = await self._client(server)
        self._require_capability(client, server, "resources")
        return await client.read_resource(uri)

    async def list_prompts(self, server: str) -> list[Prompt]:
        client = await self._client(server)
        if client.server_capabilities.prompts is None:
            return []

        prompts: list[Prompt] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await client.list_prompts(cursor=cursor)
            prompts.extend(page.prompts)
            cursor = page.next_cursor
            if cursor is None:
                return prompts
            if cursor in seen_cursors:
                raise RuntimeError(f'MCP server "{server}" repeated a cursor')
            seen_cursors.add(cursor)

    async def get_prompt(
        self,
        server: str,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> GetPromptResult:
        client = await self._client(server)
        self._require_capability(client, server, "prompts")
        return await client.get_prompt(name, arguments)

    @staticmethod
    def _require_capability(
        client: Client,
        server: str,
        capability: str,
    ) -> None:
        if getattr(client.server_capabilities, capability) is None:
            raise RuntimeError(
                f'MCP server "{server}" does not support {capability}'
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

        http_client = await self._stack.enter_async_context(
            httpx2.AsyncClient(
                auth=_BearerAuth(self, server),
                timeout=httpx2.Timeout(30.0, read=300.0),
                follow_redirects=True,
            )
        )
        transport = streamable_http_client(
            config.url,
            http_client=http_client,
        )
        client = await self._stack.enter_async_context(Client(transport))
        self._clients[server] = client
        return client


async def create_mcp_tools(
    registry: MCPRegistry,
    allowed_servers: list[str] | None = None,
) -> list[AgentTool]:
    servers = list(registry.servers) if allowed_servers is None else allowed_servers
    if unknown := set(servers) - registry.servers.keys():
        raise ValueError(f"Unknown MCP servers: {sorted(unknown)}")

    agent_tools: list[AgentTool] = []
    names: set[str] = set()
    for server in servers:
        for tool in await registry.list_tools(server):
            if not tool.name:
                raise ValueError(f'MCP server "{server}" returned an unnamed tool')
            try:
                Draft202012Validator.check_schema(tool.input_schema)
            except SchemaError as error:
                raise ValueError(
                    f'MCP tool "{server}/{tool.name}" has an invalid '
                    "input schema"
                ) from error
            if tool.input_schema.get("type") != "object":
                raise ValueError(
                    f'MCP tool "{server}/{tool.name}" has an invalid input schema'
                )
            name = _agent_tool_name(server, tool.name)
            if name in names:
                raise ValueError(f'Duplicate MCP agent tool name: "{name}"')
            names.add(name)
            agent_tools.append(_create_agent_tool(registry, server, tool, name))
    return agent_tools


def _agent_tool_name(server: str, tool: str) -> str:
    raw = f"mcp__{server}__{tool}"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)
    if len(safe) <= _MAX_TOOL_NAME_LENGTH:
        return safe
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{safe[:_MAX_TOOL_NAME_LENGTH - len(digest) - 1]}_{digest}"


def _create_agent_tool(
    registry: MCPRegistry,
    server: str,
    tool: MCPTool,
    name: str,
) -> AgentTool:
    async def execute(
        _tool_call_id: str,
        arguments: dict[str, object],
    ) -> AgentToolResult:
        result = await registry.call_tool(server, tool.name, arguments)
        content: list[TextContent] = []
        for block in result.content:
            if not isinstance(block, MCPTextContent):
                raise RuntimeError(
                    f'MCP tool "{server}/{tool.name}" returned unsupported '
                    f'{block.type} content'
                )
            content.append(TextContent(type="text", text=block.text))
        if result.structured_content is not None:
            content.append(
                TextContent(
                    type="text",
                    text=json.dumps(
                        result.structured_content,
                        ensure_ascii=False,
                    ),
                )
            )
        if result.is_error:
            message = "\n".join(block.text for block in content)
            raise RuntimeError(message or f'MCP tool "{server}/{tool.name}" failed')
        if not content:
            content.append(
                TextContent(type="text", text="MCP tool completed with no content")
            )
        return AgentToolResult(
            content=content,
            details=result.structured_content,
        )

    description = tool.description or tool.title or f"Call {tool.name} on {server}"
    return AgentTool(
        name=name,
        description=description,
        parameters=tool.input_schema,
        execute=execute,
    )

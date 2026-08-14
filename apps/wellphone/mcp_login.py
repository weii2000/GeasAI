import argparse
import asyncio

from geas.integrations.mcp import MCPRegistry, create_mcp_tools

from .config import WellphoneConfig, load_config


async def login(config: WellphoneConfig, server: str) -> list[str]:
    try:
        server_config = config.mcp_servers[server]
    except KeyError as error:
        raise ValueError(f'Unknown Wellphone MCP server: "{server}"') from error
    if server_config.oauth is None:
        raise ValueError(f'MCP server "{server}" is not configured for OAuth')

    async with MCPRegistry(
        {server: server_config},
        interactive_oauth=True,
    ) as registry:
        tools = await create_mcp_tools(
            registry,
            allowed_servers=[server],
            allowed_tools_by_server={
                server: config.mcp_tool_allowlists[server]
            },
        )
    return [tool.name for tool in tools]


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize a Wellphone MCP server")
    parser.add_argument("server", help="Configured MCP server name")
    args = parser.parse_args()
    tools = asyncio.run(login(load_config(), args.server.lower()))
    print(f"Authorized {args.server}: {', '.join(tools) or 'no allowed tools'}")


if __name__ == "__main__":
    main()

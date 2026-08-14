import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from geas.integrations.mcp import MCPServerConfig
from geas.integrations.mcp_oauth import MCPOAuthConfig


ENV_PATH = Path(__file__).with_name(".env")


@dataclass(frozen=True)
class WellphoneConfig:
    provider: str
    model: str
    memory_provider: str
    memory_model: str
    host: str
    port: int
    tool_timeout: float
    mcp_servers: dict[str, MCPServerConfig]
    mcp_tool_allowlists: dict[str, frozenset[str] | None]
    mcp_approval_tools: dict[str, frozenset[str]]


def load_config() -> WellphoneConfig:
    if ENV_PATH.exists():
        ENV_PATH.chmod(0o600)
    load_dotenv(ENV_PATH)

    provider = os.getenv("WELLPHONE_PROVIDER", "zai").strip()
    model = os.getenv("WELLPHONE_MODEL", "glm-5.2").strip()
    configured_memory_provider = os.getenv("WELLPHONE_MEMORY_PROVIDER")
    configured_memory_model = os.getenv("WELLPHONE_MEMORY_MODEL")
    if (configured_memory_provider is None) != (
        configured_memory_model is None
    ):
        raise ValueError(
            "WELLPHONE_MEMORY_PROVIDER and WELLPHONE_MEMORY_MODEL "
            "must be used together"
        )
    memory_provider = (configured_memory_provider or provider).strip()
    memory_model = (configured_memory_model or model).strip()
    host = os.getenv("WELLPHONE_HOST", "0.0.0.0").strip()
    port = _integer("WELLPHONE_PORT", 8000)
    tool_timeout = _positive_float("WELLPHONE_TOOL_TIMEOUT", 180.0)

    if not all((provider, model, memory_provider, memory_model, host)):
        raise ValueError("model providers, models, and host cannot be empty")
    if not 1 <= port <= 65_535:
        raise ValueError("WELLPHONE_PORT must be between 1 and 65535")

    mcp_servers = _load_mcp_servers()
    allowlists = _load_mcp_tool_allowlists(mcp_servers)
    return WellphoneConfig(
        provider=provider,
        model=model,
        memory_provider=memory_provider,
        memory_model=memory_model,
        host=host,
        port=port,
        tool_timeout=tool_timeout,
        mcp_servers=mcp_servers,
        mcp_tool_allowlists=allowlists,
        mcp_approval_tools=_load_mcp_approval_tools(
            mcp_servers,
            allowlists,
        ),
    )


def _load_mcp_servers() -> dict[str, MCPServerConfig]:
    prefix = "WELLPHONE_MCP_"
    suffix = "_URL"
    servers: dict[str, MCPServerConfig] = {}

    for variable, raw_url in os.environ.items():
        if not (
            variable.startswith(prefix)
            and variable.endswith(suffix)
        ):
            continue

        name = variable[len(prefix) : -len(suffix)].lower()
        url = raw_url.strip()
        parsed = urlparse(url)
        if (
            not name
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            raise ValueError(f"Invalid MCP server configuration: {variable}")
        base = f"{prefix}{name.upper()}"
        token = _optional(base + "_TOKEN")
        auth = _optional(base + "_AUTH")
        client_id = _optional(base + "_CLIENT_ID")
        client_secret = _optional(base + "_CLIENT_SECRET")
        oauth: MCPOAuthConfig | None = None
        if auth == "oauth":
            if token is not None:
                raise ValueError(f"{base}_TOKEN cannot be used with OAuth")
            oauth = MCPOAuthConfig(
                client_id=client_id,
                client_secret=client_secret,
            )
        elif auth == "bearer":
            if token is None:
                raise ValueError(f"{base}_TOKEN is required for bearer auth")
        elif auth == "none":
            if token is not None or client_id is not None or client_secret is not None:
                raise ValueError(f"{base}_AUTH=none cannot include credentials")
        elif auth is not None:
            raise ValueError(f"{base}_AUTH must be none, bearer, or oauth")
        elif client_id is not None or client_secret is not None:
            raise ValueError(f"{base}_AUTH=oauth is required for OAuth credentials")
        servers[name] = MCPServerConfig(url=url, token=token, oauth=oauth)

    return servers


def _load_mcp_tool_allowlists(
    servers: dict[str, MCPServerConfig],
) -> dict[str, frozenset[str] | None]:
    allowlists: dict[str, frozenset[str] | None] = {}
    for server in servers:
        variable = f"WELLPHONE_MCP_{server.upper()}_TOOLS"
        raw = os.getenv(variable)
        if raw is None:
            raise ValueError(f"{variable} is required for configured MCP servers")
        value = raw.strip()
        if value == "*":
            allowlists[server] = None
            continue
        tools = frozenset(item.strip() for item in value.split(",") if item.strip())
        if not tools or "*" in tools:
            raise ValueError(
                f"{variable} must be a comma-separated tool list or exactly *"
            )
        allowlists[server] = tools
    return allowlists


def _load_mcp_approval_tools(
    servers: dict[str, MCPServerConfig],
    allowlists: dict[str, frozenset[str] | None],
) -> dict[str, frozenset[str]]:
    approvals: dict[str, frozenset[str]] = {}
    for server in servers:
        variable = f"WELLPHONE_MCP_{server.upper()}_APPROVAL_TOOLS"
        raw = os.getenv(variable, "")
        tools = frozenset(item.strip() for item in raw.split(",") if item.strip())
        if "*" in tools:
            raise ValueError(f"{variable} must list tools explicitly")
        allowed = allowlists[server]
        if allowed is not None and not tools <= allowed:
            raise ValueError(f"{variable} must be a subset of the tool allowlist")
        approvals[server] = tools
    return approvals


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _integer(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv

from geas.mcp import MCPServerConfig


type AgentPhaseName = Literal["PLAN", "REVIEW"]
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model: str


def load_project_env() -> None:
    load_dotenv(ENV_PATH)


def load_model_selection(phase: AgentPhaseName) -> ModelSelection:
    provider_name = f"GEAS_{phase}_PROVIDER"
    model_name = f"GEAS_{phase}_MODEL"
    provider = os.getenv(provider_name)
    model = os.getenv(model_name)

    if not provider or not model:
        raise ValueError(
            f"{phase} model is not configured: "
            f"set {provider_name} and {model_name}"
        )

    return ModelSelection(provider=provider, model=model)


def load_mcp_servers() -> dict[str, MCPServerConfig]:
    prefix = "GEAS_MCP_"
    suffix = "_URL"
    servers: dict[str, MCPServerConfig] = {}

    for variable, url in os.environ.items():
        if not (
            variable.startswith(prefix)
            and variable.endswith(suffix)
            and url
        ):
            continue

        name = variable[len(prefix) : -len(suffix)].lower()
        parsed = urlparse(url)
        if (
            not name
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
        ):
            raise ValueError(f"Invalid MCP server configuration: {variable}")
        token = os.getenv(f"{prefix}{name.upper()}_TOKEN")
        servers[name] = MCPServerConfig(url=url, token=token or None)

    return servers

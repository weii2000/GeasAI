import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


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

    return WellphoneConfig(
        provider=provider,
        model=model,
        memory_provider=memory_provider,
        memory_model=memory_model,
        host=host,
        port=port,
        tool_timeout=tool_timeout,
    )


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

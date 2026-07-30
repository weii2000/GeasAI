import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


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

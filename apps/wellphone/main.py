import argparse

import uvicorn

from geas.ai.providers import builtin_models

from .config import WellphoneConfig, load_config
from .observability import configure_logging
from .server import create_app
from .service import WellphoneService


def main() -> None:
    configure_logging()
    config = load_config()
    args = _parse_args(config)
    models = builtin_models()
    model = models.get_model(config.provider, config.model)
    if model is None:
        available = ", ".join(
            f"{item.provider}/{item.id}" for item in models.get_models()
        )
        raise ValueError(
            f"unknown Wellphone model {config.provider}/{config.model}; "
            f"available: {available}"
        )
    memory_model = models.get_model(
        config.memory_provider,
        config.memory_model,
    )
    if memory_model is None:
        raise ValueError(
            "unknown Wellphone memory model "
            f"{config.memory_provider}/{config.memory_model}"
        )

    service = WellphoneService(
        model,
        models.stream,
        tool_timeout=args.tool_timeout,
        memory_model=memory_model,
        memory_stream_function=models.stream,
    )
    uvicorn.run(create_app(service), host=args.host, port=args.port)


def _parse_args(config: WellphoneConfig) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wellphone Agent Server")
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument(
        "--tool-timeout",
        type=float,
        default=config.tool_timeout,
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    if args.tool_timeout <= 0:
        parser.error("--tool-timeout must be positive")
    return args


if __name__ == "__main__":
    main()

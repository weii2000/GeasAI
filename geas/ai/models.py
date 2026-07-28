from collections.abc import Iterable
from typing import Protocol

from .event_stream import AssistantMessageEventStream
from .types import AssistantMessage, Context, Model, StreamOptions


class StreamFunction(Protocol):
    def __call__(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream: ...


class Models:
    def __init__(self) -> None:
        self._models: dict[tuple[str, str], Model] = {}
        self._apis: dict[str, StreamFunction] = {}

    def register_models(self, models: Iterable[Model]) -> None:
        for model in models:
            self._models[(model.provider, model.id)] = model

    def register_api(
        self,
        api: str,
        stream_function: StreamFunction,
    ) -> None:
        self._apis[api] = stream_function

    def get_providers(self) -> list[str]:
        return list(dict.fromkeys(
            model.provider for model in self._models.values()
        ))

    def get_models(self, provider_id: str | None = None) -> list[Model]:
        return [
            model
            for model in self._models.values()
            if provider_id is None or model.provider == provider_id
        ]

    def get_model(self, provider_id: str, model_id: str) -> Model | None:
        return self._models.get((provider_id, model_id))

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream_function = self._apis.get(model.api)

        if stream_function is None:
            raise ValueError(f"Unknown API: {model.api}")

        return stream_function(model, context, options)

    async def complete(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessage:
        stream = self.stream(model, context, options)
        return await stream.result()

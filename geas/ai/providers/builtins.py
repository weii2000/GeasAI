from ..apis import stream_openai_completions
from ..model_registry import ModelRegistry
from .deepseek import DEEPSEEK_MODELS
from .glm import GLM_MODELS
from .kimi import KIMI_MODELS
from .qwen import QWEN_MODELS


def builtin_models() -> ModelRegistry:
    models = ModelRegistry()
    for catalog in (
        DEEPSEEK_MODELS,
        QWEN_MODELS,
        KIMI_MODELS,
        GLM_MODELS,
    ):
        models.register_models(catalog)
    models.register_api("openai-completions", stream_openai_completions)
    return models

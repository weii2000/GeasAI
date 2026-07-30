from ..models import Models
from ..openai_completions import stream_openai_completions
from .deepseek import DEEPSEEK_MODELS
from .glm import GLM_MODELS
from .kimi import KIMI_MODELS
from .qwen import QWEN_MODELS


def builtin_models() -> Models:
    models = Models()
    for catalog in (
        DEEPSEEK_MODELS,
        QWEN_MODELS,
        KIMI_MODELS,
        GLM_MODELS,
    ):
        models.register_models(catalog)
    models.register_api("openai-completions", stream_openai_completions)
    return models

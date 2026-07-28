from .types import Model, ModelCost


DEEPSEEK_MODELS = [
    Model(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash",
        api="openai-completions",
        provider="deepseek",
        base_url="https://api.deepseek.com",
        reasoning=True,
        input=["text"],
        cost=ModelCost(
            input=0.14,
            output=0.28,
            cache_read=0.0028,
            cache_write=0,
        ),
        context_window=1_048_576,
        max_tokens=393_216,
    ),
    Model(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        api="openai-completions",
        provider="deepseek",
        base_url="https://api.deepseek.com",
        reasoning=True,
        input=["text"],
        cost=ModelCost(
            input=0.435,
            output=0.87,
            cache_read=0.003625,
            cache_write=0,
        ),
        context_window=1_048_576,
        max_tokens=393_216,
    ),
]

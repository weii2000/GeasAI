from .types import Model, ModelCost


GLM_MODELS = [
    Model(
        id="glm-5.2",
        name="GLM 5.2",
        api="openai-completions",
        provider="zai",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        reasoning=True,
        input=["text"],
        cost=ModelCost(
            input=1.4,
            output=4.4,
            cache_read=0.26,
            cache_write=0,
        ),
        context_window=1_000_000,
        max_tokens=131_072,
    ),
]

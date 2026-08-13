from ..types import Model, ModelCost


QWEN_MODELS = [
    Model(
        id="qwen3.7-plus",
        name="Qwen 3.7 Plus",
        api="openai-completions",
        provider="dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        reasoning=True,
        input=["text"],
        cost=ModelCost(
            input=2,
            output=8,
            cache_read=0.4,
            cache_write=0,
        ),
        context_window=1_000_000,
        max_tokens=65_536,
    ),
]

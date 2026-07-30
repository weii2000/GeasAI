from ..types import Model, ModelCost


KIMI_MODELS = [
    Model(
        id="kimi-k3",
        name="Kimi K3",
        api="openai-completions",
        provider="moonshot",
        base_url="https://api.moonshot.cn/v1",
        reasoning=True,
        input=["text"],
        cost=ModelCost(
            input=2.8,
            output=14,
            cache_read=0.28,
            cache_write=0,
        ),
        context_window=1_048_576,
        max_tokens=1_048_576,
        compat={
            "requires_reasoning_content_on_assistant_messages": True,
        },
    ),
]

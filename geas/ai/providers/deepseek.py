from datetime import UTC, datetime
from functools import partial

from ..types import Model, ModelCost


_OFF_PEAK_PRICES = {
    "deepseek-v4-flash": (1.5, 4.5, 0.05),
    "deepseek-v4-pro": (4.5, 13.5, 0.15),
}
_PEAK_PRICES = {
    "deepseek-v4-flash": (3.0, 9.0, 0.10),
    "deepseek-v4-pro": (9.0, 27.0, 0.30),
}


def _deepseek_cost(model_id: str, at: datetime | None = None) -> ModelCost:
    at = at or datetime.now(UTC)
    if 1 <= at.hour < 4 or 6 <= at.hour < 10:
        prices = _PEAK_PRICES
    else:
        prices = _OFF_PEAK_PRICES
    input_price, output_price, cache_read_price = prices[model_id]
    return ModelCost(
        input=input_price,
        output=output_price,
        cache_read=cache_read_price,
        cache_write=0,
    )


DEEPSEEK_MODELS = [
    Model(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash",
        api="openai-completions",
        provider="deepseek",
        base_url="https://api.deepseek.com",
        reasoning=True,
        input=["text"],
        cost=_deepseek_cost("deepseek-v4-flash"),
        context_window=1_048_576,
        max_tokens=393_216,
        cost_resolver=partial(_deepseek_cost, "deepseek-v4-flash"),
    ),
    Model(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        api="openai-completions",
        provider="deepseek",
        base_url="https://api.deepseek.com",
        reasoning=True,
        input=["text"],
        cost=_deepseek_cost("deepseek-v4-pro"),
        context_window=1_048_576,
        max_tokens=393_216,
        cost_resolver=partial(_deepseek_cost, "deepseek-v4-pro"),
    ),
]

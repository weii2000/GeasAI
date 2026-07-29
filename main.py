import asyncio
import json

from prompt_toolkit import PromptSession

from geas.ai.deepseek_models import DEEPSEEK_MODELS
from geas.ai.glm_models import GLM_MODELS
from geas.ai.kimi_models import KIMI_MODELS
from geas.ai.models import Models
from geas.ai.openai_completions import stream_openai_completions
from geas.ai.qwen_models import QWEN_MODELS
from geas.ai.types import TextDeltaEvent
from geas.core.agent import Agent
from geas.core.types import (
    AgentEvent,
    AgentState,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from geas.plan_agent.session import PlanSession


async def main() -> None:
    models = Models()
    models.register_models(DEEPSEEK_MODELS)
    models.register_models(QWEN_MODELS)
    models.register_models(KIMI_MODELS)
    models.register_models(GLM_MODELS)
    models.register_api("openai-completions", stream_openai_completions)

    model = models.get_model("deepseek", "deepseek-v4-flash")
    if model is None:
        raise RuntimeError("DeepSeek model not found")

    plan_agent = Agent(
        state=AgentState(model=model),
        stream_function=models.stream,
    )
    review_agent = Agent(
        state=AgentState(model=model),
        stream_function=models.stream,
    )
    session = PlanSession(plan_agent, review_agent)

    def print_stream(event: AgentEvent) -> None:
        if isinstance(event, MessageUpdateEvent):
            assistant_event = event.assistant_message_event

            if isinstance(assistant_event, TextDeltaEvent):
                print(assistant_event.delta, end="", flush=True)
        elif isinstance(event, ToolExecutionStartEvent):
            arguments = json.dumps(event.args, ensure_ascii=False)
            print(f"\n[tool] {event.tool_name} {arguments}")
        elif isinstance(event, ToolExecutionEndEvent):
            status = "error" if event.is_error else "ok"
            print(f"[tool:{status}] {event.tool_name}")

    plan_agent.subscribe(print_stream)
    review_agent.subscribe(print_stream)
    console = PromptSession[str]()
    print("Geas Plan Agent（输入 /quit 退出）")

    while True:
        try:
            text = (
                await console.prompt_async("\nYou> ")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if text == "/quit":
            break
        if not text:
            continue

        print("Geas> ", end="", flush=True)
        try:
            await session.prompt(text)
        except Exception as error:
            print(f"\n[error] {error}")
        else:
            print(f"\n[phase: {session.phase}]")


if __name__ == "__main__":
    asyncio.run(main())

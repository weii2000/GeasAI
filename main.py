import asyncio
import json

from prompt_toolkit import PromptSession

from geas.ai.providers import builtin_models
from geas.ai.types import TextDeltaEvent
from geas.config import load_model_selection, load_project_env
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
    load_project_env()
    models = builtin_models()

    plan_selection = load_model_selection("PLAN")
    review_selection = load_model_selection("REVIEW")
    plan_model = models.get_model(
        plan_selection.provider,
        plan_selection.model,
    )
    review_model = models.get_model(
        review_selection.provider,
        review_selection.model,
    )
    if plan_model is None:
        raise ValueError(
            "Unknown PLAN model: "
            f"{plan_selection.provider}/{plan_selection.model}"
        )
    if review_model is None:
        raise ValueError(
            "Unknown REVIEW model: "
            f"{review_selection.provider}/{review_selection.model}"
        )

    plan_agent = Agent(
        state=AgentState(model=plan_model),
        stream_function=models.stream,
    )
    review_agent = Agent(
        state=AgentState(model=review_model),
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

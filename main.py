import asyncio

from geas.ai.deepseek_models import DEEPSEEK_MODELS
from geas.ai.models import Models
from geas.ai.openai_completions import stream_openai_completions
from geas.ai.types import AssistantMessage, TextDeltaEvent
from geas.core.agent import Agent
from geas.core.types import AgentEvent, AgentState, MessageUpdateEvent


async def main() -> None:
    models = Models()
    models.register_models(DEEPSEEK_MODELS)
    models.register_api("openai-completions", stream_openai_completions)

    model = models.get_model("deepseek", "deepseek-v4-flash")
    if model is None:
        raise RuntimeError("DeepSeek model not found")

    agent = Agent(
        state=AgentState(model=model),
        stream_function=models.stream,
    )

    def print_stream(event: AgentEvent) -> None:
        if isinstance(event, MessageUpdateEvent):
            assistant_event = event.assistant_message_event

            if isinstance(assistant_event, TextDeltaEvent):
                print(assistant_event.delta, end="", flush=True)

    agent.subscribe(print_stream)
    await agent.prompt("你好，请用一句话介绍自己。")
    last_message = agent.state.messages[-1]

    if isinstance(last_message, AssistantMessage):
        print(f"\n\nstop_reason={last_message.stop_reason}")


if __name__ == "__main__":
    asyncio.run(main())

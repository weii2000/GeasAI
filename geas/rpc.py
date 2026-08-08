import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from geas.ai.model_registry import ModelRegistry
from geas.ai.providers import builtin_models
from geas.ai.types import (
    AssistantMessage,
    TextContent,
    TextDeltaEvent,
)
from geas.config import (
    AgentPhaseName,
    ModelSelection,
    load_mcp_servers,
    load_model_selection,
    load_project_env,
    save_api_key,
    save_model_selection,
)
from geas.core.agent import Agent
from geas.core.types import (
    AgentRunEvent,
    AgentState,
    AgentTool,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from geas.mcp import MCPRegistry, create_mcp_call_tool
from geas.plan_agent.profiles import load_skill_profiles
from geas.plan_agent.session import (
    PLAN_AGENT_MAX_TURNS,
    REVIEW_AGENT_MAX_TURNS,
    PlanSession,
)
from geas.plan_agent.session_manager import SessionManager
from geas.plan_agent.types import Phase, Plan
from geas.actions.publish_plan import (
    PLANWISE_SERVER_NAME,
    publish_plan,
)
from geas.actions.planwise_auth import PlanWiseAuth, login_planwise


_PLAN = TypeAdapter(Plan)


class RPCServer:
    def __init__(
        self,
        models: ModelRegistry,
        mcp_registry: MCPRegistry,
        skills_root: Path,
    ) -> None:
        self.models = models
        self.mcp_registry = mcp_registry
        agent_servers = [
            server
            for server in mcp_registry.servers
            if server != PLANWISE_SERVER_NAME
        ]
        self.extra_tools: list[AgentTool] = (
            [create_mcp_call_tool(mcp_registry, agent_servers)]
            if agent_servers
            else []
        )
        self.planwise_enabled = (
            PLANWISE_SERVER_NAME in mcp_registry.servers
        )
        self.planwise_auth: PlanWiseAuth | None = None
        self.skills_root = skills_root
        self.manager = SessionManager.create()
        self.session: PlanSession | None = None
        self.running = True
        self._unsubscribers: list[Callable[[], None]] = []
        self._try_create_session()

    async def dispatch(
        self,
        method: str,
        params: dict[str, object],
    ) -> object:
        if method == "initialize":
            return {
                "state": self.state(),
                "models": [
                    {
                        "provider": model.provider,
                        "id": model.id,
                        "name": model.name,
                    }
                    for model in self.models.get_models()
                ],
                "providers": self.models.get_providers(),
            }
        if method == "prompt":
            session = self._require_session()
            text = _require_str(params, "text")
            if not text.strip():
                raise ValueError("Prompt cannot be empty")
            try:
                await session.prompt(text)
            finally:
                self.manager.save(session)
            return self.state()
        if method == "new_session":
            self._new_session()
            return self.state()
        if method == "list_sessions":
            return self.list_sessions()
        if method == "resume_session":
            manager = SessionManager.open(
                _require_str(params, "session_id")
            )
            self.manager = manager
            self._bind_session(
                manager.load(
                    self.models,
                    self.skills_root,
                    self.extra_tools,
                )
            )
            return self.state()
        if method == "set_model":
            phase = _require_phase(params)
            provider = _require_str(params, "provider")
            model_id = _require_str(params, "model")
            model = self.models.get_model(provider, model_id)
            if model is None:
                raise ValueError(f"Unknown model: {provider}/{model_id}")
            save_model_selection(
                phase,
                ModelSelection(provider=provider, model=model_id),
            )
            if self.session is None:
                self._try_create_session()
            else:
                agent = (
                    self.session.plan_agent
                    if phase == "PLAN"
                    else self.session.review_agent
                )
                agent.state.model = model
                self.manager.save(self.session)
            return self.state()
        if method == "set_api_key":
            save_api_key(
                _require_str(params, "provider"),
                _require_str(params, "api_key"),
            )
            return None
        if method == "login_planwise":
            try:
                config = self.mcp_registry.servers[PLANWISE_SERVER_NAME]
            except KeyError as error:
                raise ValueError(
                    "PlanWise MCP is not configured"
                ) from error
            self.planwise_auth = await login_planwise(
                config.url,
                _require_str(params, "username"),
                _require_str(params, "password"),
            )
            self.mcp_registry.set_token(
                PLANWISE_SERVER_NAME,
                self.planwise_auth.access_token,
            )
            return None
        if method == "shutdown":
            self.running = False
            return None
        raise ValueError(f"Unknown method: {method}")

    def state(self) -> dict[str, object]:
        session = self.session
        if session is None:
            return {
                "session_id": self.manager.session_id,
                "cwd": str(self.manager.cwd),
                "phase": None,
                "conversation": [],
                "plan": None,
                "plan_model": self._configured_model("PLAN"),
                "review_model": self._configured_model("REVIEW"),
                "usage": {"tokens": 0, "cost": 0.0},
            }

        tokens = 0
        cost = 0.0
        for agent in (session.plan_agent, session.review_agent):
            for message in agent.state.messages:
                if isinstance(message, AssistantMessage):
                    tokens += message.usage.total_tokens
                    cost += message.usage.cost.total
        return {
            "session_id": self.manager.session_id,
            "cwd": str(self.manager.cwd),
            "phase": session.phase.name,
            "conversation": [
                {
                    "role": message.role,
                    "content": message.content,
                    "phase": message.phase.name,
                }
                for message in session.conversation
            ],
            "plan": _PLAN.dump_python(session.plan, mode="json"),
            "plan_model": _model_data(session.plan_agent),
            "review_model": _model_data(session.review_agent),
            "usage": {"tokens": tokens, "cost": cost},
        }

    def list_sessions(self) -> list[dict[str, object]]:
        return [
            {
                "id": manager.session_id,
                "updated_at": datetime.fromtimestamp(
                    manager.session_file.stat().st_mtime,
                    UTC,
                ).isoformat(),
            }
            for manager in SessionManager.list_saved()
        ]

    def close(self) -> None:
        self._unsubscribe()

    def _new_session(self) -> None:
        self.manager = SessionManager.create()
        self._unsubscribe()
        self.session = None
        self._try_create_session()

    def _try_create_session(self) -> None:
        try:
            plan_selection = load_model_selection("PLAN")
            review_selection = load_model_selection("REVIEW")
        except ValueError:
            return

        plan_model = self.models.get_model(
            plan_selection.provider,
            plan_selection.model,
        )
        review_model = self.models.get_model(
            review_selection.provider,
            review_selection.model,
        )
        if plan_model is None or review_model is None:
            return

        skill_registry, base_profile, profiles = load_skill_profiles(
            self.skills_root
        )
        self._bind_session(
            PlanSession(
                Agent(
                    state=AgentState(model=plan_model),
                    stream_function=self.models.stream,
                    max_turns=PLAN_AGENT_MAX_TURNS,
                ),
                Agent(
                    state=AgentState(model=review_model),
                    stream_function=self.models.stream,
                    max_turns=REVIEW_AGENT_MAX_TURNS,
                ),
                skill_registry,
                base_profile,
                profiles,
                self.extra_tools,
            )
        )

    def _bind_session(self, session: PlanSession) -> None:
        self._unsubscribe()
        self.session = session
        session.on_plan_approved = (
            self._publish_plan
            if self.planwise_enabled
            else None
        )
        self._unsubscribers = [
            session.plan_agent.subscribe(
                lambda event: self._emit_agent_event(event, "PLAN")
            ),
            session.review_agent.subscribe(
                lambda event: self._emit_agent_event(event, "REVIEW")
            ),
        ]

    async def _publish_plan(self, plan: Plan) -> None:
        if self.planwise_auth is not None:
            self.mcp_registry.set_token(
                PLANWISE_SERVER_NAME,
                await self.planwise_auth.get_access_token(),
            )
        publication = await publish_plan(
            self.mcp_registry,
            self.manager.session_id,
            plan,
        )
        _send({
            "type": "event",
            "event": "plan_published",
            "plan_id": publication.plan_id,
            "plan_title": publication.plan_title,
            "created_task_count": publication.created_task_count,
        })

    def _unsubscribe(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _require_session(self) -> PlanSession:
        if self.session is None:
            raise ValueError("PLAN and REVIEW models must be configured first")
        if self.session.phase is Phase.IDLE:
            raise ValueError("This planning session is complete")
        return self.session

    def _configured_model(
        self,
        phase: AgentPhaseName,
    ) -> dict[str, str] | None:
        try:
            selection = load_model_selection(phase)
        except ValueError:
            return None
        return asdict(selection)

    def _emit_agent_event(
        self,
        event: AgentRunEvent,
        phase: AgentPhaseName,
    ) -> None:
        if (
            isinstance(event, MessageStartEvent)
            and isinstance(event.message, AssistantMessage)
        ):
            _send({
                "type": "event",
                "event": "assistant_start",
                "phase": phase,
            })
        elif isinstance(event, MessageUpdateEvent):
            update = event.assistant_response_event
            if isinstance(update, TextDeltaEvent):
                _send({
                    "type": "event",
                    "event": "text_delta",
                    "phase": phase,
                    "delta": update.delta,
                })
        elif isinstance(event, ToolExecutionStartEvent):
            _send({
                "type": "event",
                "event": "tool_start",
                "phase": phase,
                "tool_call_id": event.tool_call_id,
                "name": event.tool_name,
                "args": event.args,
            })
        elif isinstance(event, ToolExecutionEndEvent):
            content = "\n".join(
                block.text
                for block in event.result.content
                if isinstance(block, TextContent)
            )
            _send({
                "type": "event",
                "event": "tool_end",
                "phase": phase,
                "tool_call_id": event.tool_call_id,
                "name": event.tool_name,
                "is_error": event.is_error,
                "content": content,
            })


def _model_data(agent: Agent) -> dict[str, str]:
    model = agent.state.model
    return {"provider": model.provider, "model": model.id}


def _require_str(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_phase(params: dict[str, object]) -> AgentPhaseName:
    phase = _require_str(params, "phase")
    if phase not in {"PLAN", "REVIEW"}:
        raise ValueError("phase must be PLAN or REVIEW")
    return cast(AgentPhaseName, phase)


def _send(message: object) -> None:
    print(
        json.dumps(message, ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


async def _serve(server: RPCServer) -> None:
    while server.running:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break

        request_id: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(method, str):
                raise ValueError("method must be a string")
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = await server.dispatch(method, params)
        except Exception as error:
            _send({
                "type": "response",
                "id": request_id,
                "ok": False,
                "error": str(error),
            })
        else:
            _send({
                "type": "response",
                "id": request_id,
                "ok": True,
                "result": result,
            })


async def main() -> None:
    load_project_env()
    models = builtin_models()
    servers = load_mcp_servers()
    async with MCPRegistry(servers) as registry:
        server = RPCServer(models, registry, Path.cwd() / "skills")
        try:
            await _serve(server)
        finally:
            server.close()


if __name__ == "__main__":
    asyncio.run(main())

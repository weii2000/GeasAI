import json
from dataclasses import asdict
from datetime import datetime

from geas.core.agent import Agent
from geas.core.types import AgentContext, AgentTool, TurnEndEvent

from .profiles import BASE_PROFILE, PHASE_PROFILES
from .tools import create_plan_agent_tools
from .types import IssueSeverity, Phase, Plan, ReviewReport


class PlanSession:
    def __init__(
        self,
        plan_agent: Agent,
        review_agent: Agent,
    ) -> None:
        if plan_agent is review_agent:
            raise ValueError("Plan and review agents must be different")

        self.plan_agent = plan_agent
        self.review_agent = review_agent
        self.plan = Plan()
        self.review_report: ReviewReport | None = None
        self.phase = Phase.PLAN

        self._tools = {
            tool.name: tool
            for tool in create_plan_agent_tools(self)
        }
        self.base_profile = BASE_PROFILE
        self.profiles = PHASE_PROFILES
        self.plan_agent.prepare_next_turn = (
            self._prepare_plan_next_turn
        )
        self.review_agent.prepare_next_turn = (
            self._prepare_review_next_turn
        )
        self.plan_agent.should_stop_after_turn = (
            self._stop_plan_after_turn
        )
        self.review_agent.should_stop_after_turn = (
            self._stop_review_after_turn
        )
        self._sync_agent(self.plan_agent, Phase.PLAN)
        self._sync_agent(self.review_agent, Phase.REVIEW)

    def update_plan(self, plan: Plan) -> None:
        self.plan = plan

    def update_review_report(self, report: ReviewReport) -> None:
        self.review_report = report

    def submit_plan(self) -> None:
        self.review_report = None
        self.phase = Phase.REVIEW

    def request_change(self) -> None:
        self.phase = Phase.PLAN

    def approve_plan(self) -> None:
        if self.review_report is None:
            raise ValueError("Plan has not been reviewed")

        if any(
            issue.severity is IssueSeverity.BLOCKING
            for issue in self.review_report.issues
        ):
            raise ValueError("Plan has blocking review issues")

        self.phase = Phase.IDLE

    def tools_for(self, phase: Phase) -> list[AgentTool]:
        tool_names = (
            *self.base_profile.tools,
            *self.profiles[phase].tools,
        )
        return [
            self._tools[name]
            for name in tool_names
        ]

    def build_system_prompt(self, profile_phase: Phase) -> str:
        profile = self.profiles[profile_phase]
        skills = [*self.base_profile.skills, *profile.skills]
        sections = [
            self.base_profile.prompt,
            profile.prompt,
            (
                "Current local time: "
                + datetime.now().astimezone().isoformat(
                    timespec="seconds",
                )
            ),
        ]

        if skills:
            sections.append(
                "Available skills:\n"
                + "\n".join(f"- {skill}" for skill in skills)
            )

        sections.append(
            "Current session state:\n"
            + json.dumps(
                {
                    "phase": self.phase,
                    "plan": asdict(self.plan),
                    "review_report": (
                        asdict(self.review_report)
                        if self.review_report is not None
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return "\n\n".join(section for section in sections if section)

    async def _prepare_plan_next_turn(
        self,
        context: AgentContext,
    ) -> AgentContext:
        return self._prepare_next_turn(
            self.plan_agent,
            Phase.PLAN,
            context,
        )

    async def _prepare_review_next_turn(
        self,
        context: AgentContext,
    ) -> AgentContext:
        return self._prepare_next_turn(
            self.review_agent,
            Phase.REVIEW,
            context,
        )

    def _prepare_next_turn(
        self,
        agent: Agent,
        phase: Phase,
        context: AgentContext,
    ) -> AgentContext:
        self._sync_agent(agent, phase)
        return AgentContext(
            messages=[*context.messages],
            system_prompt=agent.state.system_prompt,
            tools=[*agent.state.tools],
        )

    async def _stop_plan_after_turn(
        self,
        _event: TurnEndEvent,
    ) -> bool:
        return self.phase is not Phase.PLAN

    async def _stop_review_after_turn(
        self,
        _event: TurnEndEvent,
    ) -> bool:
        return self.phase is not Phase.REVIEW

    async def prompt(self, text: str) -> None:
        next_prompt = text

        while self.phase is not Phase.IDLE:
            starting_phase = self.phase
            agent = (
                self.plan_agent
                if starting_phase is Phase.PLAN
                else self.review_agent
            )
            self._sync_agent(agent, starting_phase)
            await agent.prompt(next_prompt)

            if self.phase is starting_phase or self.phase is Phase.IDLE:
                break

            next_prompt = "请根据当前阶段和 Current session state 继续。"

    def _sync_agent(self, agent: Agent, phase: Phase) -> None:
        agent.state.system_prompt = self.build_system_prompt(phase)
        agent.state.tools = self.tools_for(phase)

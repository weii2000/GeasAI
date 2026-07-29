import json
from dataclasses import asdict
from datetime import datetime

from geas.core.agent import Agent
from geas.core.types import AgentContext, AgentTool

from .profiles import BASE_PROFILE, PHASE_PROFILES
from .tools import create_plan_agent_tools
from .types import IssueSeverity, Phase, Plan, ReviewReport


class PlanSession:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.plan = Plan()
        self.review_report: ReviewReport | None = None
        self.phase = Phase.PLAN

        self._tools = {
            tool.name: tool
            for tool in create_plan_agent_tools(self)
        }
        self.base_profile = BASE_PROFILE
        self.profiles = PHASE_PROFILES
        self.agent.prepare_next_turn = self.prepare_next_turn

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

    @property
    def active_tools(self) -> list[AgentTool]:
        tool_names = (
            *self.base_profile.tools,
            *self.profiles[self.phase].tools,
        )
        return [
            self._tools[name]
            for name in tool_names
        ]

    def build_system_prompt(self) -> str:
        profile = self.profiles[self.phase]
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

    async def prepare_next_turn(
        self,
        context: AgentContext,
    ) -> AgentContext:
        self._sync_agent_state()
        return AgentContext(
            messages=[*context.messages],
            system_prompt=self.agent.state.system_prompt,
            tools=[*self.agent.state.tools],
        )

    async def prompt(self, text: str) -> None:
        self._sync_agent_state()
        await self.agent.prompt(text)

    def _sync_agent_state(self) -> None:
        self.agent.state.system_prompt = self.build_system_prompt()
        self.agent.state.tools = self.active_tools

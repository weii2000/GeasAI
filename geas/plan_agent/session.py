import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime

from geas.ai.types import AssistantMessage, TextContent
from geas.core.agent import Agent
from geas.core.types import (
    AgentContext,
    AgentEvent,
    AgentTool,
    MessageEndEvent,
    TurnEndEvent,
)

from .profiles import BASE_PROFILE, PHASE_PROFILES, Profile
from .skills import Skill, SkillRegistry, format_skills_for_prompt
from .tools import create_plan_agent_tools
from .types import (
    ConversationMessage,
    IssueSeverity,
    Phase,
    Plan,
    ReviewReport,
)

type OnPlanApproved = Callable[[Plan], Awaitable[None]]

PLAN_AGENT_MAX_TURNS = 20
REVIEW_AGENT_MAX_TURNS = 10


class PlanSession:
    def __init__(
        self,
        plan_agent: Agent,
        review_agent: Agent,
        skill_registry: SkillRegistry | None = None,
        base_profile: Profile = BASE_PROFILE,
        profiles: dict[Phase, Profile] | None = None,
        extra_tools: list[AgentTool] | None = None,
        on_plan_approved: OnPlanApproved | None = None,
    ) -> None:
        if plan_agent is review_agent:
            raise ValueError("Plan and review agents must be different")

        self.plan_agent = plan_agent
        self.review_agent = review_agent
        self.plan = Plan()
        self.review_report: ReviewReport | None = None
        self.phase = Phase.PLAN
        self.conversation: list[ConversationMessage] = []
        self.on_plan_approved = on_plan_approved
        self.skill_registry = skill_registry or SkillRegistry()
        self.base_profile = base_profile
        self.profiles = (
            profiles
            if profiles is not None
            else PHASE_PROFILES
        )

        self._tools = {
            tool.name: tool
            for tool in create_plan_agent_tools(self)
        }
        self._extra_tool_names: list[str] = []
        for tool in extra_tools or []:
            if tool.name in self._tools:
                raise ValueError(f'Duplicate tool: "{tool.name}"')
            self._tools[tool.name] = tool
            self._extra_tool_names.append(tool.name)
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
        self.plan_agent.subscribe(
            lambda event: self._record_assistant_text(
                event,
                Phase.PLAN,
            )
        )
        self.review_agent.subscribe(
            lambda event: self._record_assistant_text(
                event,
                Phase.REVIEW,
            )
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
        tool_names = [
            *self.base_profile.tools,
            *self._extra_tool_names,
            *self.profiles[phase].tools,
        ]
        if self.skills_for(phase):
            tool_names.extend(("read_skill", "bash"))
        return [
            self._tools[name]
            for name in tool_names
        ]

    def skills_for(self, phase: Phase) -> list[Skill]:
        names = dict.fromkeys(
            (*self.base_profile.skills, *self.profiles[phase].skills)
        )
        skills: list[Skill] = []
        for name in names:
            skill = self.skill_registry.get(name)
            if skill is None:
                raise ValueError(f'Unknown skill in profile: "{name}"')
            skills.append(skill)
        return skills

    def build_system_prompt(self, profile_phase: Phase) -> str:
        profile = self.profiles[profile_phase]
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

        skills_prompt = format_skills_for_prompt(
            self.skills_for(profile_phase)
        )
        if skills_prompt:
            sections.append(skills_prompt)

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
                    "conversation": [
                        asdict(message)
                        for message in self.conversation
                        if message.phase is not profile_phase
                    ],
                },
                ensure_ascii=False,
                indent=2,
                default=lambda value: value.isoformat(),
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
        if self.phase is Phase.IDLE:
            return

        self.conversation.append(
            ConversationMessage(
                role="user",
                content=text,
                phase=self.phase,
            )
        )
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

        if self.phase is Phase.IDLE and self.on_plan_approved is not None:
            try:
                await self.on_plan_approved(self.plan)
            except BaseException:
                self.phase = Phase.REVIEW
                raise

    def _record_assistant_text(
        self,
        event: AgentEvent,
        phase: Phase,
    ) -> None:
        if (
            not isinstance(event, MessageEndEvent)
            or not isinstance(event.message, AssistantMessage)
        ):
            return

        text = "\n".join(
            block.text
            for block in event.message.content
            if isinstance(block, TextContent)
        )
        if text:
            self.conversation.append(
                ConversationMessage(
                    role="assistant",
                    content=text,
                    phase=phase,
                )
            )

    def _sync_agent(self, agent: Agent, phase: Phase) -> None:
        agent.state.system_prompt = self.build_system_prompt(phase)
        agent.state.tools = self.tools_for(phase)

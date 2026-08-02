import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from geas.ai.models import Models
from geas.ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    UserMessage,
)
from geas.plan_agent.session_manager import SessionManager
from geas.plan_agent.types import (
    ConversationMessage,
    IssueSeverity,
    Phase,
    Plan,
    ReviewIssue,
    ReviewReport,
    Task,
)

from .helpers import make_assistant, make_session, make_tool_call


def test_plan_session_runs_plan_review_to_idle() -> None:
    session, plan_model, review_model = make_session(
        plan_responses=[
            make_assistant(
                [
                    TextContent(
                        type="text",
                        text="你每天可以投入多少时间？",
                    )
                ],
                "stop",
            ),
            make_assistant(
                [
                    ThinkingContent(
                        type="thinking",
                        thinking="先制定计划",
                    ),
                    make_tool_call(
                        "update_plan",
                        {
                            "title": "发布 Geas",
                            "goal": "发布 Geas",
                            "description": "完成可运行的 Agent",
                            "acceptance_criterion": "可以完成规划和评审",
                            "constraints": ["仅使用 Python"],
                            "tasks": [
                                {
                                    "title": "实现 Agent",
                                    "level": 1,
                                }
                            ],
                        },
                    ),
                    make_tool_call("submit_plan", {}),
                ],
                "toolUse",
            ),
        ],
        review_responses=[
            make_assistant(
                [
                    ThinkingContent(
                        type="thinking",
                        thinking="独立审查计划",
                    ),
                    make_tool_call(
                        "update_review_report",
                        {
                            "summary": "计划可以执行",
                            "issues": [],
                        },
                    ),
                    make_tool_call("approve_plan", {}),
                ],
                "toolUse",
            ),
        ],
    )

    asyncio.run(session.prompt("帮我规划 Geas"))
    assert session.phase is Phase.PLAN
    asyncio.run(session.prompt("每天两个小时"))

    assert session.phase is Phase.PENDING_APPROVAL
    asyncio.run(session.prompt("y"))
    assert session.phase is Phase.IDLE
    assert session.plan.title == "发布 Geas"
    assert session.plan.goal == "发布 Geas"
    assert session.plan.constraints == ["仅使用 Python"]
    assert session.review_report == ReviewReport(
        summary="计划可以执行",
    )
    assert len(plan_model.contexts) == 2
    assert len(review_model.contexts) == 1
    plan_prompt = plan_model.contexts[0].system_prompt
    review_prompt = review_model.contexts[0].system_prompt
    assert plan_prompt is not None
    assert review_prompt is not None
    assert "PLAN 阶段" in plan_prompt
    assert "REVIEW 阶段" in review_prompt
    assert "帮我规划 Geas" in review_prompt
    assert "你每天可以投入多少时间？" in review_prompt
    assert "每天两个小时" in review_prompt
    assert "先制定计划" not in review_prompt
    assert [
        message.content
        for message in session.conversation
    ] == [
        "帮我规划 Geas",
        "你每天可以投入多少时间？",
        "每天两个小时",
    ]
    assert plan_model.models[0].provider == "deepseek"
    assert review_model.models[0].provider == "review-test"
    assert all(
        isinstance(message, UserMessage)
        for message in review_model.contexts[0].messages
    )
    assert any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, ThinkingContent)
            for block in message.content
        )
        for message in session.plan_agent.state.messages
    )
    assert not any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, ThinkingContent)
            and block.thinking == "先制定计划"
            for block in message.content
        )
        for message in session.review_agent.state.messages
    )

    plan_tools = {
        tool.name
        for tool in plan_model.contexts[0].tools or []
    }
    review_tools = {
        tool.name
        for tool in review_model.contexts[0].tools or []
    }
    assert {"web_search", "update_plan", "submit_plan"} == plan_tools
    assert "update_plan" not in review_tools
    assert {
        "web_search",
        "update_review_report",
        "request_change",
        "approve_plan",
    } == review_tools


def test_invalid_tool_arguments_do_not_update_plan() -> None:
    session, plan_model, _review_model = make_session(
        plan_responses=[
            make_assistant(
                [
                    ThinkingContent(
                        type="thinking",
                        thinking="继续在 PLAN 阶段修复参数",
                    ),
                    make_tool_call(
                        "update_plan",
                        {
                            "goal": "缺少必要字段",
                        },
                    )
                ],
                "toolUse",
            ),
            make_assistant(
                [TextContent(type="text", text="参数错误。")],
                "stop",
            ),
        ]
    )

    asyncio.run(session.prompt("创建计划"))

    assert session.plan.goal == ""
    tool_results = [
        message
        for message in plan_model.contexts[1].messages
        if isinstance(message, ToolResultMessage)
    ]
    assert tool_results[-1].is_error
    assert "Invalid arguments" in tool_results[-1].content[0].text
    assert any(
        isinstance(message, AssistantMessage)
        and any(
            isinstance(block, ThinkingContent)
            for block in message.content
        )
        for message in plan_model.contexts[1].messages
    )


def test_agent_stops_after_max_turns() -> None:
    session, _plan_model, _review_model = make_session(
        plan_responses=[
            make_assistant(
                [make_tool_call("read_plan", {})],
                "toolUse",
            ),
        ],
    )
    session.plan_agent.max_turns = 1

    with pytest.raises(RuntimeError, match="exceeded max turns: 1"):
        asyncio.run(session.plan_agent.prompt("继续调用工具"))


def test_blocking_review_cannot_be_approved() -> None:
    session, _plan_model, _review_model = make_session([])
    session.submit_plan()
    session.update_review_report(
        ReviewReport(
            summary="计划缺少关键内容",
            issues=[
                ReviewIssue(
                    description="没有验收标准",
                    evidence="acceptance_criterion 为空",
                    severity=IssueSeverity.BLOCKING,
                ),
            ],
        )
    )

    with pytest.raises(
        ValueError,
        match="blocking review issues",
    ):
        session.approve_plan()

    assert session.phase is Phase.REVIEW


def test_task_tree_rejects_skipped_levels() -> None:
    with pytest.raises(
        ValueError,
        match="one level below",
    ):
        Task(
            title="一级任务",
            level=1,
            subtasks=[
                Task(title="错误的三级任务", level=3),
            ],
        )


def test_task_rejects_invalid_time_range() -> None:
    london = ZoneInfo("Europe/London")
    with pytest.raises(ValueError, match="timezone"):
        Task(
            title="无时区任务",
            level=1,
            start_time=datetime(2026, 8, 1),
        )
    with pytest.raises(ValueError, match="before start time"):
        Task(
            title="倒序任务",
            level=1,
            start_time=datetime(2026, 8, 2, tzinfo=london),
            due_time=datetime(2026, 8, 1, tzinfo=london),
        )


def test_plan_rejects_more_than_100_tasks() -> None:
    with pytest.raises(ValueError, match="more than 100"):
        Plan(
            tasks=[
                Task(title=f"任务 {index}", level=1)
                for index in range(101)
            ]
        )


def test_failed_plan_publication_can_be_retried() -> None:
    approval = make_assistant(
        [
            make_tool_call(
                "update_review_report",
                {"summary": "可以执行", "issues": []},
            ),
            make_tool_call("approve_plan", {}),
        ],
        "toolUse",
    )
    session, _plan_model, _review_model = make_session(
        [],
        review_responses=[approval],
    )
    session.phase = Phase.REVIEW
    session.plan = Plan(
        title="发布 Geas",
        goal="完成 Agent",
        tasks=[Task(title="发布", level=1)],
    )
    attempts: list[Plan] = []

    async def fail(plan: Plan) -> None:
        attempts.append(plan)
        raise RuntimeError("PlanWise unavailable")

    session.on_plan_approved = fail
    asyncio.run(session.prompt("批准计划"))

    assert session.phase is Phase.PENDING_APPROVAL
    assert attempts == []

    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(session.prompt("y"))

    assert session.phase is Phase.PENDING_APPROVAL
    assert attempts == [session.plan]

    async def succeed(plan: Plan) -> None:
        attempts.append(plan)

    session.on_plan_approved = succeed
    asyncio.run(session.prompt("y"))

    assert session.phase is Phase.IDLE
    assert attempts == [session.plan, session.plan]


def test_human_feedback_returns_plan_to_revision() -> None:
    session, _plan_model, review_model = make_session(
        plan_responses=[
            make_assistant(
                [TextContent(type="text", text="我会修改计划。")],
                "stop",
            )
        ],
        review_responses=[
            make_assistant(
                [
                    make_tool_call(
                        "update_review_report",
                        {
                            "summary": "用户要求增加预算限制",
                            "issues": [],
                        },
                    ),
                    make_tool_call("request_change", {}),
                ],
                "toolUse",
            )
        ],
    )
    session.phase = Phase.PENDING_APPROVAL

    feedback = "总预算不能超过 100 元"
    asyncio.run(session.prompt(feedback))

    assert session.phase is Phase.PLAN
    assert session.review_report == ReviewReport(
        summary="用户要求增加预算限制",
    )
    assert ConversationMessage(
        role="user",
        content=feedback,
        phase=Phase.REVIEW,
    ) in session.conversation
    assert isinstance(review_model.contexts[0].messages[-1], UserMessage)
    assert review_model.contexts[0].messages[-1].content == feedback


def test_session_manager_restores_checkpoint(tmp_path) -> None:
    session, plan_model, _review_model = make_session([])
    session.phase = Phase.REVIEW
    session.plan = Plan(
        title="Geas 发布计划",
        goal="发布 Geas",
        constraints=["仅使用 Python"],
        tasks=[
            Task(
                title="实现持久化",
                level=1,
                start_time=datetime.fromisoformat(
                    "2026-08-01T09:00:00+01:00"
                ),
            )
        ],
    )
    session.review_report = ReviewReport(summary="等待评审")
    session.plan_agent.state.messages = [
        UserMessage(
            role="user",
            content="保存这个计划",
            timestamp=1,
        ),
        make_assistant(
            [TextContent(type="text", text="计划已保存")],
            "stop",
        ),
    ]

    models = Models()
    models.register_models([
        session.plan_agent.state.model,
        session.review_agent.state.model,
    ])
    models.register_api(session.plan_agent.state.model.api, plan_model)

    root = tmp_path / "sessions"
    cwd = tmp_path / "project"
    manager = SessionManager.create(cwd, root)
    manager.save(session)

    assert manager.session_file.parent.stat().st_mode & 0o777 == 0o700
    assert manager.session_file.stat().st_mode & 0o777 == 0o600

    restored = SessionManager.open(
        manager.session_id,
        cwd,
        root,
    ).load(models)

    assert restored.phase is Phase.REVIEW
    assert restored.plan == session.plan
    assert restored.review_report == session.review_report
    assert restored.conversation == session.conversation
    assert (
        restored.plan_agent.state.messages
        == session.plan_agent.state.messages
    )
    recent = SessionManager.continue_recent(cwd, root)
    assert recent is not None
    assert recent.session_id == manager.session_id
    assert [
        saved.session_id
        for saved in SessionManager.list_saved(cwd, root)
    ] == [manager.session_id]

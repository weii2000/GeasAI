import asyncio

import pytest

from geas.ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
    UserMessage,
)
from geas.plan_agent.types import (
    IssueSeverity,
    Phase,
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
                    ThinkingContent(
                        type="thinking",
                        thinking="先制定计划",
                    ),
                    make_tool_call(
                        "update_plan",
                        {
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

    assert session.phase is Phase.IDLE
    assert session.plan.goal == "发布 Geas"
    assert session.plan.constraints == ["仅使用 Python"]
    assert session.review_report == ReviewReport(
        summary="计划可以执行",
    )
    assert len(plan_model.contexts) == 1
    assert len(review_model.contexts) == 1
    plan_prompt = plan_model.contexts[0].system_prompt
    review_prompt = review_model.contexts[0].system_prompt
    assert plan_prompt is not None
    assert review_prompt is not None
    assert "PLAN 阶段" in plan_prompt
    assert "REVIEW 阶段" in review_prompt
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

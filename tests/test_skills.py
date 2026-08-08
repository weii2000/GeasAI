import asyncio
import shlex
import sys
from dataclasses import replace

import pytest

from geas.ai.types import TextContent, ToolResultMessage
from geas.plan_agent.profiles import load_skill_profiles
from geas.plan_agent.skills import SkillRegistry
from geas.plan_agent.types import Phase

from .helpers import make_assistant, make_session, make_tool_call


def test_skill_directories_map_to_profiles(tmp_path) -> None:
    for group in ("base", "plan", "review"):
        skill_file = tmp_path / group / f"{group}-skill" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text(
            (
                "---\n"
                f"name: {group}-skill\n"
                f"description: {group} instructions\n"
                "---\n"
            ),
            encoding="utf-8",
        )

    registry, base, profiles = load_skill_profiles(tmp_path)

    assert len(registry.list()) == 3
    assert base.skills == ("base-skill",)
    assert profiles[Phase.PLAN].skills == ("plan-skill",)
    assert profiles[Phase.REVIEW].skills == ("review-skill",)

    session, _, _ = make_session([], skill_registry=registry)
    session.base_profile = base
    session.profiles = profiles
    assert [
        skill.name
        for skill in session.skills_for(Phase.PLAN)
    ] == ["base-skill", "plan-skill"]
    assert [
        skill.name
        for skill in session.skills_for(Phase.REVIEW)
    ] == ["base-skill", "review-skill"]


def test_skill_registry_discovers_external_skill(tmp_path) -> None:
    skill_file = tmp_path / "external-skill" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text(
        """\
---
name: external-skill
description: >
  Handles external planning workflows.
  Use when a task needs the external process.
---

# External Skill

Follow the external workflow.
""",
        encoding="utf-8",
    )
    script_file = skill_file.parent / "scripts" / "hello.py"
    script_file.parent.mkdir()
    script_file.write_text(
        "import sys\nprint(f'hello {sys.argv[1]}')\n",
        encoding="utf-8",
    )
    registry = SkillRegistry()
    [skill] = registry.discover(tmp_path)

    assert skill.name == "external-skill"
    assert "external planning workflows" in skill.description
    assert registry.get(skill.name) == skill
    assert registry.list() == [skill]
    assert "# External Skill" in registry.read(skill.name)

    session, model, _ = make_session(
        [
            make_assistant(
                [make_tool_call("read_skill", {"name": skill.name})],
                "toolUse",
            ),
            make_assistant(
                [
                    make_tool_call(
                        "bash",
                        {
                            "command": (
                                f"{shlex.quote(sys.executable)} "
                                f"{shlex.quote(str(script_file))} Geas"
                            ),
                        },
                    )
                ],
                "toolUse",
            ),
            make_assistant(
                [TextContent(type="text", text="Script completed")],
                "stop",
            ),
        ],
        skill_registry=registry,
    )
    session.base_profile = replace(
        session.base_profile,
        skills=(skill.name,),
    )
    prompt = session.build_system_prompt(Phase.PLAN)

    assert "<name>external-skill</name>" in prompt
    assert skill.description in prompt
    assert f"<location>{skill.path}</location>" in prompt
    assert "# External Skill" not in prompt

    read_skill = next(
        tool
        for tool in session.tools_for(Phase.PLAN)
        if tool.name == "read_skill"
    )
    with pytest.raises(KeyError, match="Unavailable skill"):
        asyncio.run(
            read_skill.execute("call", {"name": "missing-skill"})
        )
    asyncio.run(session.prompt("Use the external workflow"))

    assert {"read_skill", "bash"} <= {
        tool.name
        for tool in model.contexts[0].tools or []
    }
    result = next(
        message
        for message in model.contexts[1].messages
        if isinstance(message, ToolResultMessage)
    )
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert "# External Skill" in content.text

    script_result = next(
        message
        for message in model.contexts[2].messages
        if (
            isinstance(message, ToolResultMessage)
            and message.tool_name == "bash"
        )
    )
    script_content = script_result.content[0]
    assert isinstance(script_content, TextContent)
    assert script_content.text == "hello Geas\n"

import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import yaml


_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def discover(self, path: Path) -> list[Skill]:
        path = path.expanduser()
        if not path.exists():
            raise FileNotFoundError(path)

        files = (
            [path]
            if path.is_file()
            else sorted(path.rglob("SKILL.md"))
        )
        return [self.load(file) for file in files]

    def load(self, path: Path) -> Skill:
        path = path.expanduser().resolve()
        metadata = _read_frontmatter(path)
        name = metadata.get("name")
        description = metadata.get("description")

        if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name):
            raise ValueError(f"Invalid skill name in {path}")
        if len(name) > 64:
            raise ValueError(f"Skill name is too long in {path}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Skill description is required in {path}")
        if len(description) > 1024:
            raise ValueError(f"Skill description is too long in {path}")
        if name in self._skills:
            raise ValueError(f'Duplicate skill name: "{name}"')

        skill = Skill(
            name=name,
            description=description.strip(),
            path=path,
        )
        self._skills[name] = skill
        return skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def read(self, name: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(f'Unknown skill: "{name}"')
        return skill.path.read_text(encoding="utf-8")


def format_skills_for_prompt(skills: list[Skill]) -> str:
    if not skills:
        return ""

    items = "\n".join(
        (
            "  <skill>\n"
            f"    <name>{escape(skill.name)}</name>\n"
            f"    <description>{escape(skill.description)}</description>\n"
            f"    <location>{escape(str(skill.path))}</location>\n"
            "  </skill>"
        )
        for skill in skills
    )
    return (
        "The following skills are available. Use read_skill with a "
        "skill name to load its full instructions when relevant. "
        "Resolve relative file references against the directory "
        "containing its location.\n"
        "<available_skills>\n"
        f"{items}\n"
        "</available_skills>"
    )


def _read_frontmatter(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"Skill file does not exist: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Skill frontmatter is required in {path}")

    try:
        end = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise ValueError(f"Skill frontmatter is not closed in {path}") from error

    metadata = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise ValueError(f"Skill frontmatter must be an object in {path}")
    return metadata

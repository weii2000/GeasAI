from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Literal


class Phase(StrEnum):
    PLAN = auto()
    REVIEW = auto()
    IDLE = auto()


type TaskLevel = Literal[1, 2, 3]


class TaskStatus(StrEnum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()


class IssueSeverity(StrEnum):
    SUGGESTION = auto()
    WARNING = auto()
    BLOCKING = auto()


@dataclass
class Task:
    title: str
    level: TaskLevel
    status: TaskStatus = TaskStatus.PENDING
    subtasks: list[Task] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.level not in (1, 2, 3):
            raise ValueError("Task level must be 1, 2, or 3")

        for subtask in self.subtasks:
            self._validate_subtask(subtask)

    def add_subtask(self, subtask: Task) -> None:
        self._validate_subtask(subtask)
        self.subtasks.append(subtask)

    def _validate_subtask(self, subtask: Task) -> None:
        if subtask.level != self.level + 1:
            raise ValueError("Subtask level must be one level below its parent")


@dataclass
class Plan:
    goal: str = ""
    description: str = ""
    acceptance_criterion: str = ""
    constraints: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    def __post_init__(self) -> None:
        if any(task.level != 1 for task in self.tasks):
            raise ValueError("Top-level tasks must have level 1")


@dataclass
class ReviewIssue:
    description: str
    evidence: str
    severity: IssueSeverity


@dataclass
class ReviewReport:
    summary: str = ""
    issues: list[ReviewIssue] = field(default_factory=list)

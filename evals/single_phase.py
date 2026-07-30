from __future__ import annotations

import argparse
import asyncio
import json
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal, cast

from geas.ai.models import Models
from geas.ai.providers import builtin_models
from geas.ai.types import AssistantMessage, Model, TextContent, Usage
from geas.config import (
    ModelSelection,
    load_model_selection,
    load_project_env,
)
from geas.core.agent import Agent
from geas.core.types import AgentState
from geas.plan_agent.session import PlanSession
from geas.plan_agent.types import (
    IssueSeverity,
    Phase,
    Plan,
    ReviewIssue,
    ReviewReport,
    Task,
    TaskLevel,
)

DEFAULT_SUITE_PATH = Path(__file__).with_name(
    "single_phase_cases.json"
)
DEFAULT_OUTPUT_DIR = Path("eval-results/single-phase")
type EvalTarget = Literal["plan", "review"]


@dataclass(frozen=True)
class Expectation:
    phase: Phase
    require_complete_plan: bool = False
    require_plan_unchanged: bool = False
    min_task_count: int = 0
    max_task_count: int | None = None
    min_top_level_task_count: int = 0
    max_top_level_task_count: int | None = None
    min_question_count: int | None = None
    max_question_count: int | None = None
    require_complete_review_report: bool = False
    min_issue_count: int = 0
    minimum_issue_severity: IssueSeverity | None = None
    required_plan_terms: list[list[str]] = field(default_factory=list)
    required_constraint_terms: list[list[str]] = field(
        default_factory=list
    )
    required_issue_terms: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    description: str
    target: EvalTarget
    prompt: str
    plan: Plan
    review_report: ReviewReport | None
    expected: Expectation


@dataclass(frozen=True)
class EvalSuite:
    name: str
    version: str
    cases: list[EvalCase]


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def load_suite(path: Path = DEFAULT_SUITE_PATH) -> EvalSuite:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [_parse_case(case) for case in payload["cases"]]
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Eval case ids must be unique")
    return EvalSuite(
        name=str(payload["name"]),
        version=str(payload["version"]),
        cases=cases,
    )


def _parse_case(data: dict[str, object]) -> EvalCase:
    target = str(data["target"])
    if target not in ("plan", "review"):
        raise ValueError(f"Unknown eval target: {target}")

    inputs = data["input"]
    expected = data["expected"]
    if not isinstance(inputs, dict) or not isinstance(expected, dict):
        raise TypeError("Eval input and expected must be objects")

    plan_data = inputs.get("plan")
    report_data = inputs.get("review_report")
    plan = _parse_plan(plan_data) if isinstance(plan_data, dict) else Plan()
    report = (
        _parse_review_report(report_data)
        if isinstance(report_data, dict)
        else None
    )
    severity = expected.get("minimum_issue_severity")

    return EvalCase(
        case_id=str(data["case_id"]),
        description=str(data["description"]),
        target=target,
        prompt=str(inputs["prompt"]),
        plan=plan,
        review_report=report,
        expected=Expectation(
            phase=Phase(str(expected["phase"])),
            require_complete_plan=bool(
                expected.get("require_complete_plan", False)
            ),
            require_plan_unchanged=bool(
                expected.get("require_plan_unchanged", False)
            ),
            min_task_count=int(expected.get("min_task_count", 0)),
            max_task_count=_optional_int(
                expected.get("max_task_count")
            ),
            min_top_level_task_count=int(
                expected.get("min_top_level_task_count", 0)
            ),
            max_top_level_task_count=_optional_int(
                expected.get("max_top_level_task_count")
            ),
            min_question_count=_optional_int(
                expected.get("min_question_count")
            ),
            max_question_count=_optional_int(
                expected.get("max_question_count")
            ),
            require_complete_review_report=bool(
                expected.get("require_complete_review_report", False)
            ),
            min_issue_count=int(expected.get("min_issue_count", 0)),
            minimum_issue_severity=(
                IssueSeverity(str(severity))
                if severity is not None
                else None
            ),
            required_plan_terms=_term_groups(
                expected.get("required_plan_terms", [])
            ),
            required_constraint_terms=_term_groups(
                expected.get("required_constraint_terms", [])
            ),
            required_issue_terms=_term_groups(
                expected.get("required_issue_terms", [])
            ),
        ),
    )


def _parse_plan(data: dict[str, object]) -> Plan:
    tasks = data.get("tasks", [])
    constraints = data.get("constraints", [])
    if not isinstance(tasks, list):
        raise TypeError("Plan tasks must be a list")
    if not isinstance(constraints, list):
        raise TypeError("Plan constraints must be a list")
    return Plan(
        goal=str(data.get("goal", "")),
        description=str(data.get("description", "")),
        acceptance_criterion=str(data.get("acceptance_criterion", "")),
        constraints=[str(constraint) for constraint in constraints],
        tasks=[_parse_task(task) for task in tasks],
    )


def _parse_task(data: object) -> Task:
    if not isinstance(data, dict):
        raise TypeError("Task must be an object")
    subtasks = data.get("subtasks", [])
    if not isinstance(subtasks, list):
        raise TypeError("Task subtasks must be a list")
    level = int(data["level"])
    if level not in (1, 2, 3):
        raise ValueError("Task level must be 1, 2, or 3")
    return Task(
        title=str(data["title"]),
        level=cast(TaskLevel, level),
        subtasks=[_parse_task(subtask) for subtask in subtasks],
    )


def _parse_review_report(data: dict[str, object]) -> ReviewReport:
    issues = data.get("issues", [])
    if not isinstance(issues, list):
        raise TypeError("Review issues must be a list")
    return ReviewReport(
        summary=str(data.get("summary", "")),
        issues=[_parse_issue(issue) for issue in issues],
    )


def _parse_issue(data: object) -> ReviewIssue:
    if not isinstance(data, dict):
        raise TypeError("Review issue must be an object")
    return ReviewIssue(
        description=str(data["description"]),
        evidence=str(data["evidence"]),
        severity=IssueSeverity(str(data["severity"])),
    )


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _term_groups(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        raise TypeError("Term groups must be a list")
    return [
        [str(term) for term in group]
        for group in value
        if isinstance(group, list)
    ]


def _new_session(model: Model) -> PlanSession:
    models = builtin_models()
    return PlanSession(
        Agent(AgentState(model=model), models.stream),
        Agent(AgentState(model=model), models.stream),
    )


async def run_case(
    case: EvalCase,
    model: Model,
) -> tuple[
    list[Check],
    float,
    list[Usage],
    dict[str, object],
    str | None,
]:
    session = _new_session(model)
    session.plan = case.plan
    session.review_report = case.review_report
    session.phase = (
        Phase.PLAN if case.target == "plan" else Phase.REVIEW
    )
    agent = (
        session.plan_agent
        if case.target == "plan"
        else session.review_agent
    )
    agent.state.system_prompt = session.build_system_prompt(session.phase)
    agent.state.tools = session.tools_for(session.phase)

    started = perf_counter()
    error_message: str | None = None
    try:
        await agent.prompt(case.prompt)
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
    latency_ms = (perf_counter() - started) * 1000
    usages = [
        message.usage
        for message in agent.state.messages
        if isinstance(message, AssistantMessage)
    ]
    checks = (
        []
        if error_message is not None
        else _score(case, session, _assistant_text(agent))
    )
    output = {
        "phase": session.phase.value,
        "plan": asdict(session.plan),
        "review_report": (
            asdict(session.review_report)
            if session.review_report is not None
            else None
        ),
        "assistant_text": _assistant_text(agent),
    }
    return checks, latency_ms, usages, output, error_message


def _score(
    case: EvalCase,
    session: PlanSession,
    assistant_text: str,
) -> list[Check]:
    expected = case.expected
    checks = [
        Check(
            "phase",
            session.phase is expected.phase,
            f"actual={session.phase}; expected={expected.phase}",
        )
    ]

    if expected.require_complete_plan:
        complete = all(
            (
                session.plan.goal.strip(),
                session.plan.description.strip(),
                session.plan.acceptance_criterion.strip(),
                session.plan.tasks,
            )
        )
        checks.append(Check("complete_plan", bool(complete), "all fields"))

    if expected.require_plan_unchanged:
        checks.append(Check(
            "plan_unchanged",
            session.plan == case.plan,
            "plan must not change before clarification",
        ))

    task_count = _task_count(session.plan.tasks)
    top_level_task_count = len(session.plan.tasks)
    if expected.min_task_count:
        checks.append(Check(
            "minimum_task_count",
            task_count >= expected.min_task_count,
            f"actual={task_count}; minimum={expected.min_task_count}",
        ))
    if expected.max_task_count is not None:
        checks.append(Check(
            "maximum_task_count",
            task_count <= expected.max_task_count,
            f"actual={task_count}; maximum={expected.max_task_count}",
        ))
    if expected.min_top_level_task_count:
        checks.append(Check(
            "minimum_top_level_task_count",
            top_level_task_count >= expected.min_top_level_task_count,
            (
                f"actual={top_level_task_count}; "
                f"minimum={expected.min_top_level_task_count}"
            ),
        ))
    if expected.max_top_level_task_count is not None:
        checks.append(Check(
            "maximum_top_level_task_count",
            top_level_task_count <= expected.max_top_level_task_count,
            (
                f"actual={top_level_task_count}; "
                f"maximum={expected.max_top_level_task_count}"
            ),
        ))

    question_count = (
        assistant_text.count("?") + assistant_text.count("？")
    )
    if expected.min_question_count is not None:
        checks.append(Check(
            "minimum_question_count",
            question_count >= expected.min_question_count,
            (
                f"actual={question_count}; "
                f"minimum={expected.min_question_count}"
            ),
        ))
    if expected.max_question_count is not None:
        checks.append(Check(
            "maximum_question_count",
            question_count <= expected.max_question_count,
            (
                f"actual={question_count}; "
                f"maximum={expected.max_question_count}"
            ),
        ))

    if expected.required_plan_terms:
        checks.append(_term_check(
            "plan_terms",
            json.dumps(asdict(session.plan), ensure_ascii=False),
            expected.required_plan_terms,
        ))

    if expected.required_constraint_terms:
        checks.append(_term_check(
            "constraint_terms",
            json.dumps(session.plan.constraints, ensure_ascii=False),
            expected.required_constraint_terms,
        ))

    issues = (
        session.review_report.issues
        if session.review_report is not None
        else []
    )
    if expected.require_complete_review_report:
        report = session.review_report
        complete_report = (
            report is not None
            and bool(report.summary.strip())
            and all(
                issue.description.strip() and issue.evidence.strip()
                for issue in report.issues
            )
        )
        checks.append(Check(
            "complete_review_report",
            bool(complete_report),
            "non-empty summary, descriptions, and evidence",
        ))

    if expected.min_issue_count:
        checks.append(Check(
            "minimum_issue_count",
            len(issues) >= expected.min_issue_count,
            f"actual={len(issues)}; minimum={expected.min_issue_count}",
        ))

    if expected.minimum_issue_severity is not None:
        severity_rank = {
            IssueSeverity.SUGGESTION: 0,
            IssueSeverity.WARNING: 1,
            IssueSeverity.BLOCKING: 2,
        }
        required_rank = severity_rank[expected.minimum_issue_severity]
        checks.append(Check(
            "minimum_issue_severity",
            any(
                severity_rank[issue.severity] >= required_rank
                for issue in issues
            ),
            f"minimum={expected.minimum_issue_severity}",
        ))

    if expected.required_issue_terms:
        issue_text = json.dumps(
            [asdict(issue) for issue in issues],
            ensure_ascii=False,
        )
        checks.append(_term_check(
            "issue_terms",
            issue_text,
            expected.required_issue_terms,
        ))

    return checks


def _term_check(
    name: str,
    text: str,
    groups: list[list[str]],
) -> Check:
    normalized = _normalize(text)
    missing = [
        group
        for group in groups
        if not any(_normalize(term) in normalized for term in group)
    ]
    return Check(name, not missing, f"missing={missing}")


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _task_count(tasks: list[Task]) -> int:
    return sum(
        1 + _task_count(task.subtasks)
        for task in tasks
    )


def _assistant_text(agent: Agent) -> str:
    return "\n".join(
        block.text
        for message in agent.state.messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextContent)
    )


def _summarize_usage(usages: list[Usage]) -> dict[str, object]:
    return {
        "model_calls": len(usages),
        "input": sum(usage.input for usage in usages),
        "output": sum(usage.output for usage in usages),
        "cache_read": sum(usage.cache_read for usage in usages),
        "cache_write": sum(usage.cache_write for usage in usages),
        "reasoning": sum(usage.reasoning or 0 for usage in usages),
        "total_tokens": sum(usage.total_tokens for usage in usages),
        "cost": {
            "input": sum(usage.cost.input for usage in usages),
            "output": sum(usage.cost.output for usage in usages),
            "cache_read": sum(
                usage.cost.cache_read for usage in usages
            ),
            "cache_write": sum(
                usage.cost.cache_write for usage in usages
            ),
            "total": sum(usage.cost.total for usage in usages),
        },
    }


async def _run(args: argparse.Namespace) -> int:
    load_project_env()
    models = builtin_models()
    suite = load_suite(args.suite)
    cases = suite.cases
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case.case_id in selected]
        missing = selected - {case.case_id for case in cases}
        if missing:
            raise ValueError(f"Unknown eval cases: {sorted(missing)}")
    models_by_target = _select_models(cases, models, args)

    passed = 0
    total = 0
    started_at = datetime.now(timezone.utc)
    results: list[dict[str, object]] = []
    all_usages: list[Usage] = []
    for repetition in range(1, args.repetitions + 1):
        for case in cases:
            total += 1
            model = models_by_target[case.target]
            checks, latency_ms, usages, output, error_message = (
                await run_case(case, model)
            )
            all_usages.extend(usages)
            usage = _summarize_usage(usages)
            if error_message is not None:
                print(
                    f"FAIL {case.case_id} #{repetition}: "
                    f"[{model.provider}/{model.id}] "
                    f"{error_message}"
                )
                results.append({
                    "case_id": case.case_id,
                    "target": case.target,
                    "repetition": repetition,
                    "passed": False,
                    "latency_ms": latency_ms,
                    "usage": usage,
                    "output": output,
                    "checks": [],
                    "error": error_message,
                })
                continue

            case_passed = all(check.passed for check in checks)
            passed += case_passed
            status = "PASS" if case_passed else "FAIL"
            print(
                f"{status} {case.case_id} #{repetition} "
                f"[{model.provider}/{model.id}] "
                f"({latency_ms:.0f} ms)"
            )
            for check in checks:
                mark = "✓" if check.passed else "✗"
                print(f"  {mark} {check.name}: {check.detail}")
            results.append({
                "case_id": case.case_id,
                "target": case.target,
                "repetition": repetition,
                "passed": case_passed,
                "latency_ms": latency_ms,
                "usage": usage,
                "output": output,
                "checks": [asdict(check) for check in checks],
                "error": None,
            })

    print(f"\nSummary: {passed}/{total} passed")
    if not args.no_save:
        output_path = save_results(
            suite=suite,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            passed=passed,
            total=total,
            models={
                target: {
                    "provider": model.provider,
                    "id": model.id,
                }
                for target, model in models_by_target.items()
            },
            usage=_summarize_usage(all_usages),
            results=results,
            output_dir=args.output_dir,
        )
        print(f"Saved: {output_path}")
    return 0 if passed == total else 1


def _select_models(
    cases: list[EvalCase],
    models: Models,
    args: argparse.Namespace,
) -> dict[EvalTarget, Model]:
    override = (
        ModelSelection(args.provider, args.model)
        if args.provider is not None and args.model is not None
        else None
    )
    selected: dict[EvalTarget, Model] = {}
    targets: tuple[EvalTarget, ...] = ("plan", "review")

    for target in targets:
        if not any(case.target == target for case in cases):
            continue
        phase = "PLAN" if target == "plan" else "REVIEW"
        selection = override or load_model_selection(phase)
        model = models.get_model(selection.provider, selection.model)
        if model is None:
            raise ValueError(
                f"Unknown {phase} model: "
                f"{selection.provider}/{selection.model}"
            )
        selected[target] = model

    return selected


def save_results(
    *,
    suite: EvalSuite,
    started_at: datetime,
    completed_at: datetime,
    passed: int,
    total: int,
    models: dict[str, dict[str, str]],
    usage: dict[str, object],
    results: list[dict[str, object]],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = started_at.strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"{timestamp}_{suite.name}.json"
    payload = {
        "suite": suite.name,
        "suite_version": suite.version,
        "models": models,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "summary": {
            "passed": passed,
            "total": total,
            "pass_rate": passed / total if total else 0,
            "usage": usage,
        },
        "results": results,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Plan Agent phase against real models."
    )
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument(
        "--provider",
        help="Override the configured model provider for all cases.",
    )
    parser.add_argument(
        "--model",
        help="Override the configured model id for all cases.",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print results without saving a JSON report.",
    )
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if (args.provider is None) != (args.model is None):
        parser.error("--provider and --model must be used together")
    return args


def main() -> int:
    return asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

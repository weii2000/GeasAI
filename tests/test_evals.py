import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evals.single_phase import (
    _score,
    _summarize_usage,
    load_suite,
    save_results,
)
from geas.config import load_model_selection
from geas.plan_agent.types import Phase, Plan, Task

from .helpers import make_assistant, make_session


def test_single_phase_eval_suite_has_unique_plan_and_review_cases() -> None:
    suite = load_suite()
    cases = suite.cases

    assert suite.version == "0.2"
    assert len(cases) == 6
    assert len({case.case_id for case in cases}) == 6
    assert sum(case.target == "plan" for case in cases) == 3
    assert sum(case.target == "review" for case in cases) == 3


def test_single_phase_eval_rejects_overdecomposed_plan() -> None:
    case = next(
        case
        for case in load_suite().cases
        if case.case_id == "plan_complete_goal_submits"
    )
    session, _plan_model, _review_model = make_session([])
    session.phase = Phase.REVIEW
    session.plan = Plan(
        goal="完成项目",
        description="四周完成任务管理 API",
        acceptance_criterion="核心接口测试通过",
        constraints=["四周内完成", "每天最多投入 1 小时"],
        tasks=[
            Task(title=f"任务 {index}", level=1)
            for index in range(41)
        ],
    )

    checks = {
        check.name: check
        for check in _score(case, session, "")
    }

    assert not checks["maximum_task_count"].passed
    assert not checks["maximum_top_level_task_count"].passed


def test_single_phase_eval_saves_json_result(tmp_path: Path) -> None:
    suite = load_suite()
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    usage = make_assistant([], "stop").usage
    usage.input = 100
    usage.output = 20
    usage.reasoning = 5
    usage.total_tokens = 120
    usage.cost.total = 0.01

    path = save_results(
        suite=suite,
        started_at=now,
        completed_at=now,
        passed=1,
        total=1,
        models={
            "plan": {
                "provider": "deepseek",
                "id": "deepseek-v4-flash",
            }
        },
        usage=_summarize_usage([usage]),
        results=[{"case_id": "example"}],
        output_dir=tmp_path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["summary"]["pass_rate"] == 1
    assert payload["models"]["plan"]["provider"] == "deepseek"
    assert payload["summary"]["usage"]["total_tokens"] == 120


def test_model_selection_requires_phase_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEAS_PLAN_PROVIDER", raising=False)
    monkeypatch.delenv("GEAS_PLAN_MODEL", raising=False)

    with pytest.raises(ValueError, match="PLAN model is not configured"):
        load_model_selection("PLAN")

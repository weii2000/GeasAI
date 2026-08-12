from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from pydantic import TypeAdapter

from geas.plan_agent.profiles import (
    BASE_PROMPT,
    PLAN_PROMPT,
    REVIEW_PROMPT,
)
from geas.plan_agent.types import Phase

from .single_phase import (
    DEFAULT_SUITE_PATH,
    Check,
    EvalCase,
    EvalOutput,
    load_suite,
    save_results,
    score_output,
)


DEFAULT_OUTPUT_DIR = Path("eval-results/comparison/codex")
_OUTPUT = TypeAdapter(EvalOutput)
type TokenUsage = dict[str, int]


def _build_prompt(case: EvalCase) -> str:
    if case.target == "plan":
        phase_prompt = PLAN_PROMPT
        mapping = """
- 需要澄清时：保持输入状态不变，在 assistant_text 中提问。
- update_plan：把完整的新计划写入 plan。
- submit_plan：把 phase 设为 review，并把 review_report 设为 null。
"""
    else:
        phase_prompt = REVIEW_PROMPT
        mapping = """
- update_review_report：把完整评审结果写入 review_report，plan 保持不变。
- request_change：把 phase 设为 plan。
- approve_plan：把 phase 设为 pending_approval。
"""

    state = json.dumps(
        {
            "phase": case.target,
            "plan": asdict(case.input.plan),
            "review_report": (
                asdict(case.input.review_report)
                if case.input.review_report is not None
                else None
            ),
        },
        ensure_ascii=False,
        indent=2,
        default=lambda value: value.isoformat(),
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"""
{BASE_PROMPT}

{phase_prompt}

Codex eval 适配规则：
- 本适配器不提供 Geas tools。不要调用 Codex 工具、读取仓库或访问网络。
- 直接返回上述 Geas tool 调用完成后的最终状态。
{mapping.strip()}
- assistant_text 只放需要直接回复用户的文字，否则使用空字符串。

Current local time: {now}

Current session state:
{state}

User message:
{case.input.prompt}
""".strip()


def _strict_schema(value: object, property_map: bool = False) -> None:
    if isinstance(value, dict):
        if not property_map:
            for key in ("default", "format", "title"):
                value.pop(key, None)
        for key, child in value.items():
            _strict_schema(child, key == "properties")
        properties = value.get("properties")
        if value.get("type") == "object" and isinstance(properties, dict):
            value["required"] = list(properties)
            value["additionalProperties"] = False
    elif isinstance(value, list):
        for child in value:
            _strict_schema(child)


def _parse_usage(data: bytes) -> TokenUsage | None:
    totals = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "reasoning": 0,
        "total_tokens": 0,
    }
    found = False
    for line in data.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if event.get("type") != "turn.completed" or not isinstance(
            usage,
            dict,
        ):
            continue
        input_tokens = int(usage.get("input_tokens", 0))
        cached_tokens = int(usage.get("cached_input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        totals["input"] += max(input_tokens - cached_tokens, 0)
        totals["cache_read"] += cached_tokens
        totals["output"] += output_tokens
        totals["reasoning"] += int(
            usage.get("reasoning_output_tokens", 0)
        )
        totals["total_tokens"] += input_tokens + output_tokens
        found = True
    return totals if found else None


def _usage_report(tokens: TokenUsage | None) -> dict[str, object]:
    return {
        "model_calls": None,
        "input": tokens["input"] if tokens else None,
        "output": tokens["output"] if tokens else None,
        "cache_read": tokens["cache_read"] if tokens else None,
        "cache_write": None,
        "reasoning": tokens["reasoning"] if tokens else None,
        "total_tokens": tokens["total_tokens"] if tokens else None,
        "cost": {
            "input": None,
            "output": None,
            "cache_read": None,
            "cache_write": None,
            "total": None,
        },
    }


def _sum_usage(usages: list[TokenUsage]) -> TokenUsage | None:
    if not usages:
        return None
    return {
        key: sum(usage[key] for usage in usages)
        for key in usages[0]
    }


async def run_case(
    case: EvalCase,
    model: str | None,
    effort: str | None,
) -> tuple[
    list[Check],
    float,
    TokenUsage | None,
    EvalOutput,
    str | None,
]:
    initial_output = EvalOutput(
        phase=Phase.PLAN if case.target == "plan" else Phase.REVIEW,
        plan=case.input.plan,
        review_report=case.input.review_report,
        assistant_text="",
    )
    with TemporaryDirectory() as directory:
        root = Path(directory)
        schema_path = root / "output-schema.json"
        output_path = root / "output.json"
        schema = _OUTPUT.json_schema()
        _strict_schema(schema)
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False),
            encoding="utf-8",
        )

        command = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--json",
            "--cd",
            directory,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if model is not None:
            command.extend(("--model", model))
        if effort is not None:
            command.extend((
                "--config",
                f'model_reasoning_effort="{effort}"',
            ))
        command.append(_build_prompt(case))

        started = perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        except OSError as error:
            return (
                [],
                (perf_counter() - started) * 1000,
                None,
                initial_output,
                f"{type(error).__name__}: {error}",
            )

        latency_ms = (perf_counter() - started) * 1000
        try:
            usage = _parse_usage(stdout)
        except (TypeError, ValueError):
            usage = None

        if process.returncode:
            detail = stderr.decode(errors="replace").strip()[-2000:]
            return (
                [],
                latency_ms,
                usage,
                initial_output,
                detail or f"Codex exited with code {process.returncode}",
            )

        try:
            output = _OUTPUT.validate_json(output_path.read_bytes())
        except Exception as error:
            return (
                [],
                latency_ms,
                usage,
                initial_output,
                f"Invalid Codex output: {error}",
            )
        return score_output(case, output), latency_ms, usage, output, None


async def _run(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    cases = suite.cases
    if args.case_ids:
        selected = set(args.case_ids)
        cases = [case for case in cases if case.case_id in selected]
        missing = selected - {case.case_id for case in cases}
        if missing:
            raise ValueError(f"Unknown eval cases: {sorted(missing)}")

    passed = 0
    total = 0
    started_at = datetime.now(timezone.utc)
    results: list[dict[str, object]] = []
    all_usages: list[TokenUsage] = []
    model_name = args.model or "default"
    effort_name = args.effort or "model_default"
    for repetition in range(1, args.repetitions + 1):
        for case in cases:
            total += 1
            checks, latency_ms, usage, output, error = await run_case(
                case,
                args.model,
                args.effort,
            )
            if usage is not None:
                all_usages.append(usage)
            case_passed = error is None and all(
                check.passed for check in checks
            )
            passed += case_passed
            status = "PASS" if case_passed else "FAIL"
            print(
                f"{status} {case.case_id} #{repetition} "
                f"[codex/{model_name}/{effort_name}] "
                f"({latency_ms:.0f} ms)"
            )
            for check in checks:
                mark = "✓" if check.passed else "✗"
                print(f"  {mark} {check.name}: {check.detail}")
            if error is not None:
                print(f"  {error}")
            results.append({
                "case_id": case.case_id,
                "target": case.target,
                "repetition": repetition,
                "passed": case_passed,
                "latency_ms": latency_ms,
                "usage": _usage_report(usage),
                "output": asdict(output),
                "checks": [asdict(check) for check in checks],
                "error": error,
            })

    total_usage = _sum_usage(all_usages)
    token_text = (
        str(total_usage["total_tokens"])
        if total_usage is not None
        else "unavailable"
    )
    print(
        f"\nSummary: {passed}/{total} passed; "
        f"tokens={token_text}; cost=unavailable"
    )
    if not args.no_save:
        targets = {case.target for case in cases}
        output_path = save_results(
            suite=suite,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            passed=passed,
            total=total,
            models={
                target: {
                    "provider": "codex",
                    "id": model_name,
                    "reasoning_effort": effort_name,
                }
                for target in targets
            },
            usage=_usage_report(total_usage),
            results=results,
            output_dir=args.output_dir,
        )
        print(f"Saved: {output_path}")
    return 0 if passed == total else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the single-phase eval suite with Codex CLI."
    )
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--model", help="Codex model override.")
    parser.add_argument(
        "--effort",
        choices=("minimal", "low", "medium", "high", "xhigh"),
        help="Codex reasoning effort override.",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    return args


def main() -> int:
    return asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

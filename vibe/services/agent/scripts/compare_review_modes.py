"""Compare legacy, paired and direct review on the same inputs and audit saved reports."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from statistics import mean
from time import monotonic
from typing import get_args
from uuid import uuid4

from pydantic_core import ErrorType

from agent.config.settings import Settings
from agent.services.spec_review import _VALIDATION_REASONS
from agent.services.spec_stage_retry import TRANSIENT_RETRY_BACKOFF_SECONDS, TRANSIENT_RETRY_LIMIT
from agent.spec_cli_progress import STAGES, TASK_RECOVERY_MESSAGES
from agent.spec_review_cli import write_report

if __package__:
    from .evaluate_quality_benchmark import evaluate_case
    from .evaluate_spec_report import evaluate
else:
    from evaluate_quality_benchmark import evaluate_case
    from evaluate_spec_report import evaluate


COMPARISON_MODES = ("legacy", "multi_agent", "multi_agent_direct")


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    source: Path
    gold: Path | None
    official: bool = False


@dataclass(frozen=True)
class Trial:
    case: BenchmarkCase
    mode: str
    repetition: int

    @property
    def name(self) -> str:
        return f"{self.case.name}-{self.mode}-{self.repetition:02d}"


def experiment_matrix(cases: list[BenchmarkCase], repetitions: int) -> list[Trial]:
    trials = []
    for case_index, case in enumerate(cases):
        for repetition in range(1, repetitions + 1):
            offset = (case_index + repetition - 1) % len(COMPARISON_MODES)
            order = COMPARISON_MODES[offset:] + COMPARISON_MODES[:offset]
            trials.extend(Trial(case, mode, repetition) for mode in order)
    return trials


def summarize_runs(runs: list[dict]) -> dict:
    by_case: dict = {}
    for case in sorted({run["case"] for run in runs}):
        by_case[case] = {}
        for mode in COMPARISON_MODES:
            selected = [r for r in runs if r["case"] == case and r["mode"] == mode]
            complete = [r for r in selected if r["status"] == "completed"]

            def measured(*path: str) -> float | None:
                values = []
                for trial in complete:
                    value = trial
                    for key in path:
                        value = value.get(key) if isinstance(value, dict) else None
                    if value is None:
                        return None
                    values.append(value)
                return round(mean(values), 4) if values else None

            usages = [trial.get("usage") or {} for trial in selected]

            def total_reported(direction: str) -> int | None:
                values = [usage.get(f"reported_{direction}_tokens", usage.get(f"{direction}_tokens")) for usage in usages]
                known = [value for value in values if value is not None]
                return sum(known) if known else None

            by_case[case][mode] = {
                "trials": len(selected),
                "completed": len(complete),
                "failed": len(selected) - len(complete),
                "structural_passed": sum(r.get("structural_passed", False) for r in complete),
                "mean_elapsed_seconds": measured("elapsed_seconds"),
                "all_trials_reported_input_tokens": total_reported("input"),
                "all_trials_reported_output_tokens": total_reported("output"),
                "all_trials_usage_complete": all(usage.get("usage_complete") is True for usage in usages) if usages else None,
                "mean_input_tokens": measured("usage", "input_tokens"),
                "mean_output_tokens": measured("usage", "output_tokens"),
                "mean_cost_usd": measured("usage", "cost_usd"),
                "mean_findings": measured("structural", "metrics", "findings"),
                "mean_annotated_recall": measured("quality", "annotation_recall"),
                "mean_extraction_recall": measured("quality", "extraction", "annotation_recall"),
            }
    return by_case


def compare_pair(baseline: dict, candidate: dict) -> dict:
    def token_count(record):
        usage = record.get("usage") or {}
        counts = usage.get("input_tokens"), usage.get("output_tokens")
        return sum(counts) if None not in counts else None

    pair = baseline, candidate
    baseline_tokens, candidate_tokens = map(token_count, pair)
    baseline_recall, candidate_recall = ((run.get("quality") or {}).get("annotation_recall") for run in pair)
    baseline_report, candidate_report = (run.get("structural") or {} for run in pair)

    def same_report_field(field):
        first, second = baseline_report.get(field), candidate_report.get(field)
        return first == second if first and second else None

    same_model = same_report_field("model")
    same_input = same_report_field("source_sha256")
    same_run = same_report_field("run_id")
    independent = not same_run if same_run is not None else None
    first_hash, second_hash = (run.get("implementation_fingerprint") for run in pair)
    frozen = ((first_hash == second_hash and all(run.get("implementation_unchanged_during_trial") is True for run in pair))
              if first_hash and second_hash else None)
    first_settings, second_settings = (run.get("trial_settings") for run in pair)
    same_settings = first_settings == second_settings if first_settings and second_settings else None
    complete = all(run["status"] == "completed" and run.get("structural_passed") for run in pair)
    within_budget = None
    if baseline_tokens is not None and baseline_tokens > 0 and candidate_tokens is not None:
        within_budget = abs(baseline_tokens - candidate_tokens) <= baseline_tokens * 0.1

    return {
        "case": baseline["case"], "repetition": baseline["repetition"], "both_valid": complete,
        "same_model": same_model, "same_source": same_input, "independent_runs": independent,
        "same_implementation": frozen, "same_settings": same_settings,
        "baseline_mode": baseline["mode"], "candidate_mode": candidate["mode"],
        "baseline_tokens": baseline_tokens, "candidate_tokens": candidate_tokens,
        f"{baseline['mode']}_tokens": baseline_tokens, f"{candidate['mode']}_tokens": candidate_tokens,
        "tokens_within_10_percent": within_budget,
        "eligible_for_equal_budget_analysis": all((complete, same_model, same_input, independent, frozen, same_settings, within_budget)),
        "annotation_recall_delta": (round(candidate_recall - baseline_recall, 4)
                                    if complete and baseline_recall is not None and candidate_recall is not None else None),
        "elapsed_seconds_delta": round(candidate["elapsed_seconds"] - baseline["elapsed_seconds"], 3) if complete else None,
    }


def paired_comparisons(runs: list[dict]) -> list[dict]:
    by_case = {}
    for run in runs:
        by_case.setdefault((run["case"], run["repetition"]), {})[run["mode"]] = run
    return [
        compare_pair(group[baseline], group[candidate])
        for _, group in sorted(by_case.items())
        for baseline, candidate in combinations(COMPARISON_MODES, 2)
        if baseline in group and candidate in group
    ]


def write_json(path: Path, value: dict) -> None:
    write_report(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def score_expert_review(report_path: Path, review: dict) -> dict:
    raw = report_path.read_bytes()
    report = json.loads(raw)
    if review["run_id"] != report["passport"]["run_id"] or review["report_sha256"] != sha256(raw).hexdigest():
        raise ValueError("Expert decisions do not identify this exact generated report")
    finding_ids = {f"D-{i:03d}" for i in range(1, len(report["findings"]) + 1)}
    decisions = review["findings"]
    if not isinstance(decisions, dict) or not decisions.keys() <= finding_ids:
        raise ValueError("Unknown finding in expert review")
    defects = set()
    false_positives = []
    for finding_id, labels in sorted(decisions.items()):
        if labels is None:
            continue
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError("Expected a list of adjudicated defect IDs or null")
        if labels:
            defects.update(labels)
        else:
            false_positives.append(finding_id)
    unresolved = sorted(f for f in finding_ids if decisions.get(f) is None)
    return {"unique_confirmed_defects": len(defects), "confirmed_defect_ids": sorted(defects),
            "confirmed_false_findings": len(false_positives), "false_finding_ids": false_positives,
            "unassessed_or_uncertain_finding_ids": unresolved, "adjudication_complete": not unresolved}


def apply_expert_reviews(output: Path, adjudications: Path) -> None:
    comparison = json.loads((output / "comparison.json").read_text())
    reviews = json.loads(adjudications.read_text())
    available = {run["structural"]["run_id"]: run for run in comparison["runs"] if run.get("structural_passed")}
    seen = set()
    for review in reviews:
        run_id = review["run_id"]
        if run_id not in available or run_id in seen:
            raise ValueError("Unknown or duplicate trial in expert reviews")
        run = available[run_id]
        report = (output / run["report"]).resolve()
        if report.parent != output:
            raise ValueError("Expected a report from the experiment directory")
        run["expert_review"] = score_expert_review(report, review)
        seen.add(run_id)
    comparison["expert_reviewed_runs"] = len(seen)
    write_json(output / "adjudicated-comparison.json", comparison)


def implementation_fingerprint() -> str:
    project = Path(__file__).resolve().parents[1]
    files = [*sorted((project / "src/agent/services").glob("spec_*.py")),
             project / "src/agent/spec_review_cli.py", project / "src/agent/utils/review_telemetry.py",
             project / "src/agent/config/settings.py", project / "uv.lock"]
    digest = sha256()
    for file in files:
        digest.update(str(file.relative_to(project)).encode())
        digest.update(sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def trial_settings(env_file: Path, timeout: int) -> dict:
    config = Settings(_env_file=env_file)
    return {
        "model": config.llm_model, "endpoint_sha256": sha256(config.llm_base_url.encode()).hexdigest(),
        "analysis_limit_seconds": timeout, "request_timeout_seconds": config.llm_timeout_seconds,
        "request_retries": config.llm_max_retries, "primary_concurrency": config.spec_review_concurrency,
        "task_transient_retry_limit": TRANSIENT_RETRY_LIMIT,
        "task_transient_retry_backoff_seconds": TRANSIENT_RETRY_BACKOFF_SECONDS,
        "drex_enabled": config.spec_review_use_nace, "drex_model": config.nace_model,
        "drex_timeout_seconds": config.nace_timeout_seconds,
    }


# The CLI progress stream contains application-authored diagnostics. Keep
# exactly those structured values, never arbitrary exception names/messages,
# fields, stdout, provider responses or raw stderr in benchmark artifacts.
_ERROR_TYPES = frozenset({
    "APIError", "APIStatusError", "APIConnectionError", "APITimeoutError",
    "APIResponseValidationError", "AuthenticationError", "PermissionDeniedError",
    "BadRequestError", "NotFoundError", "ConflictError", "UnprocessableEntityError",
    "RateLimitError", "InternalServerError", "HTTPStatusError", "RequestError",
    "ConnectError", "ReadError", "WriteError", "RemoteProtocolError",
    "LocalProtocolError", "ProtocolError", "NetworkError", "TransportError",
    "TimeoutError", "TimeoutException", "ConnectTimeout", "ReadTimeout",
    "WriteTimeout", "PoolTimeout", "ConnectionError", "ConnectionResetError",
    "ConnectionRefusedError", "BrokenPipeError", "OSError", "ValueError",
    "ValidationError", "JSONDecodeError", "UnicodeDecodeError",
    "TypeError", "RuntimeError", "AttributeError", "KeyError", "IndexError",
    "OutputParserException", "ReviewPipelineError", "ReviewProviderRateLimit",
})
_TIMEOUT_TYPES = frozenset({
    "TimeoutError", "TimeoutException", "APITimeoutError", "ConnectTimeout",
    "ReadTimeout", "WriteTimeout", "PoolTimeout",
})
_SCHEMA_CODES = frozenset(get_args(ErrorType))
_SAFE_VALIDATION_REASONS = _VALIDATION_REASONS | {"The JSON schema is invalid."}
_RATE_LIMIT_LINE = "Ошибка: AI-провайдер ограничил запросы. Повторите запуск позже или используйте --wait-rate-limit."
_DEADLINE_LINE = "Ошибка: истёк срок проверки. Попробуйте документ меньшего размера или увеличьте --timeout-seconds."
_PROGRESS_LINE = re.compile(r"\[[0-9]{2,4}:[0-5][0-9]\] (.*)")
_REQUEST_LINE = re.compile(r"запрос ([0-9]{1,9}) · (отправлен|ответ получен|запрос не завершён)")


def _stage_failure(stage: str | None, message: str) -> dict | None:
    fields = message.split(" · ")
    if fields[0] not in _ERROR_TYPES and fields[0] != "Ошибка анализа":
        return None
    error_type = fields[0] if fields[0] in _ERROR_TYPES else None
    status = None
    codes = []
    if len(fields) <= 3:
        for field in fields[1:]:
            http = re.fullmatch(r"HTTP ([1-5][0-9]{2})", field)
            if http:
                status = int(http[1])
            elif error_type == "ValidationError":
                codes.extend(code for code in field.split(", ") if code in _SCHEMA_CODES)
    category = "review_failed"
    if status == 429 or error_type in {"RateLimitError", "ReviewProviderRateLimit"}:
        category = "rate_limited"
    elif status in {408, 504} or error_type in _TIMEOUT_TYPES:
        category = "provider_timeout"
    elif error_type == "ValidationError":
        category = "validation_failed"
    return {
        "failure_category": category, "failed_stage": stage,
        "error_type": error_type, "http_status": status,
        "validation_reason": None, "schema_error_codes": sorted(set(codes)),
    }


def failure_diagnostics(stderr: str | bytes | None, *, exit_code: int, process_timeout: bool = False) -> dict:
    """Recover a last safe diagnostic, without copying stderr or inferring a stage
    from unrecognized text. An ambiguous deadline has no asserted failed stage."""
    result = {
        "failure_category": "review_failed" if exit_code else None,
        "failed_stage": None, "error_type": None, "http_status": None,
        "validation_reason": None, "schema_error_codes": [],
    }
    if not exit_code:
        return result
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    labels = {label: stage for stage, label in STAGES.items()}
    labels["AI-анализ"] = None
    latest: dict[str | None, tuple[int, dict]] = {}
    validation_events: Counter[tuple[str | None, str | None]] = Counter()
    active: set[tuple[str | None, str]] = set()
    terminal_category = None
    for index, line in enumerate((stderr or "").splitlines()):
        if line == _RATE_LIMIT_LINE:
            terminal_category = "rate_limited"
            continue
        if line == _DEADLINE_LINE:
            terminal_category = "deadline"
            continue
        progress = _PROGRESS_LINE.fullmatch(line)
        if not progress:
            continue
        label, separator, message = progress[1].partition(" — ")
        if not separator or label not in labels:
            continue
        stage = labels[label]
        request = _REQUEST_LINE.fullmatch(message)
        if request:
            key = (stage, request[1])
            if request[2] == "отправлен":
                active.add(key)
                # A newer same-stage request may be a correction; do not
                # attribute its eventual success/failure to the old attempt.
                latest.pop(stage, None)
            else:
                active.discard(key)
            continue
        if message == "ответ требует исправления; выполняется повтор":
            latest.pop(stage, None)
            continue
        marker = "проверка ответа не пройдена: "
        if message.startswith(marker):
            reason = message[len(marker):]
            safe_reason = reason if reason in _SAFE_VALIDATION_REASONS else None
            validation_events[stage, safe_reason] += 1
            prior = latest.get(stage)
            diagnostic = {**result, **(prior[1] if prior else {})}
            diagnostic.update(failure_category="validation_failed", failed_stage=stage,
                              validation_reason=safe_reason)
            latest[stage] = index, diagnostic
            continue
        diagnostic = _stage_failure(stage, message)
        if diagnostic:
            latest[stage] = index, diagnostic

    candidates = [item[1] for item in sorted(latest.values(), key=lambda item: item[0], reverse=True)]
    if process_timeout or terminal_category == "deadline":
        timeout = next((item for item in candidates if item["failure_category"] == "provider_timeout"), None)
        if timeout:
            result.update(timeout)
        else:
            active_stages = {stage for stage, _ in active}
            if len(active_stages) == 1:
                result["failed_stage"] = next(iter(active_stages))
        result["failure_category"] = "process_timeout" if process_timeout else "deadline"
    elif terminal_category == "rate_limited":
        rate = next((item for item in candidates if item["failure_category"] == "rate_limited"), None)
        if rate:
            result.update(rate)
        result["failure_category"] = "rate_limited"
    elif candidates:
        result.update(candidates[0])
    if validation_events:
        # Another batch of the same role may start while a failed batch unwinds.
        # Preserve explicitly observed errors separately from the exit diagnosis.
        result["observed_validation_failures"] = [
            {"stage": stage, "reason": reason, "count": count}
            for (stage, reason), count in validation_events.items()
        ]
    return result


def stage_activity(stderr: str | bytes | None) -> dict[str, dict[str, int]]:
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    labels = {label: stage for stage, label in STAGES.items()}
    events = {message: event for event, message in TASK_RECOVERY_MESSAGES.items()}
    counts = {key: Counter() for key in ("stage_requests", *events.values())}
    for line in (stderr or "").splitlines():
        progress = _PROGRESS_LINE.fullmatch(line)
        if not progress:
            continue
        label, _, message = progress[1].partition(" — ")
        if label not in labels:
            continue
        if message in events:
            counts[events[message]][labels[label]] += 1
        else:
            request = _REQUEST_LINE.fullmatch(message)
            if request and request[2] == "отправлен":
                counts["stage_requests"][labels[label]] += 1
    return {key: dict(values) for key, values in counts.items()}


def run_trial(trial: Trial, output_dir: Path, env_file: Path, timeout: int) -> dict:
    name = trial.name
    if any((output_dir / f"{name}{ext}").exists() for ext in (".json", ".usage.json", ".checkpoints")):
        # Preserve artifacts from a process interrupted before saving its trial record.
        name = f"{name}-restart-{uuid4().hex[:12]}"
    report = output_dir / f"{name}.json"
    usage = output_dir / f"{name}.usage.json"
    checkpoints = output_dir / f"{name}.checkpoints"
    started = monotonic()
    fingerprint = implementation_fingerprint()
    settings = trial_settings(env_file, timeout)
    command = [
        sys.executable, "-m", "agent.spec_review_cli", "--input", str(trial.case.source),
        "--output", str(report), "--review-mode", trial.mode, "--timeout-seconds", str(timeout),
        "--metrics-output", str(usage), "--env-file", str(env_file), "--checkpoint-dir", str(checkpoints),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout+360)
        exit_code = result.returncode
        stderr = result.stderr
    except subprocess.TimeoutExpired as error:
        exit_code = 124
        stderr = error.stderr
    record = {
        "case": trial.case.name, "mode": trial.mode, "repetition": trial.repetition,
        "elapsed_seconds": round(monotonic() - started, 3),
        "status": "failed", "exit_code": exit_code, "structural_passed": False,
        **failure_diagnostics(stderr, exit_code=exit_code, process_timeout=exit_code == 124),
        **stage_activity(stderr),
        "checkpoints": checkpoints.name,
        "implementation_fingerprint": fingerprint, "trial_settings": settings,
        "implementation_unchanged_during_trial": fingerprint == implementation_fingerprint(),
        "usage": json.loads(usage.read_text()) if usage.exists() else None,
    }
    if exit_code or not report.exists():
        return record
    try:
        structural = evaluate(trial.case.source, report, official=trial.case.official)
        payload = json.loads(report.read_text())
        # Every newly generated trial must attest the requested mode explicitly.
        # Mode-less historical passports remain readable by the standalone auditor.
        if payload["passport"].get("review_mode") != trial.mode:
            raise ValueError("Unexpected report mode")
        record.update(status="completed", structural=structural,
                      structural_passed=structural["passed"] == structural["total"], report=report.name)
        if trial.case.gold:
            record["quality"] = evaluate_case(trial.case.gold, report)
        else:
            record["quality"] = None
    except (ValueError, TypeError, KeyError, OSError) as error:
        record.update(status="failed", structural_passed=False, audit_error=type(error).__name__)
    return record


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    benchmark = root / "vibe/docs/examples/quality-benchmark"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--official-source", type=Path, default=root / ".artifacts/requirements.md")
    parser.add_argument("--stress-source", type=Path, help="An additional, unlabelled input for a structural and timing comparison")
    parser.add_argument("--official-gold", type=Path, default=root / ".artifacts/organizer-eval/quality-anchor-gold.json", help="Optional retrospective labels; not organizer ground truth")
    parser.add_argument("--case", action="append", choices=["official", "stress", *sorted(p.name.removesuffix(".gold.json") for p in benchmark.glob("*.gold.json"))])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true", help="List trials without contacting the provider")
    parser.add_argument("--resume", action="store_true", help="Continue an interrupted experiment with identical sources, settings and code")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed trial")
    parser.add_argument("--adjudications", type=Path, help="Score expert decisions against an existing experiment without calling a model")
    args = parser.parse_args()
    if args.adjudications:
        try:
            apply_expert_reviews(args.output_dir.resolve(), args.adjudications)
        except (ValueError, TypeError, KeyError, OSError) as error:
            parser.error(f"Invalid expert review or experiment files ({type(error).__name__})")
        return 0
    if not 1 <= args.repetitions <= 20 or not 30 <= args.timeout_seconds <= 900:
        parser.error("repetitions must be 1–20 and timeout 30–900 seconds")
    cases = [BenchmarkCase(p.name.removesuffix(".gold.json"), benchmark / json.loads(p.read_text())["source"], p) for p in sorted(benchmark.glob("*.gold.json"))]
    if args.case is None or "official" in args.case:
        gold = None
        if args.official_gold.is_file():
            annotated = (args.official_gold.parent / json.loads(args.official_gold.read_text())["source"]).resolve()
            if annotated == args.official_source.resolve():
                gold = args.official_gold.resolve()
        cases.insert(0, BenchmarkCase("official", args.official_source.resolve(), gold, True))
    if args.stress_source:
        cases.append(BenchmarkCase("stress", args.stress_source.resolve(), None))
    if args.case:
        by_name = {case.name: case for case in cases}
        if "stress" in args.case and not args.stress_source:
            parser.error("--case stress requires --stress-source")
        cases = [by_name[name] for name in dict.fromkeys(args.case)]
    if not cases or any(not case.source.is_file() for case in cases):
        parser.error("Selected source file does not exist")
    trials = experiment_matrix(cases, args.repetitions)
    if args.dry_run:
        print("\n".join(trial.name for trial in trials))
        return 0
    output = args.output_dir.resolve()
    env_file = args.env_file.resolve()
    manifest = {
        "implementation": implementation_fingerprint(), "settings": trial_settings(env_file, args.timeout_seconds),
        "trial_names": [trial.name for trial in trials],
        "sources": {case.name: sha256(case.source.read_bytes()).hexdigest() for case in cases},
        "annotations": {case.name: sha256(case.gold.read_bytes()).hexdigest() if case.gold else None for case in cases},
    }
    records = []
    manifest_path = output / "experiment.json"
    if args.resume:
        if not manifest_path.is_file() or json.loads(manifest_path.read_bytes()) != manifest:
            parser.error("Cannot resume: experiment sources, settings, or implementation differ")
        comparison = output / "comparison.json"
        records = json.loads(comparison.read_bytes())["runs"] if comparison.exists() else []
        saved = [(r["case"], r["mode"], r["repetition"]) for r in records]
        if saved != [(t.case.name, t.mode, t.repetition) for t in trials[:len(records)]]:
            parser.error("Saved trials are not a prefix of this experiment")
    elif output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory or --resume an identical experiment")
    output.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        write_json(manifest_path, manifest)
    for trial in trials[len(records):]:
        print(f"Running {trial.name}", flush=True)
        record = run_trial(trial, output, env_file, args.timeout_seconds)
        records.append(record)
        write_json(output / "comparison.json", {
            "timeout_seconds": args.timeout_seconds, "repetitions": args.repetitions,
            "runs": records, "summary": summarize_runs(records), "pairs": paired_comparisons(records),
            "limitation": "Annotation agreement is not semantic accuracy. Unmatched findings need expert adjudication. The comparison does not select the default mode.",
        })
        print(f"  {record['status']} ({record['elapsed_seconds']} s)", flush=True)
        if args.fail_fast and (record["status"] != "completed" or not record["structural_passed"]):
            return 1
    return 0 if all(record["status"] == "completed" and record["structural_passed"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

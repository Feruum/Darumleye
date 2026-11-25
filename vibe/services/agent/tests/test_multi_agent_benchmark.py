import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from scripts import compare_review_modes
from scripts.compare_review_modes import BenchmarkCase, experiment_matrix, paired_comparisons, score_expert_review, summarize_runs
from scripts.evaluate_spec_report import evaluate


def test_failed_trial_persists_only_allowlisted_provider_diagnostic(tmp_path, monkeypatch):
    trial = compare_review_modes.Trial(BenchmarkCase("example", tmp_path / "test.txt", None), "legacy", 1)
    label = compare_review_modes.STAGES["individual"]
    stderr = (
        f"[00:12] {label} — APIStatusError · HTTP 502\n"
        f"[00:13] {label} — PRIVATE_REASONING_TOKEN\n"
        f"{label} — AuthenticationError · HTTP 401\n"
        "provider response: PRIVATE_INPUT_CREDENTIAL\n"
        "Ошибка: AI-анализ не завершён или модель вернула некорректный результат. Отчёт не записан.\n"
    )
    monkeypatch.setattr(compare_review_modes, "implementation_fingerprint", lambda: "frozen")
    monkeypatch.setattr(compare_review_modes, "trial_settings", lambda *_: {})
    monkeypatch.setattr(compare_review_modes.subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 1, "PRIVATE_STDOUT", stderr))
    record = compare_review_modes.run_trial(trial, tmp_path, tmp_path / ".env", 900)
    assert record["failure_category"] == "review_failed"
    assert record["failed_stage"] == "individual"
    assert record["error_type"] == "APIStatusError"
    assert record["http_status"] == 502
    assert record["schema_error_codes"] == []
    assert "PRIVATE_" not in json.dumps(record)


def test_diagnostics_keep_latest_terminal_validation_and_allowed_schema_codes():
    extracted = compare_review_modes.STAGES["extraction"]
    reviewed = compare_review_modes.STAGES["set"]
    stderr = (
        f"[00:01] {extracted} — ValidationError · json_invalid\n"
        f"[00:02] {extracted} — ответ требует исправления; выполняется повтор\n"
        f"[00:03] {extracted} — запрос 2 · отправлен\n"
        f"[00:04] {extracted} — запрос 2 · ответ получен\n"
        f"[00:05] {reviewed} — ValidationError · missing, literal_error, PRIVATE_SCHEMA_CODE\n"
        f"[00:06] {reviewed} — проверка ответа не пройдена: The JSON schema is invalid.\n"
    )
    record = compare_review_modes.failure_diagnostics(stderr, exit_code=1)
    assert record == {
        "failure_category": "validation_failed",
        "failed_stage": "set",
        "error_type": "ValidationError",
        "http_status": None,
        "validation_reason": "The JSON schema is invalid.",
        "schema_error_codes": ["literal_error", "missing"],
    }


def test_rate_limit_and_deadline_choose_relevant_stage():
    label = compare_review_modes.STAGES["individual"]
    other = compare_review_modes.STAGES["set"]
    rate = compare_review_modes.failure_diagnostics(
        f"[00:04] {other} — ValidationError · json_invalid\n"
        f"[00:05] {other} — проверка ответа не пройдена: The JSON schema is invalid.\n"
        f"[00:06] {label} — RateLimitError · HTTP 429\n"
        "Ошибка: AI-провайдер ограничил запросы. Повторите запуск позже или используйте --wait-rate-limit.\n",
        exit_code=1,
    )
    assert (rate["failure_category"], rate["failed_stage"], rate["http_status"]) == ("rate_limited", "individual", 429)
    deadline = compare_review_modes.failure_diagnostics(
        f"[00:01] {other} — запрос 2 · отправлен\n"
        "Ошибка: истёк срок проверки. Попробуйте документ меньшего размера или увеличьте --timeout-seconds.\n",
        exit_code=1,
    )
    assert (deadline["failure_category"], deadline["failed_stage"]) == ("deadline", "set")
    timeout = compare_review_modes.failure_diagnostics(f"[00:05] {label} — APITimeoutError\n", exit_code=1)
    assert (timeout["failure_category"], timeout["error_type"]) == ("provider_timeout", "APITimeoutError")


def test_process_timeout_partial_stderr_preserves_safe_active_stage(tmp_path, monkeypatch):
    trial = compare_review_modes.Trial(BenchmarkCase("example", tmp_path / "test.txt", None), "legacy", 1)
    label = compare_review_modes.STAGES["set"]
    stderr = f"[00:00] {label} — запрос 1 · отправлен\nSECRET\n".encode()
    monkeypatch.setattr(compare_review_modes, "implementation_fingerprint", lambda: "frozen")
    monkeypatch.setattr(compare_review_modes, "trial_settings", lambda *_: {})
    def timeout(*_, **__):
        raise subprocess.TimeoutExpired(["secret"], 1260, output=b"PRIVATE_STDOUT", stderr=stderr)
    monkeypatch.setattr(compare_review_modes.subprocess, "run", timeout)
    record = compare_review_modes.run_trial(trial, tmp_path, tmp_path / ".env", 900)
    assert record["exit_code"] == 124
    assert record["failure_category"] == "process_timeout"
    assert record["failed_stage"] == "set"
    assert "SECRET" not in json.dumps(record)
    assert "PRIVATE" not in json.dumps(record)


def test_completed_trial_does_not_retain_recovered_errors():
    label = compare_review_modes.STAGES["individual"]
    diagnostic = compare_review_modes.failure_diagnostics(f"[00:01] {label} — APIStatusError · HTTP 502\n", exit_code=0)
    assert diagnostic["failure_category"] is None
    assert diagnostic["failed_stage"] is None
    assert diagnostic["error_type"] is None
    assert diagnostic["http_status"] is None


def test_experiment_matrix_alternates_modes_and_repeats_each_case():
    case = BenchmarkCase("example", Path("example.md"), None)
    matrix = experiment_matrix([case], 3)
    assert [(run.mode, run.repetition) for run in matrix] == [
        ("legacy", 1), ("multi_agent", 1), ("multi_agent", 2),
        ("legacy", 2), ("legacy", 3), ("multi_agent", 3),
    ]
    assert len({run.name for run in matrix}) == 6


def test_summary_keeps_failed_trials_and_missing_token_data_visible():
    runs = [
        {"case": "example", "mode": "legacy", "status": "completed", "structural_passed": True, "elapsed_seconds": 2.0, "usage": {"input_tokens": 4, "output_tokens": 3}},
        {"case": "example", "mode": "legacy", "status": "completed", "structural_passed": True, "elapsed_seconds": 4.0, "usage": None},
        {"case": "example", "mode": "multi_agent", "status": "failed", "structural_passed": False, "elapsed_seconds": 9.0},
    ]
    summary = summarize_runs(runs)
    legacy = summary["example"]["legacy"]
    assert legacy["completed"] == 2
    assert legacy["mean_elapsed_seconds"] == 3.0
    assert legacy["mean_input_tokens"] is None
    assert summary["example"]["multi_agent"]["failed"] == 1
    assert summary["example"]["multi_agent"]["mean_elapsed_seconds"] is None


def test_interrupted_matrix_resumes_after_failed_trial_with_same_configuration(tmp_path, monkeypatch):
    output = tmp_path / "evaluation"
    args = ["evaluate", "--output-dir", str(output), "--case", "defective", "--repetitions", "1"]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(compare_review_modes, "trial_settings", lambda _env, timeout: {"limit": timeout})
    calls = []

    def trial_result(trial, *_):
        calls.append(trial.name)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {
            "case": trial.case.name,
            "mode": trial.mode,
            "repetition": trial.repetition,
            "status": "failed",
            "structural_passed": False,
            "elapsed_seconds": 2,
        }

    monkeypatch.setattr(compare_review_modes, "run_trial", trial_result)
    with pytest.raises(KeyboardInterrupt):
        compare_review_modes.main()
    previous = json.loads((output / "comparison.json").read_text())["runs"]
    assert len(previous) == 1
    monkeypatch.setattr(sys, "argv", [*args, "--resume", "--timeout-seconds", "300"])
    with pytest.raises(SystemExit):
        compare_review_modes.main()
    monkeypatch.setattr(sys, "argv", [*args, "--resume"])
    assert compare_review_modes.main() == 1
    saved = json.loads((output / "comparison.json").read_text())["runs"]
    assert saved[:1] == previous and len(saved) == 2
    assert calls == ["defective-legacy-01", "defective-multi_agent-01", "defective-multi_agent-01"]


def test_pair_comparisons_do_not_treat_missing_usage_as_equal_budget():
    legacy = {"case": "example", "mode": "legacy", "repetition": 1, "status": "completed", "structural_passed": True, "elapsed_seconds": 2, "usage": {"input_tokens": 80, "output_tokens": 20}, "structural": {"model": "fixture"}, "quality": {"annotation_recall": 0.5}}
    multi = {**legacy, "mode": "multi_agent", "elapsed_seconds": 3, "quality": {"annotation_recall": 0.75}, "usage": {"input_tokens": 85, "output_tokens": 20}}
    pair = paired_comparisons([legacy, multi])[0]
    assert pair["tokens_within_10_percent"] is True
    assert pair["annotation_recall_delta"] == 0.25
    assert pair["same_model"] is True
    assert paired_comparisons([legacy, {**multi, "usage": None}])[0]["tokens_within_10_percent"] is None


def test_comparable_budget_requires_same_frozen_case_and_two_independent_runs():
    base = {"case": "a", "mode": "legacy", "repetition": 1, "status": "completed",
            "structural_passed": True, "elapsed_seconds": 1, "usage": {"input_tokens": 90, "output_tokens": 10},
            "implementation_fingerprint": "rev", "implementation_unchanged_during_trial": True,
            "trial_settings": {"model": "model", "analysis_limit_seconds": 900},
            "quality": {"annotation_recall": .5},
            "structural": {"model": "model", "source_sha256": "source", "run_id": "run-1"}}
    multi = {**base, "mode": "multi_agent", "structural": {**base["structural"], "run_id": "run-2"}}
    assert paired_comparisons([base, multi])[0]["eligible_for_equal_budget_analysis"]
    for different in ({"model": "other"}, {"source_sha256": "other"}, {"run_id": "run-1"}):
        record = {**multi, "structural": {**multi["structural"], **different}}
        assert not paired_comparisons([base, record])[0]["eligible_for_equal_budget_analysis"]
    for different in ({"implementation_unchanged_during_trial": False}, {"usage": None},
                      {"usage": {"input_tokens": 900, "output_tokens": 100}}, {"status": "failed"}):
        assert not paired_comparisons([base, {**multi, **different}])[0]["eligible_for_equal_budget_analysis"]


def test_expert_findings_are_hash_bound_and_duplicate_defects_count_once(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"passport": {"run_id": "run-1"}, "findings": [{}] * 5}))
    review = {"run_id": "run-1", "report_sha256": sha256(report.read_bytes()).hexdigest(),
              "findings": {"D-001": ["month"], "D-002": ["month"], "D-003": [], "D-005": None}}
    result = score_expert_review(report, review)
    assert result["unique_confirmed_defects"] == 1
    assert result["false_finding_ids"] == ["D-003"]
    assert result["unassessed_or_uncertain_finding_ids"] == ["D-004", "D-005"]
    assert not result["adjudication_complete"]
    for invalid in ({"run_id": "wrong"}, {"report_sha256": "wrong"}, {"findings": {"D-009": []}}):
        with pytest.raises(ValueError):
            score_expert_review(report, {**review, **invalid})


async def test_independent_audit_accepts_multi_agent_report_and_graph(tmp_path):
    from agent.services.spec_review import extract_specification, review_specification
    from test_spec_multi_agent import ExpertChat, SOURCE

    source = tmp_path / "requirements.txt"
    source.write_text(SOURCE)
    report = await review_specification(extract_specification(source.name, "text/plain", source.read_bytes()), ExpertChat(), review_mode="multi_agent")
    output = tmp_path / "report.json"
    output.write_text(report.model_dump_json())
    result = evaluate(source, output)
    assert result["passed"] == result["total"], [item for item in result["checks"] if not item["passed"]]
    assert result["metrics"]["graph_nodes"] > 0

    payload = json.loads(output.read_bytes())
    graph = payload["evidence_graph"]
    graph["edges"] = [
        edge for edge in graph["edges"]
        if (edge["source"], edge["kind"]) != ("D-001", "cites")
    ]
    del payload["readiness_checklist"]
    output.write_text(json.dumps(payload))
    rejected = evaluate(source, output)
    failures = {item["criterion"] for item in rejected["checks"] if not item["passed"]}
    assert "Graph links each finding to its reported requirements and citations" in failures
    assert "All readiness criteria exactly once and in order" in failures

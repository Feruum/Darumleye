"""CLI convenience and terminal progress without an external model."""

import json

import pytest
from langchain_core.messages import AIMessage

from agent import spec_review_cli as cli
from agent.services.spec_review import INDIVIDUAL_CRITERIA, SET_CRITERIA
from test_spec_review import check, expert_reply


class ControlledModel:
    model_name = "fixture"

    async def ainvoke(self, messages, **kwargs):
        data = json.loads(messages[-1].content)
        text = "REQ-1. Показывать баланс."
        if data["stage"] == "extraction":
            reply = {"requirements": [{"source_id": "REQ-1", "text": text,
                                       "evidence": {"start": 1, "end": 1, "quote": text}}]}
        elif data["stage"] == "individual":
            reply = {"requirements": [{"id": "R-001", "checks": [check(c) for c in INDIVIDUAL_CRITERIA]}],
                     "findings": []}
        elif data["stage"] == "set":
            quote = {"start": 1, "end": 1, "quote": text}
            reply = {"summary": "Проверено", "set_checks": [{**check(c), "evidence": [quote]} for c in SET_CRITERIA],
                     "findings": []}
        else:
            reply = expert_reply(data)
        return AIMessage(content=json.dumps(reply, ensure_ascii=False))


@pytest.fixture
def configured_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ChatOpenAI", lambda **kwargs: ControlledModel())
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-only-secret")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "http://fixture.invalid/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "fixture")
    monkeypatch.setenv("AGENT_SPEC_REVIEW_USE_NACE", "false")
    source = tmp_path / "requirements.md"
    source.write_text("REQ-1. Показывать баланс.\n", encoding="utf-8")
    return source, tmp_path / ".env"


def test_short_command_writes_markdown_and_shows_real_progress(configured_cli, capsys):
    source, env = configured_cli
    assert cli.main(["--review-mode", "legacy", str(source), "--env-file", str(env)]) == 0
    output = source.with_name("requirements.review.md")
    assert "## Итог" in output.read_text(encoding="utf-8")
    printed = capsys.readouterr()
    assert all(
        label in printed.err
        for label in ("Извлечение", "Индивидуальная", "Проверка набора")
    )
    assert "Нужна доработка" in printed.out  # No checklist assessment in the controlled model.
    assert str(output) in printed.out
    assert "test-only-secret" not in printed.out + printed.err


def test_default_output_never_overwrites_a_previous_review(configured_cli, capsys):
    source, env = configured_cli
    previous = source.with_name("requirements.review.md")
    previous.write_text("previous report", encoding="utf-8")
    assert cli.main(["--review-mode", "legacy", str(source), "--env-file", str(env), "--quiet"]) == 0
    new = source.with_name("requirements.review-2.md")
    assert previous.read_text() == "previous report"
    assert new.is_file()
    printed = capsys.readouterr()
    assert printed.err == ""
    assert printed.out.strip() == str(new)


def test_json_format_and_print_report_use_stdout_for_only_the_report(
    configured_cli, capsys
):
    source, env = configured_cli
    assert (
        cli.main(
            ["--review-mode", "legacy", str(source), "--format", "json", "--print-report", "--env-file", str(env)]
        )
        == 0
    )
    output = source.with_name("requirements.review.json")
    printed = capsys.readouterr()
    assert json.loads(printed.out) == json.loads(output.read_text())
    assert "Нужна доработка" in printed.err


def test_explicit_legacy_command_remains_supported(configured_cli, capsys):
    source, env = configured_cli
    output = source.parent / "custom.json"
    assert (
        cli.main(
            ["--review-mode", "legacy", 
                "--input",
                str(source),
                "--output",
                str(output),
                "--quiet",
                "--env-file",
                str(env),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text())["verdict"]["status"] == "needs_revision"
    assert capsys.readouterr().out.strip() == str(output)


def test_review_mode_cli_defaults_and_explicit_selection():
    parser = cli.build_parser()
    assert parser.parse_args(["requirements.md"]).review_mode == "multi_agent"
    assert parser.parse_args(["requirements.md", "--review-mode", "legacy"]).review_mode == "legacy"
    assert parser.parse_args(["requirements.md", "--review-mode", "multi_agent_direct"]).review_mode == "multi_agent_direct"
    assert parser.parse_args(["requirements.md", "--review-mode", "multi_agent"]).review_mode == "multi_agent"
    with pytest.raises(SystemExit):
        parser.parse_args(["requirements.md", "--review-mode", "unknown"])


@pytest.mark.parametrize(("mode", "model_calls"), [("legacy", 3), ("multi_agent_direct", 11), ("multi_agent", 14)])
def test_cli_usage_written_separately_without_inventing_provider_cost(configured_cli, capsys, mode, model_calls):
    source, env = configured_cli
    report = source.with_name("review.json")
    metrics = source.with_name("usage.json")
    assert cli.main(["--review-mode", mode, str(source), "--env-file", str(env), "--output", str(report), "--metrics-output", str(metrics), "--quiet"]) == 0
    data = json.loads(metrics.read_text())
    assert data["model_calls"] == model_calls
    assert data["input_tokens"] is None and data["output_tokens"] is None
    assert data["cost_usd"] is None
    assert json.loads(report.read_text())["passport"]["review_mode"] == mode
    assert capsys.readouterr().out.strip() == str(report)


def test_cli_metrics_output_cannot_overwrite_document(configured_cli, capsys):
    source, env = configured_cli
    original = source.read_bytes()
    assert cli.main(["--review-mode", "legacy", str(source), "--env-file", str(env), "--metrics-output", str(source), "--quiet"]) == 1
    assert source.read_bytes() == original
    assert "Файл метрик" in capsys.readouterr().err


@pytest.mark.parametrize("collision", ["manifest", "reference", "config"])
def test_metrics_sidecar_preserves_all_selected_inputs_on_failure(configured_cli, collision):
    source, env = configured_cli
    manifest = source.with_name("references.json")
    attachment = source.with_name("bank-policy.md")
    attachment.write_text("Approval requires two signatures.")
    manifest.write_text(json.dumps([{
        "path": attachment.name, "category": "bank_policy", "title": "Approval",
        "issuer": "Fixture", "version": "1", "scope": "Fixture", "source_url": None,
    }]))
    target = {"manifest": manifest, "reference": attachment, "config": env}[collision]
    original = target.read_bytes() if target.exists() else None
    code = cli.main(["--review-mode", "legacy", str(source), "--env-file", str(env), "--reference-manifest", str(manifest),
                     "--output", str(source.with_name("result.json")), "--metrics-output", str(target)])
    assert code == 1
    assert (target.read_bytes() if target.exists() else None) == original


def test_incompatible_output_format_is_rejected_without_touching_source(
    configured_cli, capsys
):
    source, env = configured_cli
    with pytest.raises(SystemExit) as error:
        cli.main(
            ["--review-mode", "legacy", 
                str(source),
                "--format",
                "json",
                "-o",
                str(source.parent / "report.md"),
                "--env-file",
                str(env),
            ]
        )
    assert error.value.code != 0
    assert source.read_text() == "REQ-1. Показывать баланс.\n"
    assert not (source.parent / "report.md").exists()


def test_provider_failure_does_not_write_report_or_leak_error_details(
    configured_cli, monkeypatch, capsys
):
    source, env = configured_cli

    class FailingModel(ControlledModel):
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("private-provider-details")

    monkeypatch.setattr(cli, "ChatOpenAI", lambda **kwargs: FailingModel())
    metrics = source.with_name("failed-usage.json")
    assert cli.main(["--review-mode", "legacy", str(source), "--env-file", str(env), "--metrics-output", str(metrics)]) == 1
    assert not source.with_name("requirements.review.md").exists()
    assert json.loads(metrics.read_text())["status"] == "failed"
    assert "private-provider-details" not in metrics.read_text()
    printed = capsys.readouterr()
    assert "Ошибка:" in printed.err
    assert "private-provider-details" not in printed.out + printed.err


@pytest.mark.parametrize("mode", ["legacy", "multi_agent_direct", "multi_agent"])
def test_checkpoint_resume_shows_saved_work_and_never_calls_model(
    configured_cli, monkeypatch, capsys, mode
):
    source, env = configured_cli
    checkpoint = source.parent / "checkpoints"
    command = [str(source), "--env-file", str(env), "--checkpoint-dir", str(checkpoint), "--format", "json"]
    assert cli.main([*command, "--review-mode", mode]) == 0
    capsys.readouterr()

    class NoCallsModel(ControlledModel):
        async def ainvoke(self, messages, **kwargs):
            pytest.fail("Accepted stages must be restored from the checkpoint")

    monkeypatch.setattr(cli, "ChatOpenAI", lambda **kwargs: NoCallsModel())
    assert cli.main([*command, "--review-mode", mode]) == 0
    assert "контрольная точка" in capsys.readouterr().err
    first = json.loads(source.with_name("requirements.review.json").read_text())
    resumed = json.loads(source.with_name("requirements.review-2.json").read_text())
    assert first["passport"]["run_id"] == resumed["passport"]["run_id"]


def test_missing_source_reports_actionable_error_without_output(tmp_path, capsys):
    assert cli.main(["--review-mode", "legacy", str(tmp_path / "missing.pdf"), "--quiet"]) == 1
    assert "входной файл не найден" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_default_cli_run_writes_multi_agent_graph(configured_cli, monkeypatch, capsys):
    from test_spec_multi_agent import ExpertChat, SOURCE

    source, env = configured_cli
    source.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(cli, "ChatOpenAI", lambda **kwargs: ExpertChat())
    assert cli.main([str(source), "--env-file", str(env), "--format", "json", "--quiet"]) == 0
    report = json.loads(source.with_name("requirements.review.json").read_text())
    assert report["passport"]["review_mode"] == "multi_agent"
    assert report["evidence_graph"]["nodes"]
    assert capsys.readouterr().err == ""

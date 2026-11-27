"""Final severity follows the supported consequence and its reach in the source."""

import pytest

from agent.services.spec_document import extract_specification
from agent.services.spec_multi_agent import SeverityBasis
from agent.services.spec_review import Evidence, review_specification
from test_spec_multi_agent import VariantChat


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lines,kind,proposed,effect,scope,expected",
    [
        (["При повторной печати оператор обязан сохранить макет.",
          "При повторной печати оператору запрещено сохранять макет."],
         "conflict", "critical", "incompatible_actions", "bounded_case", "high"),
        (["Каждое задание должно завершиться фиксацией итогового статуса.",
          "При получении квитанции устанавливают статус «Доставлено». При сбое квитанция отсутствует."],
         "missing", "medium", "missing_operational_result", "bounded_case", "high"),
        (["Система выбирает ровно один доступный контейнер с наименьшей загрузкой.",
          "Несколько контейнеров могут иметь равную загрузку. Выбор должен быть воспроизводим."],
         "missing", "high", "acceptance_detail", "bounded_case", "medium"),
        (["Оповещение должно появиться достаточно быстро, чтобы оператор не ждал."],
         "untestable", "high", "acceptance_detail", "bounded_case", "medium"),
        (["Клиент обязан получить остаток по интерфейсу сверки. Это единственный способ сверки.",
          "Все операции интерфейса сверки перечислены ниже. PUT создаёт запись."],
         "missing", "medium", "missing_operational_result", "whole_required_operation", "critical"),
        (["Публиковать итоговые решения можно только после финальной проверки.",
          "При ошибке проверки итоговое решение всё равно публикуется.",
          "Опубликованное решение безвозвратно прекращает все выплаты получателю."],
         "conflict", "high", "incompatible_actions", "severe_business_harm", "critical"),
        (["Подтверждённые платежи списываются немедленно. Такой же платёж можно списать повторно.",
          "Списания необратимы."],
         "conflict", "high", "incompatible_actions", "severe_business_harm", "critical"),
        (["В первой ссылке на действующую инструкцию напечатаны два пробела перед её названием."],
         "other", "medium", "editorial", "bounded_case", "low"),
    ],
    ids=["bounded-conflict", "missing-branch", "tie-detail", "local-quality",
         "whole-operation", "explicit-consequence", "short-complete-rule", "editorial"],
)
async def test_final_level_follows_cited_impact(lines, kind, proposed, effect, scope, expected):
    basis = {"effect": effect, "scope": scope, "scope_quote": lines[-1] if scope == "severe_business_harm" else lines[0]}
    report, calls = await run_case(lines, kind, proposed, basis)
    assert len(calls) == 1
    finding, = report.findings
    assert finding.severity == expected
    assert not finding.severity_provisional
    assert report.statistics.critical == int(expected == "critical")
    observations = [
        node for node in report.evidence_graph.nodes
        if node.kind == "observation" and node.finding_id == "D-001" and node.severity
    ]
    assert {node.severity for node in observations} == {proposed}


@pytest.mark.asyncio
@pytest.mark.parametrize("basis", [
    None,
    {"effect": "incompatible_actions"},
    {"effect": "incompatible_actions", "scope": "severe_business_harm",
     "scope_quote": "Все записи реестра при сбое неизбежно уничтожаются."},
    {"effect": "incompatible_actions", "scope": "whole_required_operation", "scope_quote": "передача"},
], ids=["missing-basis", "incomplete-basis", "invented-impact", "isolated-word"])
async def test_unsubstantiated_critical_cannot_erase_or_escalate_a_confirmed_conflict(basis):
    report, calls = await run_case(
        ["Для неполной заявки разрешена передача оператору.", "Для неполной заявки передача оператору запрещена."],
        "conflict", "critical", basis,
    )
    assert len(calls) == 1
    finding, = report.findings
    assert finding.severity == "high"
    assert finding.severity_provisional
    node, = [node for node in report.evidence_graph.nodes if node.kind == "finding"]
    assert "предварительн" in node.description.lower()


@pytest.mark.parametrize("fragment", ["при", "обмен", "...", "по правилу при"])
def test_short_excerpt_must_support_broader_scope_in_the_original_source(fragment):
    line = "Правила применяют пока идёт обмен... При завершении действуют по правилу приёмки."
    spec = extract_specification("rules.txt", "text/plain", line.encode())
    evidence = [Evidence(start=1, end=1, quote=fragment)]
    basis = SeverityBasis(effect="incompatible_actions", scope="severe_business_harm", scope_quote=fragment)
    assert not basis.grounded(evidence, spec)


async def run_case(lines, kind, proposed, basis):
    spec = extract_specification("workflow.txt", "text/plain", "\n".join(lines).encode())
    evidence = [{"start": i, "end": i, "quote": line} for i, line in enumerate(lines, 1)]
    calls = []

    def variant(data, result):
        stage = data["stage"]
        if stage == "extraction":
            result["requirements"] = [
                {"source_id": None, "text": line, "evidence": item}
                for line, item in zip(lines, evidence, strict=True)
            ]
        elif stage.endswith("_set"):
            for row in [*result["set_checks"], *result["readiness_checklist"]]:
                row["evidence"] = evidence[:1]
            result["findings"] = [{
                "kind": kind, "severity": proposed, "issue": "Не согласован результат проверки.",
                "recommendation": "Согласовать применимое правило.", "evidence": evidence,
                "requirement_ids": ["R-001"], "criteria": ["Согласованность"],
            }] if data["role"] == "logic_inspector" else []
        elif stage == "multi_adjudicate":
            calls.append(result)
            row, = result["decisions"]
            row.update(evidence=evidence, severity=proposed, severity_basis=basis)

    report = await review_specification(spec, VariantChat(variant), review_mode="multi_agent_direct")
    return report, calls

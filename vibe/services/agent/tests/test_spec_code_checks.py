"""Deterministic source checks and bounded, complete individual review."""

import asyncio
from types import SimpleNamespace

import pytest

from agent.services import spec_review as review
from agent.services.spec_document import Specification


def test_near_duplicate_findings_preserve_numeric_and_negation_distinctions():
    evidence = [review.Evidence(start=1, end=1, quote="Порог и условия уведомления")]
    def finding(issue):
        return review.Finding(kind="ambiguous", severity="medium", issue=issue,
            recommendation="Уточнить условие.", evidence=evidence, requirement_ids=["R-001"])
    original = finding("Не определено условие отправки уведомления после 10 минут ожидания.")
    paraphrase = finding("Не определено условие отправки уведомления после 10 минут ожидания")
    changed_number = finding("Не определено условие отправки уведомления после 20 минут ожидания.")
    changed_negation = finding("Не определено условие не отправки уведомления после 10 минут ожидания.")
    assert len(review.deduplicate_findings([original, paraphrase, changed_number, changed_negation])) == 3


def test_same_generic_issue_at_distinct_requirements_is_not_merged():
    def finding(location, req):
        return review.Finding(kind="missing", severity="medium", issue="Не определён срок операции.",
            recommendation=f"Уточнить срок для {req}.", evidence=[review.Evidence(start=location, end=location, quote=f"Операция {req}")], requirement_ids=[req])
    assert len(review.deduplicate_findings([finding(1, "R-001"), finding(2, "R-002")])) == 2


async def test_acceptance_examples_are_checked_against_business_rules(monkeypatch):
    lines = ["RULE-1. Месяц определяется датой события.", "### Критерии приёмки", "| Условие | Действие | Ожидаемый результат |", "|---|---|---|", "| Событие 31 января | Обработка 1 февраля | Февральский итог |"]
    spec = Specification("case.md", "md", lines)
    observed = []
    def evidence(index):
        return {"start": index, "end": index, "quote": lines[index - 1]}
    async def invoke(_llm, stage, _instructions, data, output):
        observed.append(stage)
        if stage == "extraction":
            return output.model_validate({"requirements": [{"source_id": "RULE-1", "text": lines[0], "evidence": evidence(1)}, {"source_id": None, "text": lines[-1], "evidence": evidence(5)}]})
        if stage == "set":
            return output.model_validate({"summary": "Проверено", "set_checks": [dict(criterion=c, status="pass", comment="Проверено", evidence=[]) for c in review.SET_CRITERIA], "findings": []})
        if stage == "individual":
            return output.model_validate({"requirements": [{"id": item["id"], "checks": [dict(criterion=c, status="pass", comment="Проверено") for c in review.INDIVIDUAL_CRITERIA]} for item in data["requirements"]], "findings": []})
        assert stage == "acceptance_alignment"
        assert any(item["index"] == 5 for item in data["acceptance_units"])
        return output.model_validate({"findings": [dict(kind="conflict", severity="high", issue="Дата события против месяца обработки.", recommendation="Согласовать дату учёта.", evidence=[evidence(1), evidence(5)], requirement_ids=["R-001", "R-002"], criteria=["Согласованность"])]})
    monkeypatch.setattr(review, "invoke_stage", invoke)
    report = await review.review_specification(spec, SimpleNamespace(model_name="fixture"), review_mode="legacy")
    assert observed.count("acceptance_alignment") == 1
    assert len(report.findings) == 1
    assert report.findings[0].kind == "conflict"
    assert next(item for item in report.set_checks if item.criterion == "Согласованность").status == "fail"
    assert report.verdict.status == "not_ready"


def requirement(
    identifier: int,
    text: str,
    quote: str,
    start: int,
    end: int | None = None,
    source_id: str | None = None,
) -> review.Requirement:
    return review.Requirement(
        id=f"R-{identifier:03d}",
        text=text,
        source_id=source_id,
        evidence=review.Evidence(start=start, end=end or start, quote=quote),
        checks=[],
    )


def checks(criteria):
    return [
        {"criterion": criterion, "status": "pass", "comment": "Проверено."}
        for criterion in criteria
    ]


def test_duplicate_requirements_and_reused_declared_ids_are_source_grounded():
    lines = [
        "PAY-1. Уведомить клиента об отказе.",
        "PAY-2: Уведомить клиента об отказе.",
        "PAY-1. Сформировать отчёт.",
    ]
    spec = Specification("payments.md", "md", lines)
    requirements = [
        requirement(1, "Уведомить клиента.", lines[0], 1, source_id="PAY-1"),
        requirement(2, "Сообщить об отказе.", lines[1], 2, source_id="PAY-2"),
        requirement(3, "Сформировать отчёт.", lines[2], 3, source_id="PAY-1"),
    ]

    findings = review.review_code_checks(spec, requirements)

    assert len(findings) == 2
    assert {tuple(finding.requirement_ids) for finding in findings} == {
        ("R-001", "R-002"),
        ("R-001", "R-003"),
    }
    for finding in findings:
        assert len(finding.evidence) == 2
        assert finding.evidence[0].end < finding.evidence[1].start
        for evidence in finding.evidence:
            spec.verify_quote(evidence.start, evidence.end, evidence.quote)
    assert review.review_code_checks(spec, requirements) == findings


def test_repeat_checks_are_conservative_about_overlap_references_and_paraphrases():
    lines = [
        "REQ-1. Уведомить клиента и сохранить отчёт.",
        "Следовать правилу REQ-1 при отказе.",
        "REQ-10. Внести отчёт в историю.",
        "REQ-11. Записать отчёт в историю.",
        "REQ-12: -100 тенге.",
        "REQ-13: 100 тенге.",
    ]
    spec = Specification("payments.md", "md", lines)
    requirements = [
        requirement(1, "Уведомить клиента.", lines[0], 1, source_id="REQ-1"),
        requirement(2, "Сохранить отчёт.", lines[0], 1, source_id="REQ-1"),
        requirement(3, "Следовать правилу.", lines[1], 2, source_id="REQ-1"),
        requirement(4, "Сохранить отчёт.", lines[2], 3, source_id="REQ-10"),
        requirement(5, "Сохранить отчёт.", lines[3], 4, source_id="REQ-11"),
        requirement(6, "Указать отрицательную сумму.", lines[4], 5, source_id="REQ-12"),
        requirement(7, "Указать положительную сумму.", lines[5], 6, source_id="REQ-13"),
    ]

    assert review.review_code_checks(spec, requirements) == []


def test_code_checks_do_not_treat_id_fragments_as_requirement_text():
    lines = [
        "REQ-1. Уведомить клиента.",
        "REQ-1.2 Указать комиссию.",
        "REQ-2. Отправить отчёт.",
        "REQ-2. Записать результат.",
    ]
    source = Specification("payments.md", "md", lines)
    requirements = [
        requirement(1, "Уведомить клиента.", lines[0], 1, source_id="REQ-1"),
        # The extractor can verify a source_id substring, but code may only
        # identify an independently declared ID, not the start of a longer ID.
        requirement(2, "Указать комиссию.", lines[1], 2, source_id="REQ-1"),
        requirement(3, "Отправить отчёт.", "REQ-2", 3, source_id="REQ-2"),
        requirement(4, "Записать результат.", "REQ-2", 4, source_id="REQ-2"),
    ]
    assert review.review_code_checks(source, requirements) == []


async def test_code_findings_reach_set_stage_and_prevent_false_ready_verdict(
    monkeypatch,
):
    lines = [
        "PAY-1. Уведомить клиента.",
        "PAY-2. Уведомить клиента.",
        "PAY-1. Сформировать отчёт.",
    ]
    source = Specification("payments.md", "md", lines)
    requirements = [
        requirement(1, "Уведомить клиента.", lines[0], 1, source_id="PAY-1"),
        requirement(2, "Уведомить клиента.", lines[1], 2, source_id="PAY-2"),
        requirement(3, "Сформировать отчёт.", lines[2], 3, source_id="PAY-1"),
    ]
    set_contexts = []

    async def invoke(llm, stage, instructions, data, output):
        if stage == "extraction":
            return output.model_validate(
                {
                    "requirements": [
                        item.model_dump(exclude={"id", "checks"})
                        for item in requirements
                    ]
                }
            )
        if stage == "individual":
            return output.model_validate(
                {
                    "requirements": [
                        {"id": item["id"], "checks": checks(review.INDIVIDUAL_CRITERIA)}
                        for item in data["requirements"]
                    ],
                    "findings": [],
                }
            )
        assert stage == "set"
        set_contexts.append(data)
        return output.model_validate(
            {
                "summary": "Все требования проверены.",
                "set_checks": [
                    {**item, "evidence": []} for item in checks(review.SET_CRITERIA)
                ],
                "findings": [],
            }
        )

    monkeypatch.setattr(review, "invoke_stage", invoke)
    report = await review.review_specification(
        source, SimpleNamespace(model_name="fixture"), review_mode="legacy"
    )

    assert len(set_contexts[0]["code_findings"]) == 2
    assert report.statistics.violations == 2
    assert report.statistics.needs_revision == 3
    assert report.verdict.status != "ready"
    set_checks = {item.criterion: item for item in report.set_checks}
    assert set_checks["Отсутствие дублирования"].status == "fail"
    assert set_checks["Связность"].status == "fail"
    assert all(
        set_checks[key].evidence for key in ("Отсутствие дублирования", "Связность")
    )


def batch_source(count):
    lines = [f"Правило {i}." for i in range(1, count + 1)]
    return Specification("rules.md", "md", lines), [
        requirement(i, line, line, i) for i, line in enumerate(lines, 1)
    ]


async def test_individual_batches_are_concurrent_bounded_and_ordered(monkeypatch):
    source, requirements = batch_source(24)
    started = []
    completed = []
    active = 0
    peak = 0
    release = asyncio.Event()

    async def invoke(llm, stage, instructions, data, output):
        nonlocal active, peak
        assert stage == "individual"
        batch = data["requirements"]
        first = batch[0]["id"]
        started.append(first)
        active += 1
        peak = max(peak, active)
        if len(started) == 3:
            release.set()
        try:
            await release.wait()
            # Finish batches out of order to distinguish execution and output.
            await asyncio.sleep({"R-001": 0.03, "R-007": 0.01}.get(first, 0))
            completed.append(first)
            return output.model_validate(
                {
                    "requirements": [
                        {"id": item["id"], "checks": checks(review.INDIVIDUAL_CRITERIA)}
                        for item in batch
                    ],
                    "findings": [
                        {
                            "kind": "other",
                            "severity": "low",
                            "issue": f"Замечание {first}.",
                            "recommendation": "Уточнить.",
                            "evidence": [batch[0]["evidence"]],
                            "requirement_ids": [first],
                        }
                    ],
                }
            )
        finally:
            active -= 1

    monkeypatch.setattr(review, "invoke_stage", invoke)
    findings = await asyncio.wait_for(
        review.review_individually(source, requirements, SimpleNamespace(), gate=asyncio.Semaphore(3)), timeout=1
    )

    assert peak == 3
    assert completed != started
    assert [finding.requirement_ids[0] for finding in findings] == [
        "R-001",
        "R-007",
        "R-013",
        "R-019",
    ]
    assert all(
        len(item.checks) == len(review.INDIVIDUAL_CRITERIA) for item in requirements
    )


async def test_individual_batch_failure_cancels_siblings(monkeypatch):
    source, requirements = batch_source(18)
    started = []
    cancelled = []
    all_started = asyncio.Event()

    async def invoke(llm, stage, instructions, data, output):
        first = data["requirements"][0]["id"]
        started.append(first)
        if len(started) == 3:
            all_started.set()
        try:
            await all_started.wait()
            if first == "R-007":
                raise ValueError("fixture batch failure")
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(first)
            raise

    monkeypatch.setattr(review, "invoke_stage", invoke)
    with pytest.raises(ValueError, match="fixture batch failure"):
        await asyncio.wait_for(
            review.review_individually(source, requirements, SimpleNamespace()),
            timeout=1,
        )
    assert set(cancelled) == {"R-001", "R-013"}


async def test_extraction_chunks_are_bounded_and_source_order_is_stable(monkeypatch):
    lines = [f"Правило {i}. " + "а" * 2600 for i in range(1, 5)]
    spec = Specification("large.md", "md", lines)
    started = []
    finished = []
    active = 0
    peak = 0
    release = asyncio.Event()

    async def invoke(llm, stage, instructions, data, output):
        nonlocal active, peak
        assert stage == "extraction"
        index = data["primary_start"]
        assert index == data["primary_end"]
        started.append(index)
        active += 1
        peak = max(peak, active)
        if len(started) == 3:
            release.set()
        try:
            await release.wait()
            await asyncio.sleep({1: 0.03, 2: 0.01}.get(index, 0))
            finished.append(index)
            return output.model_validate(
                {
                    "requirements": [
                        {
                            "source_id": None,
                            "text": f"Правило {index}.",
                            "evidence": {
                                "start": index,
                                "end": index,
                                "quote": f"Правило {index}.",
                            },
                        }
                    ]
                }
            )
        finally:
            active -= 1

    monkeypatch.setattr(review, "invoke_stage", invoke)
    requirements = await asyncio.wait_for(
        review.extract_requirements(spec, SimpleNamespace(), concurrency=3), timeout=1
    )
    assert peak == 3
    assert finished != started
    assert [(item.id, item.evidence.start) for item in requirements] == [
        (f"R-{index:03d}", index) for index in range(1, 5)
    ]


async def test_invalid_individual_citation_gets_one_correction_then_requires_exact_quote(
    monkeypatch,
):
    source, requirements = batch_source(1)
    attempts = []

    async def invoke(llm, stage, instructions, data, output):
        attempts.append(instructions)
        batch = data["requirements"]
        evidence = dict(batch[0]["evidence"])
        if len(attempts) == 1:
            evidence["quote"] = "invented source text"
        return output.model_validate(
            {
                "requirements": [
                    {"id": batch[0]["id"], "checks": checks(review.INDIVIDUAL_CRITERIA)}
                ],
                "findings": [
                    {
                        "kind": "other",
                        "severity": "low",
                        "issue": "Учебное замечание.",
                        "recommendation": "Уточнить.",
                        "evidence": [evidence],
                        "requirement_ids": [batch[0]["id"]],
                    }
                ],
            }
        )

    monkeypatch.setattr(review, "invoke_stage", invoke)
    results = await review.review_individually(source, requirements, SimpleNamespace())
    assert len(attempts) == 2
    assert "correction" in attempts[1].lower()
    assert results[0].evidence[0] == requirements[0].evidence


async def test_configurable_review_concurrency():
    active = 0
    peak = 0

    async def worker(value):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return value

    assert await review.bounded_review_map([1, 2, 3, 4], worker, concurrency=2) == [
        1,
        2,
        3,
        4,
    ]
    assert peak == 2

from types import SimpleNamespace

from agent.services.spec_document import Specification
from agent.services.spec_review import (
    Evidence,
    Requirement,
    INDIVIDUAL_CRITERIA,
    SET_CRITERIA,
    review_core,
)
from agent.services.spec_review_context import is_large_document, set_scopes


def document_and_requirements():
    texts = [
        "Дневной лимит оплаты равен 100 000 тенге.",
        *[f"Раздел {i}: независимая операция." for i in range(1, 5)],
        "Дневной лимит оплаты равен 150 000 тенге.",
    ]
    units = [text + "\n" + "текст " * 850 for text in texts]
    specification = Specification("long.txt", "txt", units)
    requirements = [
        Requirement(
            id=f"R-{index:03d}",
            source_id=None,
            text=text,
            evidence=Evidence(start=index, end=index, quote=text),
            checks=[],
        )
        for index, text in enumerate(texts, 1)
    ]
    return specification, requirements


def test_long_set_coverage_includes_every_source_unit_and_distant_requirements():
    specification, requirements = document_and_requirements()
    assert is_large_document(specification)
    scopes = set_scopes(specification, requirements)
    covered = {
        unit["index"]
        for scope in scopes
        if scope.kind == "section"
        for unit in scope.units
    }
    assert covered == set(range(1, len(specification.units) + 1))
    assert any(
        scope.kind == "cross_section"
        and {requirements[0].id, requirements[-1].id}
        <= {item.id for item in scope.requirements}
        for scope in scopes
    )


async def test_long_document_comparison_keeps_cross_section_conflict_and_original_citations(
    monkeypatch,
):
    from agent.services import spec_review

    specification, requirements = document_and_requirements()
    calls = []

    async def model(_llm, stage, _instructions, data, output):
        calls.append((stage, data))
        if stage == "individual":
            return output.model_validate(
                {
                    "requirements": [
                        {
                            "id": item["id"],
                            "checks": [
                                {
                                    "criterion": criterion,
                                    "status": "pass",
                                    "comment": "Проверено.",
                                }
                                for criterion in INDIVIDUAL_CRITERIA
                            ],
                        }
                        for item in data["requirements"]
                    ],
                    "findings": [],
                }
            )
        conflict = data.get("scope") == "cross_section"
        findings = (
            [
                {
                    "kind": "conflict",
                    "severity": "high",
                    "issue": "Два несовместимых дневных лимита.",
                    "recommendation": "Согласовать дневной лимит.",
                    "evidence": [
                        requirements[0].evidence.model_dump(),
                        requirements[-1].evidence.model_dump(),
                    ],
                    "requirement_ids": [requirements[0].id, requirements[-1].id],
                }
            ]
            if conflict
            else []
        )
        return output.model_validate(
            {
                "summary": "Проверен блок.",
                "set_checks": [
                    {
                        "criterion": criterion,
                        "status": "fail"
                        if conflict and criterion == "Согласованность"
                        else "pass",
                        "comment": "Проверено.",
                        "evidence": [],
                    }
                    for criterion in SET_CRITERIA
                ],
                "findings": findings,
            }
        )

    monkeypatch.setattr(spec_review, "invoke_stage", model)
    result = await review_core(
        specification,
        requirements,
        SimpleNamespace(model_name="fixture"),
        "2026-11-25T00:00:00Z",
        "test-run",
    )
    assert result.statistics.requirements == len(requirements)
    assert result.verdict.status == "not_ready"
    assert result.findings[0].evidence[0].start == 1
    assert result.findings[0].evidence[1].start == 6
    assert any(
        stage == "set" and data.get("scope") == "cross_section" for stage, data in calls
    )
    assert all(
        len(data["units"]) < len(specification.units)
        for stage, data in calls
        if stage == "individual"
    )
    assert "не является полным перебором" in result.summary

import pytest

from agent.services.spec_multi_agent import AdjudicationDecision, validate_conflict_resolution
from agent.services.spec_review import extract_specification, review_specification
from test_spec_multi_agent import VariantChat


FIRST = "A-1. При остановке линии проход запрещён всем, включая дежурную бригаду."
SECOND = "A-2. При остановке линии дежурную бригаду обязаны пропустить."
DEFAULT = "Правила действуют, если иными условиями не предусмотрено иное."


def rejected(source, *, basis, clause, term=None, priority=None,
             case="Дежурная бригада запрашивает проход при остановке линии."):
    resolution = dict(case=case, first_rule=source[0], second_rule=source[1], basis=basis,
                      decisive_clause=clause, operative_term=term)
    if priority is not None:
        resolution["priority"] = priority
    return dict(id="O-1", status="unsupported", rationale="Условия источника разрешают ситуацию.",
                evidence=[dict(start=i, end=i, quote=text) for i, text in enumerate(source, 1)],
                conflict_resolution=resolution)


@pytest.mark.asyncio
async def test_retry_groundless_exception_in_normal_review():
    source = [FIRST, SECOND, DEFAULT]
    invalid = rejected(source, basis="explicit_exception", clause=DEFAULT, term="не предусмотрено иное")
    evidence = invalid["evidence"]
    attempts = []

    def variant(data, result):
        stage = data["stage"]
        if stage == "extraction":
            result["requirements"] = [
                {"source_id": f"A-{i}", "text": item["quote"], "evidence": item}
                for i, item in enumerate(evidence[:2], 1)
            ]
        elif stage.endswith("_set"):
            for check in [*result["set_checks"], *result["readiness_checklist"]]:
                check["evidence"] = [evidence[0]]
            result["findings"] = [{
                "kind": "conflict", "severity": "high", "issue": "Дежурной бригаде одновременно запрещён и обязателен проход.",
                "recommendation": "Установить результат для дежурной бригады.",
                "evidence": evidence[:2], "requirement_ids": ["R-001", "R-002"], "criteria": ["Согласованность"],
            }] if data["role"] == "logic_inspector" else []
        elif stage == "multi_adjudicate":
            row, = data["findings"]
            attempts.append(row["id"])
            if len(attempts) == 1:
                result["decisions"] = [{**invalid, "id": row["id"]}]
            else:
                result["decisions"] = [dict(id=row["id"], status="confirmed", severity="high",
                    rationale="Общая оговорка не разрешает противоположные требования к проходу дежурной бригады.",
                    evidence=evidence[:2])]

    spec = extract_specification("access.txt", "text/plain", "\n".join(source).encode())
    report = await review_specification(spec, VariantChat(variant), review_mode="multi_agent_direct")
    assert len(attempts) == 2
    assert len(report.findings) == 1
    assert report.findings[0].kind == "conflict"


@pytest.mark.parametrize(("basis", "clause", "operative", "applies", "supersedes", "valid"), [
    ("explicit_precedence", DEFAULT, "предусмотрено иное", "A-2", "A-1", False),
    ("explicit_precedence", "A-20 имеет приоритет над A-1.", "имеет приоритет над", "A-2", "A-1", False),
    ("explicit_precedence", "ALT-A-2 имеет приоритет над A-1.", "имеет приоритет над", "A-2", "A-1", False),
    ("explicit_precedence", "K.A-2 имеет приоритет над A-1.", "имеет приоритет над", "A-2", "A-1", False),
    ("explicit_precedence", "A-2.3 имеет приоритет над A-1.", "имеет приоритет над", "A-2", "A-1", False),
    ("explicit_precedence", "Порядок. (A-2) имеет приоритет над A-1.", "имеет приоритет над", "A-2", "A-1", True),
    ("explicit_precedence", "A-2 имеет приоритет над A-1.", "имеет приоритет над", "A-2", "A-1", True),
    ("explicit_exception", "Как исключение из A-1 дежурную бригаду обязаны пропустить.", "Как исключение из",
     "дежурную бригаду обязаны пропустить", "A-1", True),
], ids=["generic-proviso", "different-rule-id", "hyphen-prefix", "dot-prefix", "dot-suffix",
        "sentence-punctuation", "explicit-precedence-by-id", "explicit-scoped-exception"])
def test_resolution_binds_both_premises_to_governing_clause(basis, clause, operative, applies, supersedes, valid):
    decision = rejected([FIRST, SECOND, clause], basis=basis, clause=clause, term=operative, priority={
        "prevailing_rule": "second_rule", "prevailing_anchor": applies, "displaced_anchor": supersedes,
    })
    row = AdjudicationDecision.model_validate(decision)
    if valid:
        validate_conflict_resolution(row)
    else:
        with pytest.raises(ValueError, match="Bind the governing clause"):
            validate_conflict_resolution(row)


def test_distinct_scopes_need_no_priority():
    second = "A-2. При нормальной работе проход дежурной бригады разрешён."
    decision = rejected([FIRST, second], basis="different_scope", clause=second)
    validate_conflict_resolution(AdjudicationDecision.model_validate(decision))


def test_inline_carveout_can_use_scope_and_action_instead_of_rule_ids():
    general = "Сервис возвращает ошибку 400, кроме запросов чужой карты."
    special = "Для запросов чужой карты сервис возвращает ошибку 404."
    decision = rejected([general, special], case="Пользователь запрашивает чужую карту.",
                        basis="explicit_exception", clause=general,
                        term="кроме запросов чужой карты", priority={
                            "prevailing_rule": "second_rule", "prevailing_anchor": "запросов чужой карты",
                            "displaced_anchor": "ошибку 400",
                        })
    validate_conflict_resolution(AdjudicationDecision.model_validate(decision))

from agent.services.spec_document import Specification
from agent.services.spec_multi_agent import Group, Observation, _merge_candidates
from agent.services.spec_review import Evidence, Finding, Requirement
from agent.services.spec_review_context import set_scopes


def test_conflict_subcase_compares_two_source_rules_when_experts_quote_different_clauses():
    permission = Evidence(start=10, end=10, quote="Заявителю доступно изменение заказа ещё 12 часов.")
    lock = Evidence(start=40, end=40, quote="В момент закрытия заказ блокируется.")
    refuse = Evidence(start=40, end=40, quote="После закрытия новые версии не сохраняются.")
    narrow = Finding(kind="conflict", severity="high",
                     issue="Неясно, можно ли изменить заказ ещё 12 часов после закрытия.",
                     recommendation="Согласовать момент закрытия заказа.",
                     evidence=[permission, lock], requirement_ids=["R-001"])
    wide = narrow.model_copy(update={
        "issue": "Изменение заказа доступно 12 часов, хотя закрытие блокирует все версии.",
        "requirement_ids": ["R-004", "R-005"], "evidence": [permission, refuse]})
    left = Group([Observation("O-01", "logic_inspector", narrow)])
    right = Group([Observation("O-02", "contract_inspector", wide)])
    assert _merge_candidates([left, right]) == [("O-01", "O-02")]

    unrelated = wide.model_copy(update={"evidence": [permission, refuse.model_copy(update={"start": 55, "end": 55})]})
    assert not _merge_candidates([left, Group([Observation("O-03", "scenario_builder", unrelated)])])


def test_cross_section_review_fetches_original_referenced_rule_before_lexically_similar_neighbors():
    target = "EX-AB"
    units = ["Контакт доступен согласно EX-AB."] + ["Контакт доступен согласно регламенту."] * 60
    units.append("EX-AB. Перед публикацией заявитель отзывает согласие.")
    spec = Specification("example.md", "md", units)
    rows = [Requirement(id=f"R-{i:03}", source_id=target if i == len(units) else None,
                        text=text, evidence=Evidence(start=i, end=i, quote=text), checks=[])
            for i, text in enumerate(units, 1)]
    scope = next(s for s in set_scopes(spec, rows) if s.kind == "cross_section" and rows[0].id in s.primary_ids)
    assert rows[-1] in scope.requirements
    assert any(u["index"] == len(rows) for u in scope.units)


def test_recheck_prioritizes_a_distant_exception_with_a_shared_rule_reference():
    from agent.services.spec_document import Specification
    from agent.services.spec_review import Evidence, Finding
    from agent.services.spec_multi_agent import recheck_source_context

    original = "REQ-101. Курьер обязан получить подпись получателя."
    exception = "Исключение к REQ-101. При бесконтактной передаче подпись заменяет код."
    units = [original, *["Перечень сведений для сверки. " * 14] * 100, exception]
    source = Specification(filename="delivery.md", source="md", units=units)
    finding = Finding(kind="missing", severity="high", issue="Не определено исключение при передаче.",
                      requirement_ids=[], evidence=[Evidence(start=1, end=1, quote=original)], recommendation="Уточнить правило.")
    context = recheck_source_context(source, finding, [])
    assert context["partial"]
    assert any(unit["index"] == len(units) and unit["text"] == exception for unit in context["units"])

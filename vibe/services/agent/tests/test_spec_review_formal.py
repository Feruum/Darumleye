import pytest

from agent.services.spec_document import Specification
from agent.services.spec_review import Evidence, Requirement
from agent.services.spec_review_facts import build_fact_index
from agent.services.spec_review_formal import build_clause_index, verify_rule_conflicts, select_semantic_context


def fixture(lines):
    source = Specification("sample.md", "md", lines)
    reqs = [Requirement(id=f"R-{i:03d}", source_id=None, text=line,
                        evidence=Evidence(start=i, end=i, quote=line), checks=[])
            for i, line in enumerate(lines, 1)]
    return source, reqs


def test_clause_index_includes_exact_entities_conditions_and_outcomes():
    lines = ["Счёт со статусом «Проверен» утверждается автоматически.",
             "Если срок истёк, заявка отклоняется.",
             "Поле `invoiceId` содержит идентификатор счёта."]
    source, reqs = fixture(lines)
    facts = build_clause_index(source, reqs)
    assert any(f.kind == "entity" and f.quote == "Счёт" for f in facts)
    assert any(f.kind == "condition" and f.value == "проверен" for f in facts)
    assert any(f.kind == "condition" and "срок истёк" in f.quote for f in facts)
    assert any(f.kind == "outcome" and f.value == "approved" for f in facts)
    assert any(f.kind == "interface_field" and f.value == "invoiceId" for f in facts)
    for fact in facts:
        source.verify_quote(fact.start, fact.end, fact.quote)


def test_z3_proves_shared_inclusive_numeric_boundary_with_incompatible_outcomes():
    lines = ["Счёт со статусом «Проверен» и суммой от 0 до 50 000 тенге включительно утверждается автоматически.",
             "Счёт со статусом «Проверен» и суммой ровно 5 000 000 тиын отклоняется автоматически; исключений нет."]
    source, reqs = fixture(lines)
    numeric = build_fact_index(source, reqs)
    witnesses = verify_rule_conflicts(source, reqs, numeric)
    assert len(witnesses) == 1
    witness = witnesses[0]
    assert witness.status == "overlap_and_incompatible_outcomes"
    assert witness.inputs == {"status": "проверен", "value": "5000000", "unit": "KZT_minor"}
    assert {witness.left_outcome, witness.right_outcome} == {"approved", "rejected"}
    assert witness.requirement_ids == ("R-001", "R-002")
    context = select_semantic_context(build_clause_index(source, reqs), witnesses, {"R-001", "R-002"})
    assert context["formal_witnesses"][0]["inputs"]["value"] == "5000000"
    assert not select_semantic_context(build_clause_index(source, reqs), witnesses, {"R-001"})["formal_witnesses"]


@pytest.mark.parametrize("other", [
    "Счёт со статусом «Черновик» и суммой ровно 50 000 тенге отклоняется автоматически.",
    "Счёт со статусом «Проверен» и суммой ровно 50 000 тенге утверждается автоматически.",
    "Счёт со статусом «Проверен» и суммой ровно 50 000 тенге отклоняется автоматически, если владелец отказался.",
])
def test_distinct_conditions_same_outcome_and_unparsed_exception_are_not_formal_conflicts(other):
    source, reqs = fixture(["Счёт со статусом «Проверен» и суммой от 0 до 50 000 тенге включительно утверждается автоматически.", other])
    assert not verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))


def test_z3_verifies_logical_conflict_without_numeric_assumptions():
    source, reqs = fixture(["Заявка со статусом «Подана» утверждается автоматически.",
                           "Заявка со статусом «Подана» отклоняется автоматически."])
    witnesses = verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))
    assert witnesses[0].inputs == {"status": "подана"}


def test_not_explicitly_inclusive_range_does_not_prove_endpoint_conflict():
    source, reqs = fixture(["Счёт со статусом «Проверен» и суммой от 0 до 50 000 тенге утверждается автоматически.",
                           "Счёт со статусом «Проверен» и суммой ровно 50 000 тенге отклоняется автоматически."])
    assert not verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))


def test_explicit_count_guards_share_an_integer_boundary_across_sections():
    lines = ["Если количество мест не менее 12, заявка одобряется.",
             "Раздел приёмки.", "Если количество мест не более 12, заявка отклоняется."]
    source, reqs = fixture(lines)
    witnesses = verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))
    assert len(witnesses) == 1
    assert witnesses[0].requirement_ids == ("R-001", "R-003")
    assert witnesses[0].inputs == {"count_field": "количество мест", "value": "12", "unit": "count"}


@pytest.mark.parametrize("left,right", [
    ("количество мест больше 4", "количество мест меньше 5"),
    ("количество мест меньше 1", "количество мест меньше 0"),
    ("количество мест равно 5", "количество гостей равно 5"),
    ("поле `eligible` равно true", "поле `eligible` равно false"),
    ("поле `eligible` равно true", "поле `Eligible` равно true"),
    ("поле `eligible` равно true", "поле `eligible` равно true и время истекло"),
])
def test_distinct_or_incomplete_explicit_guards_have_no_witness(left, right):
    source, reqs = fixture([f"Если {left}, заявка одобряется.", f"Если {right}, заявка отклоняется."])
    assert not verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))


def test_boolean_guard_with_an_exception_outside_extracted_excerpt_uses_original_unit():
    base = "Если поле `eligible` равно true, заявка отклоняется"
    source, reqs = fixture(["Если поле `eligible` равно true, заявка одобряется.", base + "."])
    witnesses = verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))
    assert witnesses[0].inputs == {"field:eligible": "true"}
    source.units[1] = base + ", если приём закрыт."
    reqs[1].evidence.quote = base
    assert not verify_rule_conflicts(source, reqs, build_fact_index(source, reqs))

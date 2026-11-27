import pytest

from agent.services import spec_multi_agent

from agent.services.spec_document import Specification
from agent.services.spec_multi_agent import (
    AdjudicationDecision, ConfirmedRootPair, ConfirmedRootReview, Group, GroupConflict,
    Observation, _apply_confirmed_root_review, _confirmed_root_payload,
)
from agent.services.spec_review import Evidence, Finding


def confirmed_group(index, line, quote, severity="medium", issue="Один исправляемый результат."):
    anchor = Evidence(start=line, end=line, quote=quote)
    finding = Finding(kind="conflict", severity=severity, issue=issue, recommendation="Согласовать наблюдаемый результат.",
                      evidence=[anchor], requirement_ids=[f"R-{line:03}"], criteria=["Согласованность"])
    oid = f"O-{index:04}"
    return Group([Observation(oid, "logic_inspector", finding)], AdjudicationDecision(
        id=oid, status="confirmed", severity=severity, evidence=[anchor], rationale=f"Основание {index}."))


def equivalent(a, b):
    return ConfirmedRootPair(left=a.id, right=b.id, left_citation=0, right_citation=0,
                             repair="Согласовать единую базу расчёта.",scope="Показатель по одной группе заявок.",
                             why_both_resolve="Единый знаменатель одновременно задаёт результат примера и вычисления.")


def test_pair_grounded_root_preserves_all_groups_decisions_and_strongest_confirmed_severity():
    one = confirmed_group(1, 1, "Процент считают среди поданных заявок.", "medium")
    two = confirmed_group(2, 1, "Процент считают среди поданных заявок.", "high", "Пример доли использует только принятые заявки.")
    two.linked_requirements.append("R-004")
    other = confirmed_group(3, 2, "Для другого отчёта расчёт идёт среди пользователей.")
    spec = Specification("test.md", "md", [one.finding().evidence[0].quote, other.finding().evidence[0].quote])
    payload, limited = _confirmed_root_payload([one, two, other], spec)
    assert not limited
    assert [row["id"] for row in payload["confirmed_findings"]] == [one.id, two.id]
    assert payload["cited_source_units"][0]["text"] == one.finding().evidence[0].quote
    groups, mapping = _apply_confirmed_root_review([one,two,other], ConfirmedRootReview(pairs=[equivalent(one,two)]), [])
    assert len(groups) == 2
    merged = next(g for g in groups if len(g.observations) == 2)
    assert mapping == {one.id: two.id,two.id:two.id,other.id:other.id}
    assert merged.finding().severity == "high" and merged.finding().issue == two.finding().issue
    assert {d.id for d in [merged.adjudication,*merged.merged_decisions]} == {one.id,two.id}
    assert set(merged.finding().requirement_ids) == {"R-001","R-004"}
    assert merged.merge_reasons and sum(len(g.observations) for g in groups) == 3


def test_scope_invalid_index_disagreement_and_missing_pair_cannot_erase_a_distinct_defect():
    one, two, three = [confirmed_group(i, 1, "Область расчёта.") for i in range(1,4)]
    other = confirmed_group(4, 2, "Другая область.")
    pair = equivalent(one,two)
    for row, blocked in [(equivalent(one,other),[]), (pair.model_copy(update={"left_citation":7}),[]),
                         (pair, [GroupConflict(one.id, two.id)])]:
        with pytest.raises(ValueError):
            _apply_confirmed_root_review([one,two,other], ConfirmedRootReview(pairs=[row]), blocked)
    groups, _ = _apply_confirmed_root_review([one,two,three], ConfirmedRootReview(pairs=[pair,equivalent(two,three)]), [])
    assert sorted(len(g.observations) for g in groups) == [1,2]


def test_invalid_pair_does_not_erase_a_disjoint_attested_pair():
    a, b, c, d = [confirmed_group(i, 1, "Единое правило приёмки.") for i in range(1, 5)]
    conflict = GroupConflict(c.id, d.id)
    review = ConfirmedRootReview(pairs=[equivalent(c, d), equivalent(a, b)])
    with pytest.raises(ValueError):
        _apply_confirmed_root_review([a, b, c, d], review, [conflict])
    recovered, mapping = _apply_confirmed_root_review(
        [a, b, c, d], review, [conflict], preserve_valid_pairs=True)
    assert len(recovered) == 3
    assert mapping[a.id] == mapping[b.id]
    assert mapping[c.id] != mapping[d.id]
    spec = Specification("test.md", "md", ["Единое правило приёмки."])
    payload, limited = _confirmed_root_payload([a, b, c, d], spec, [conflict])
    assert not limited
    assert payload["prohibited_pairs"] == [[c.id, d.id]]
    assert payload["confirmed_findings"][0]["citations"][0]["citation_index"] == 0


def test_payload_budget_counts_only_findings_with_an_admissible_partner(monkeypatch):
    one, two, other = [confirmed_group(i, i, f"Положение {i}.") for i in range(1, 4)]
    spec = Specification("test.md", "md", [f"Положение {i}." for i in range(1, 4)])
    groups = [one, two, other]
    monkeypatch.setattr(spec_multi_agent, "MAX_FINALIZER_GROUPS", 2)
    assert _confirmed_root_payload(groups, spec) == (None, False)
    two.linked_requirements = ["R-001"]
    assert _confirmed_root_payload(groups, spec, [GroupConflict(two.id, one.id)]) == (None, False)
    payload, limited = _confirmed_root_payload(groups, spec)
    assert not limited
    assert [row["id"] for row in payload["confirmed_findings"]] == [one.id, two.id]
    assert [unit["index"] for unit in payload["cited_source_units"]] == [1, 2]
    other.linked_requirements = ["R-001"]
    assert _confirmed_root_payload(groups, spec) == (None, True)


def test_cited_units_overlap_with_distinct_requirement_ids():
    one = confirmed_group(1, 1, "Общая граница.")
    two = confirmed_group(2, 2, "Итоговое значение.")
    one.observations[0].finding.evidence[0].end = 2
    spec = Specification("test.md", "md", ["Общая граница.", "Итоговое значение."])
    payload, limited = _confirmed_root_payload([one, two], spec)
    assert not limited
    assert [row["id"] for row in payload["confirmed_findings"]] == [one.id, two.id]


def test_unpresented_ids_cannot_merge_in_normal_or_recovery_path():
    one, two, other = [confirmed_group(i, 1, "Общее решение.") for i in range(1, 4)]
    review = ConfirmedRootReview(pairs=[equivalent(one, two), equivalent(one, other)])
    shown = {one.id, two.id}
    with pytest.raises(ValueError):
        _apply_confirmed_root_review([one, two, other], review, [], presented_ids=shown)
    groups, mapped = _apply_confirmed_root_review(
        [one, two, other], review, [], presented_ids=shown, preserve_valid_pairs=True)
    assert len(groups) == 2
    assert mapped[one.id] == mapped[two.id]
    assert mapped[other.id] == other.id

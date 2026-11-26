import json

import pytest

from agent.services.spec_document import Specification
from agent.services.spec_review import Evidence, Requirement
from agent.services.spec_review_facts import build_fact_index, select_fact_context


def requirements(lines):
    return [Requirement(id=f"R-{index:03d}", source_id=None, text=line,
                        evidence=Evidence(start=index, end=index, quote=line), checks=[])
            for index, line in enumerate(lines, 1)]


def test_extracts_literal_ranges_times_and_units_without_making_conflict_claims():
    lines = ["Лимит от 1 000 до 10 000 ₸.",
             "Операция от 31 января в 23:58 обрабатывается за 2 минуты.",
             "Параметр REQ-12 описан отдельно."]
    spec = Specification("sample.md", "md", lines)
    index = build_fact_index(spec, requirements(lines))
    values = {(fact.kind, fact.value, fact.unit, fact.relation) for fact in index}
    assert ("quantity", "100000", "KZT_minor", "lower_bound") in values
    assert ("quantity", "1000000", "KZT_minor", "upper_bound") in values
    assert ("date", "01-31", "calendar_day", "mentioned") in values
    assert ("time", "23:58", "day_time", "mentioned") in values
    assert ("quantity", "120", "second", "mentioned") in values
    assert all(fact.requirement_id != "R-003" for fact in index)
    for fact in index:
        spec.verify_quote(fact.start, fact.end, fact.quote)


def test_selects_scope_and_never_treats_disagreement_candidates_as_proven_defects():
    lines = ["Лимит операции равен 100 тенге.", "Лимит операции равен 15000 тиын.",
             "Лимит перевода равен 1000 тенге.", "Лимит операции равен 10000 тиын."]
    spec = Specification("sample.md", "md", lines)
    reqs = requirements(lines)
    index = build_fact_index(spec, reqs)
    context = select_fact_context(index, {"R-001", "R-002", "R-004"})
    assert {fact["requirement_id"] for fact in context["facts"]} == {"R-001", "R-002", "R-004"}
    by_id = {fact["id"]: fact for fact in context["facts"]}
    pairs = {(by_id[pair["left"]]["requirement_id"], by_id[pair["right"]]["requirement_id"])
             for pair in context["comparison_candidates"]}
    assert ("R-001", "R-002") in pairs
    assert ("R-001", "R-004") not in pairs
    assert all(pair["status"] == "needs_context" for pair in context["comparison_candidates"])


def test_fact_prompt_budget_is_bounded_without_slicing_evidence():
    spec = Specification("sample.md", "md", [f"Лимит {index} тенге на операцию." for index in range(1, 80)])
    index = build_fact_index(spec, requirements(spec.units))
    context = select_fact_context(index, {fact.requirement_id for fact in index}, max_chars=700)
    import json
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 700
    assert context["truncated"]
    for fact in context["facts"]:
        spec.verify_quote(fact["start"], fact["end"], fact["quote"])


def test_signed_quantities_remain_exact_and_invalid_dates_are_not_indexed():
    lines = ["Корректировка равна −12,50 тенге.", "Дата 31 февраля не существует."]
    spec = Specification("sample.md", "md", lines)
    facts = build_fact_index(spec, requirements(lines))
    assert [(fact.value, fact.unit) for fact in facts] == [("-1250", "KZT_minor")]


def test_shared_range_boundary_produces_a_hypothetical_probe_not_an_expected_result():
    lines = ["Счёт проверен: лимит суммы от 0 до 50 000 тенге, счёт утверждается.",
             "Счёт проверен: лимит суммы ровно 50 000 тенге, счёт отклоняется."]
    spec = Specification("sample.md", "md", lines)
    index = build_fact_index(spec, requirements(lines))
    context = select_fact_context(index, {"R-001", "R-002"})
    candidates = [pair for pair in context["comparison_candidates"] if pair["reason"] == "shared_boundary"]
    assert len(candidates) == 1
    assert candidates[0]["probe_values"] == ["5000000"]
    assert candidates[0]["unit"] == "KZT_minor"
    assert candidates[0]["status"] == "needs_context"


def test_distinct_acceptance_example_amounts_are_not_comparison_candidates():
    lines = ["Расходы 48 500 ₸, порог 50 000 ₸, провести покупку.",
             "Расходы 60 000 ₸, порог 70 000 ₸, провести покупку."]
    spec = Specification("sample.md", "md", lines)
    context = select_fact_context(build_fact_index(spec, requirements(lines)), {"R-001", "R-002"})
    assert len(context["facts"]) == 4
    assert not context["comparison_candidates"]


def test_small_budget_preserves_complete_boundary_probe_before_unrelated_mentions():
    lines = [f"Значение {index} тенге в учебном примере." for index in range(1, 15)]
    lines += ["Счёт проверен: лимит суммы от 0 до 50 000 тенге, счёт утверждается.",
              "Счёт проверен: лимит суммы ровно 50 000 тенге, счёт отклоняется."]
    spec = Specification("sample.md", "md", lines)
    reqs = requirements(lines)
    context = select_fact_context(build_fact_index(spec, reqs), {req.id for req in reqs}, max_chars=1000)
    assert any(pair["reason"] == "shared_boundary" for pair in context["comparison_candidates"])
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("profile, use_z3", [("full", True), ("full", False), ("numeric", False), ("off", False)])
async def test_existing_experts_receive_bounded_facts_and_scenario_witness_instructions(profile, use_z3):
    from langchain_core.messages import AIMessage
    from agent.services.spec_review import review_specification

    lines = ["Счёт со статусом «Проверен» и суммой от 0 до 50 000 тенге включительно утверждается автоматически.",
             "Счёт со статусом «Проверен» и суммой ровно 50 000 тенге отклоняется автоматически."]
    anchor = {"start": 1, "end": 1, "quote": lines[0]}

    class FixtureChat:
        model_name = "fixture"

        def __init__(self):
            self.calls = []

        async def ainvoke(self, messages, **kwargs):
            data = json.loads(messages[1].content)
            stage = data["stage"]
            self.calls.append((stage, messages[0].content, data))
            if stage == "extraction":
                response = {"requirements": [{"source_id": None, "text": line,
                    "evidence": {"start": index, "end": index, "quote": line}} for index, line in enumerate(lines, 1)]}
            elif stage.endswith("_individual"):
                response = {"requirements": [{"id": row["id"], "checks": [
                    {"criterion": criterion, "status": "pass", "comment": "Проверено."}
                    for criterion in data["criteria"]]} for row in data["requirements"]], "findings": []}
            elif stage.endswith("_set"):
                response = {"summary": "Проверено.", "set_checks": [{"criterion": criterion, "status": "pass",
                    "comment": "Проверено.", "evidence": [anchor]} for criterion in data["criteria"]],
                    "readiness_checklist": [{"id": id_, "availability": "uncertain", "status": "uncertain",
                        "comment": "Требуется уточнение."} for id_ in data["readiness_criteria"]], "findings": []}
            elif stage == "multi_finalizer":
                response = {"merges": []}
            else:
                raise AssertionError(f"Unexpected additional model stage: {stage}")
            return AIMessage(content=json.dumps(response, ensure_ascii=False), response_metadata={"finish_reason": "stop"})

    chat = FixtureChat()
    spec = Specification("sample.md", "md", lines)
    report = await review_specification(spec, chat, review_mode="multi_agent_direct", fact_profile=profile, use_z3=use_z3)
    formal_nodes = [node for node in report.evidence_graph.nodes if node.id.startswith("O-formal-")]
    assert bool(formal_nodes) == (profile == "full" and use_z3)
    if formal_nodes:
        assert formal_nodes[0].severity is None
        assert "Формальная проверка Z3" in report.report_markdown
    expert_calls = [(stage, prompt, data) for stage, prompt, data in chat.calls if stage.endswith(("_individual", "_set"))]
    assert len(expert_calls) == 9
    for stage, prompt, data in expert_calls:
        if profile == "off":
            assert "fact_context" not in data and "semantic_context" not in data
            assert "minimal modeled" not in prompt
            continue
        assert data["fact_context"]["facts"]
        allowed = {row["id"] for row in data["requirements"]}
        assert all(fact["requirement_id"] in allowed for fact in data["fact_context"]["facts"])
        assert "search hints, not defects" in prompt
        if stage.startswith("multi_scenario_builder_"):
            assert "minimal modeled" in prompt
            assert "negative control" in prompt
        if profile == "full":
            assert data["semantic_context"]["clauses"]
            assert bool(data["semantic_context"]["formal_witnesses"]) == use_z3
        else:
            assert "semantic_context" not in data

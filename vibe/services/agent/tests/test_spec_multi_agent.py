import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from agent.services.spec_review import (
    INDIVIDUAL_CRITERIA,
    READINESS_CRITERIA,
    SET_CRITERIA,
    extract_specification,
    review_specification,
)


SOURCE = "R-1. Предоставить выписку.\nR-2. Отобразить дату.\n"
ANCHOR = {"start": 1, "end": 1, "quote": "R-1. Предоставить выписку."}


class ExpertChat:
    model_name = "fixture"

    def __init__(self):
        self.stages = []
        self.active = 0
        self.peak = 0

    async def ainvoke(self, messages, **kwargs):
        data = json.loads(messages[1].content)
        stage = data["stage"]
        self.stages.append(data)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        def check(criterion):
            return {"criterion": criterion, "status": "pass", "comment": "Проверено."}
        finding = {
            "kind": "missing", "severity": "high", "issue": "Не указан период выписки.",
            "recommendation": "Уточнить период.", "evidence": [ANCHOR],
            "requirement_ids": ["R-001"], "criteria": ["Полнота"],
        }
        if stage == "extraction":
            result = {"requirements": [{"source_id":"R-1", "text":"Предоставить выписку", "evidence":ANCHOR},
                                        {"source_id":"R-2", "text":"Отобразить дату", "evidence": {"start":2, "end":2, "quote":"R-2. Отобразить дату."}}]}
        elif stage.endswith("_individual"):
            result = {"requirements": [{"id": row["id"], "checks": [check(c) for c in data["criteria"]]} for row in data["requirements"]], "findings": []}
        elif stage.endswith("_set"):
            result = {"summary":"Проверено.", "set_checks": [{**check(c), "evidence": [ANCHOR]} for c in data["criteria"]],
                      "readiness_checklist": [{"id": k, "availability":"present", "status":"pass", "comment":"Проверено.", "evidence":[ANCHOR]} for k in data["readiness_criteria"]],
                      "findings": [finding] if data["role"] in {"logic_inspector", "gap_hunter"} else []}
        elif stage == "multi_finalizer" or stage.startswith("multi_pair_"):
            result = {"summary":"Обнаружено замечание.", "merges":[]}
        elif stage == "multi_recheck":
            result = {"status":"disagrees", "comment":"Требуется уточнение."}
        elif stage == "multi_adjudicate":
            result = {"decisions": [{"id": row["id"], "status": "confirmed", "severity": row["severity"],
                                      "rationale": "Подтверждено исходным текстом.", "evidence": row["evidence"][:1]}
                                     for row in data["findings"]]}
        else:
            raise AssertionError(stage)
        return AIMessage(content=json.dumps(result, ensure_ascii=False), response_metadata={"finish_reason":"stop"})


@pytest.mark.asyncio
async def test_multi_agent_parallel_coverage_and_provenance():
    chat = ExpertChat()
    source = extract_specification("fixture.txt", "text/plain", SOURCE.encode())
    report = await review_specification(source, chat, concurrency=3)
    assert report.passport.review_mode == "multi_agent"
    assert report.passport.review_profile.model_dump() == {
        "fact_profile": "full", "z3_enabled": True, "reasoning_effort": "auto",
        "adjudication_reasoning_effort": "auto", "concurrency": 3,
        "drex_enabled": False, "references_count": 0,
    }
    assert "Профиль фактов: full" in report.report_markdown
    assert report.evidence_graph is not None
    for req in report.requirements:
        assert [c.criterion for c in req.checks] == list(INDIVIDUAL_CRITERIA)
    assert [c.criterion for c in report.set_checks] == list(SET_CRITERIA)
    assert [c.id for c in report.readiness_checklist] == list(READINESS_CRITERIA)
    assert chat.peak <= 3
    assert report.verdict.status != "ready"
    assert len(report.findings) == 1
    graph = report.evidence_graph
    assert graph.adjudication.confirmed == 1 and graph.adjudication.candidates == 1
    node_ids = {node.id for node in graph.nodes}
    assert len(node_ids) == len(graph.nodes)
    assert all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)
    observations = [n for n in graph.nodes if n.kind == "observation"]
    assert {n.role for n in observations} == {"logic_inspector", "gap_hunter"}
    assert all(any(e.source == n.id and e.kind == "contributes_to" and e.target == "D-001" for e in graph.edges) for n in observations)
    assert all("already_identified" not in data for data in chat.stages if data["stage"].endswith(("_individual", "_set")))
    assert any(node.kind == "evidence" and node.evidence.quote == ANCHOR["quote"] for node in graph.nodes)


@pytest.mark.asyncio
async def test_direct_route_preserves_expert_provenance_and_uses_only_the_central_medium_validator():
    class DirectChat(ExpertChat):
        model_name = "gpt-6-luna"

        def __init__(self):
            super().__init__()
            self.efforts = []

        async def ainvoke(self, messages, **kwargs):
            stage = json.loads(messages[1].content)["stage"]
            self.efforts.append((stage, kwargs.get("reasoning_effort")))
            return await super().ainvoke(messages, **kwargs)

    chat = DirectChat()
    report = await review_specification(source(), chat, review_mode="multi_agent_direct", concurrency=3)

    assert report.passport.review_mode == "multi_agent_direct"
    assert all(not stage.startswith("multi_pair_") for stage, _ in chat.efforts)
    finalizers = [data for data in chat.stages if data["stage"] == "multi_finalizer"]
    assert len(finalizers) == 1
    finalizer = finalizers[0]
    assert "paired_summaries" not in finalizer
    assert {card["role"]: card["status"] for card in finalizer["coverage_cards"]} == {
        "logic_inspector": "completed", "scenario_builder": "completed", "gap_hunter": "completed",
        "contract_inspector": "completed", "risk_inspector": "completed", "reference_inspector": "skipped",
    }
    assert {"logic_inspector", "gap_hunter"} == set(finalizer["groups"][0]["roles"])
    assert all(effort == ("medium" if stage in {"multi_finalizer", "multi_recheck", "multi_adjudicate"} else "low")
               for stage, effort in chat.efforts)
    assert chat.peak <= 3
    assert len(report.findings) == 1
    assert all([row.criterion for row in req.checks] == list(INDIVIDUAL_CRITERIA) for req in report.requirements)
    assert [row.criterion for row in report.set_checks] == list(SET_CRITERIA)
    assert [row.id for row in report.readiness_checklist] == list(READINESS_CRITERIA)
    graph = report.evidence_graph
    contributors = {edge.source for edge in graph.edges if edge.kind == "contributes_to" and edge.target == "D-001"}
    assert {node.role for node in graph.nodes if node.id in contributors} == {"logic_inspector", "gap_hunter"}


class VariantChat(ExpertChat):
    def __init__(self, variant):
        super().__init__()
        self.variant = variant

    async def ainvoke(self, messages, **kwargs):
        response = await super().ainvoke(messages, **kwargs)
        data = json.loads(messages[1].content)
        result = json.loads(response.content)
        self.variant(data, result)
        return AIMessage(content=json.dumps(result, ensure_ascii=False), response_metadata={"finish_reason":"stop"})


def source():
    return extract_specification("fixture.txt", "text/plain", SOURCE.encode())


@pytest.mark.asyncio
async def test_final_adjudication_reclassifies_blocking_conflict_and_preserves_original_severity():
    second = {"start": 2, "end": 2, "quote": "R-2. Отобразить дату."}

    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_set":
            result["findings"] = [{"kind": "conflict", "severity": "medium", "issue": "Несовместимые результаты.",
                "recommendation": "Согласовать результат.", "evidence": [ANCHOR, second],
                "requirement_ids": ["R-001", "R-002"], "criteria": ["Согласованность"]}]
        elif data["stage"] == "multi_gap_hunter_set":
            result["findings"] = []
        elif data["stage"] == "multi_adjudicate":
            result["decisions"] = [{"id": row["id"], "status": "confirmed", "severity": "critical",
                "rationale": "Обязательные результаты несовместимы; приёмка невозможна.",
                "evidence": [ANCHOR, second]} for row in data["findings"]]

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    assert report.statistics.critical == 1
    assert report.verdict.status == "not_ready"
    assert report.findings[0].severity == "critical"
    graph = report.evidence_graph
    assert any(n.kind == "observation" and n.severity == "medium" and n.finding_id == "D-001" for n in graph.nodes)
    assert any(n.kind == "finding" and n.severity == "critical" and "приёмка невозможна" in n.description for n in graph.nodes)


@pytest.mark.asyncio
async def test_final_adjudication_quarantines_only_source_backed_rejections():
    def variant(data, result):
        if data["stage"] == "multi_adjudicate":
            result["decisions"] = [{"id": row["id"], "status": "unsupported", "severity": None,
                "rationale": "Требование уже описано в документе.", "evidence": [ANCHOR]} for row in data["findings"]]

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    assert not report.findings
    assert report.evidence_graph.adjudication.unsupported == 1
    assert report.verdict.status != "ready"
    quarantined = [n for n in report.evidence_graph.nodes if n.kind == "observation" and n.finding_id is None]
    assert {n.role for n in quarantined} == {"logic_inspector", "gap_hunter"}
    assert "Отклонённые наблюдения" in report.report_markdown


@pytest.mark.asyncio
async def test_uncertain_adjudication_context_is_not_a_primary_finding_citation():
    extra = {"start": 2, "end": 2, "quote": "R-2. Отобразить дату."}

    def variant(data, result):
        if data["stage"] == "multi_adjudicate":
            result["decisions"] = [{"id": row["id"], "status": "uncertain", "severity": None,
                "rationale": "Контекст не разрешает неопределённость.", "evidence": [extra]} for row in data["findings"]]

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    graph = report.evidence_graph
    by_id = {node.id: node for node in graph.nodes}
    citations = [by_id[e.target].evidence.model_dump() for e in graph.edges if e.source == "D-001" and e.kind == "cites"]
    context = [by_id[e.target].evidence.model_dump() for e in graph.edges if e.source == "D-001" and e.kind == "adjudication_context"]
    assert citations == [ANCHOR] == [e.model_dump() for e in report.findings[0].evidence]
    assert context == [extra]


@pytest.mark.asyncio
async def test_invalid_adjudication_evidence_preserves_original_finding_and_does_not_cache(tmp_path):
    from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope

    def variant(data, result):
        if data["stage"] == "multi_adjudicate":
            result["decisions"] = [{"id": row["id"], "status": "unsupported", "severity": None,
                "rationale": "Пропуска нет.", "evidence": [{"start": 1, "end": 1, "quote": "Не существующая цитата"}]}
                for row in data["findings"]]

    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    with checkpoint_scope(checkpoint):
        report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    assert len(report.findings) == 1
    assert report.findings[0].severity == "high"
    assert report.evidence_graph.adjudication.unreviewed == 1
    resumed = ExpertChat()
    with checkpoint_scope(checkpoint):
        report = await review_specification(source(), resumed, review_mode="multi_agent_direct")
    assert report.evidence_graph.adjudication.confirmed == 1
    assert any(value["stage"] == "multi_adjudicate" for value in resumed.stages)


@pytest.mark.asyncio
async def test_local_pair_semantic_merge_retains_every_line_of_provenance():
    def variant(data, result):
        stage = data["stage"]
        if stage == "multi_scenario_builder_set":
            result["findings"] = [{
                "kind":"missing", "severity":"medium", "issue":"Период формирования выписки не определён.",
                "recommendation":"Указать границы периода.", "requirement_ids":["R-001"],
                "criteria":["Полнота"], "evidence":[ANCHOR],
            }]
            result["counterexamples"] = [{"finding_index":0,"scenario":"Клиент запрашивает выписку.","outcomes":"Разные даты начала периода."}]
        elif stage == "multi_pair_1":
            assert len(data["groups"]) == 2
            assert {role for group in data["groups"] for role in group["roles"]} == {"logic_inspector", "scenario_builder"}
            result["merges"] = [data["candidate_pairs"][0]]
        elif stage == "multi_finalizer" and data["candidate_pairs"]:
            # The global finalizer sees local groups, not each local finding.
            result["merges"] = [data["candidate_pairs"][0]]

    chat = VariantChat(variant)
    report = await review_specification(source(), chat, review_mode="multi_agent")
    assert any(stage["stage"] == "multi_pair_1" for stage in chat.stages)
    assert len(report.findings) == 1
    assert report.findings[0].severity == "high"
    graph = report.evidence_graph
    observations = [n for n in graph.nodes if n.kind == "observation" and n.severity]
    assert {n.role for n in observations} == {"logic_inspector", "scenario_builder", "gap_hunter"}
    assert all(any(e.source == n.id and e.target == "D-001" and e.kind == "contributes_to" for e in graph.edges) for n in observations)
    assert any(n.kind == "counterexample" and n.role == "scenario_builder" for n in graph.nodes)
    assert graph.unresolved_count == 1
    assert next(c for c in report.requirements[0].checks if c.criterion == "Полнота").status == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("review_mode", ["multi_agent", "multi_agent_direct"])
async def test_invalid_finalizer_merge_falls_back_without_losing_findings(review_mode):
    def variant(data, result):
        if data["stage"] == "multi_gap_hunter_set":
            result["findings"][0]["issue"] = "Период получения выписки не определён."
        elif data["stage"] == "multi_finalizer":
            result["merges"] = [["O-unknown", "O-0001"]]

    report = await review_specification(source(), VariantChat(variant), review_mode=review_mode)
    assert report.evidence_graph.finalization_status == "deterministic_fallback"
    assert len(report.findings) == 2
    assert report.findings[0].severity == "high"


@pytest.mark.asyncio
async def test_repeated_invented_quote_is_discarded_but_exact_peer_finding_survives():
    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_set":
            result["findings"][0]["evidence"][0]["quote"] = "Поддельная цитата"
            result["counterexamples"] = [{"finding_index":0,"scenario":"Выдуманный сценарий.","outcomes":"Выдуманный результат."}]

    chat = VariantChat(variant)
    report = await review_specification(source(), chat, review_mode="multi_agent")
    assert len([stage for stage in chat.stages if stage["stage"] == "multi_logic_inspector_set"]) == 2
    assert len(report.findings) == 1
    assert report.verdict.status != "ready"
    assert all(n.evidence is None or n.evidence.quote != "Поддельная цитата" for n in report.evidence_graph.nodes)
    assert not any(n.kind == "counterexample" for n in report.evidence_graph.nodes)
    assert {n.role for n in report.evidence_graph.nodes if n.kind == "observation" and n.severity} == {"gap_hunter"}
    assert next(c for c in report.set_checks if c.criterion == "Согласованность").status == "uncertain"


@pytest.mark.asyncio
async def test_recheck_limit_and_unresolved_disputes_prevent_positive_assessments():
    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_set":
            original = result["findings"][0]
            result["findings"] = [dict(original, issue=f"Не определено условие {i}.") for i in range(1, 9)]
        elif data["stage"] == "multi_gap_hunter_set":
            result["findings"] = []

    chat = VariantChat(variant)
    report = await review_specification(source(), chat, review_mode="multi_agent")
    assert len(report.findings) == 8
    assert len([stage for stage in chat.stages if stage["stage"] == "multi_recheck"]) == 6
    assert report.evidence_graph.unresolved_count == 8
    assert report.verdict.status == "not_ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("review_mode", ["multi_agent", "multi_agent_direct"])
@pytest.mark.parametrize("invalid_reference",[False,True])
async def test_reference_validation_keeps_normative_findings_separate(monkeypatch,invalid_reference,review_mode):
    from agent.services import spec_multi_agent
    from agent.services.spec_reference_review import (
        ReferenceDocument, ReferenceObservation, ReferenceSource, ReferenceReview,
    )

    document = extract_specification("policy.txt","text/plain", b"Retain statement history.")
    reference_source = ReferenceSource(
        id="S-001", filename="policy.txt", source=document.source, sha256=document.sha256,
        unreadable_pages=[], category="bank_policy", title="Учебная политика",
        issuer="Учебный пример", version="1", scope="Выписки", source_url=None,
        applicability="relevant", summary="Учебный пример.",
    )
    async def reference_result(*args, **kwargs):
        return ReferenceReview(status="completed", explanation="Учебное сопоставление.",
            sources=[reference_source], observations=[ReferenceObservation(
            id="RF-001", source_id="S-001", status="potential_discrepancy", issue="Не определён срок.",
            recommendation="Уточнить.", requirement_ids=["R-999" if invalid_reference else "R-001"], specification_evidence=[ANCHOR],
            reference_evidence=[{"start":1,"end":1,"quote":"Retain statement history."}],
        )])

    monkeypatch.setattr(spec_multi_agent, "compare_references", reference_result)
    def variant(data,result):
        if data["stage"] == "multi_risk_inspector_set":
            result["findings"] = [{
                "kind":"other", "severity":"medium", "issue":"Не описано ожидаемое поведение.",
                "recommendation":"Уточнить.", "requirement_ids":["R-001"], "criteria":[], "evidence":[ANCHOR],
            }]
    chat = VariantChat(variant)
    report = await review_specification(source(), chat, review_mode=review_mode,
                                        references=[ReferenceDocument(reference_source, document)])
    if review_mode == "multi_agent_direct":
        assert not any(data["stage"].startswith("multi_pair_") for data in chat.stages)
        finalizer = next(data for data in chat.stages if data["stage"] == "multi_finalizer")
        assert finalizer["reference_cards_are_separate_from_core"] is True
        assert [card["id"] for card in finalizer["reference_cards"]] == ([] if invalid_reference else ["RF-001"])
        card = next(card for card in finalizer["coverage_cards"] if card["role"] == "reference_inspector")
        assert card["status"] == ("unavailable" if invalid_reference else "completed")
    assert len(report.findings) == 2
    if invalid_reference:
        assert report.reference_review.status == "unavailable"
        assert not report.reference_review.observations
        assert all(not n.id.startswith("RF-") for n in report.evidence_graph.nodes)
        return
    assert len(report.reference_review.observations) == 1
    graph = report.evidence_graph
    assert any(n.kind == "finding" and n.id == "RF-001" for n in graph.nodes)
    assert any(n.kind == "evidence" and n.source_id == "S-001" for n in graph.nodes)
    risk = next(n for n in graph.nodes if n.kind == "observation" and n.role == "risk_inspector")
    assert any(e.kind == "about" and e.source == risk.id and e.target == "O-ref-RF-001" for e in graph.edges)


@pytest.mark.asyncio
async def test_multi_agent_reuses_validated_stage_checkpoints(tmp_path):
    from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope

    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    first = ExpertChat()
    with checkpoint_scope(checkpoint):
        a = await review_specification(source(), first, review_mode="multi_agent")
    resumed = ExpertChat()
    with checkpoint_scope(checkpoint):
        b = await review_specification(source(), resumed, review_mode="multi_agent")
    assert first.stages
    assert not resumed.stages
    assert a.evidence_graph == b.evidence_graph


@pytest.mark.asyncio
async def test_reducer_preserves_differing_numbers_and_negations_on_shared_quote():
    def variant(data,result):
        if data["stage"] == "multi_logic_inspector_set":
            original = result["findings"][0]
            result["findings"] = [dict(original, issue="Не установлен лимит 10."),
                                  dict(original, issue="Не установлен лимит 20."),
                                  dict(original, issue="Установлен лимит 10.")]
        elif data["stage"] == "multi_gap_hunter_set":
            result["findings"] = []
        elif data["stage"] == "multi_finalizer":
            assert not data["candidate_pairs"]
            result["summary"] = "Замечаний нет. ТЗ готово."

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent")
    assert len(report.findings) == 3
    assert "3 замечаний" in report.summary
    assert "ТЗ готово" not in report.summary
    assert report.verdict.status == "not_ready"


def test_graph_enforces_evidence_and_foreign_node_integrity():
    from agent.services.spec_evidence_graph import EvidenceGraph
    from pydantic import ValidationError

    def graph(*nodes):
        return EvidenceGraph.model_validate({"schema_version":1, "nodes":list(nodes), "edges":[],
                                             "experts":[], "finalization_status":"completed", "unresolved_count":0})
    with pytest.raises(ValidationError, match="graph evidence"):
        graph({"id":"E-1","kind":"evidence","title":"Цитата"})
    with pytest.raises(ValidationError, match="Unknown graph node reference"):
        graph({"id":"E-1","kind":"evidence","title":"Цитата","evidence":ANCHOR,"source_id":"S-001"})
    with pytest.raises(ValidationError, match="graph requirement identity"):
        graph({"id":"R-1","kind":"requirement","title":"Требование"})


@pytest.mark.asyncio
async def test_finalizer_exposes_cross_group_disagreements_without_removing_evidence():
    def variant(data, result):
        stage = data["stage"]
        if stage == "multi_logic_inspector_set":
            result["findings"][0]["issue"] = "Не установлен лимит выписки."
        elif stage == "multi_gap_hunter_set":
            result["findings"][0]["issue"] = "Установлен лимит выписки."
        elif stage == "multi_finalizer":
            assert data["disagreement_candidates"]
            result["disagreements"] = [data["disagreement_candidates"][0]]
            result["merges"] = []
        elif stage == "multi_recheck" and "conflicting_observation" in data:
            result["status"] = "uncertain"

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent")
    assert len(report.findings) == 2
    assert any(e.kind == "disagrees_with" and {e.source, e.target} == {"D-001", "D-002"} for e in report.evidence_graph.edges)
    assert report.evidence_graph.unresolved_count >= 1


@pytest.mark.asyncio
async def test_independent_gap_survives_partial_overlap_with_registry_quote():
    # The extractor paraphrased only obligation one, but retained the entire
    # sentence as its source quote. A substring check is not semantic coverage.
    quote = "R-1. Предоставить выписку и фиксировать период."
    omitted = {"start":1,"end":1,"quote":"фиксировать период."}
    def variant(data, result):
        def replace(obj):
            if isinstance(obj, dict):
                if obj == ANCHOR:
                    obj["quote"] = quote
                else:
                    for item in obj.values():
                        replace(item)
            elif isinstance(obj, list):
                for item in obj:
                    replace(item)
        replace(result)
        if data["stage"] == "multi_gap_hunter_set":
            result["extraction_gaps"] = [omitted]
    converted = extract_specification("fixture.txt","text/plain",
                                      (quote+"\nR-2. Отобразить дату.\n").encode())
    report = await review_specification(converted, VariantChat(variant), review_mode="multi_agent")
    assert any(item.model_dump() == omitted for item in report.extraction_gaps)
    assert report.verdict.status != "ready"


@pytest.mark.asyncio
async def test_timing_opposites_are_not_near_duplicate_defects():
    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_set":
            result["findings"][0]["issue"] = "Не указан срок возврата после подтверждения операции."
        elif data["stage"] == "multi_gap_hunter_set":
            result["findings"][0]["issue"] = "Не указан срок возврата до подтверждения операции."
    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent")
    assert len(report.findings) == 2


def test_dense_section_registry_is_bounded_and_explicitly_partial():
    from agent.services.spec_multi_agent import compact_section_registry
    from agent.services.spec_review import Requirement, Evidence

    items = [Requirement(id=f"R-{index:03d}", source_id=None,
                         text=("Подробное отдельное обязательство " * 80) + str(index),
                         evidence=Evidence(**ANCHOR), checks=[]) for index in range(1, 401)]
    data = compact_section_registry(items, items[:2])
    assert data["total"] == 400
    assert data["included"] < data["total"]
    assert data["partial"]
    assert len(json.dumps(data["entries"], ensure_ascii=False, separators=(",",":"))) <= 12000
    assert all(item["id"] not in {"R-001","R-002"} for item in data["entries"])


@pytest.mark.asyncio
async def test_repeated_bad_requirement_association_preserves_verified_matrix_and_observations(tmp_path):
    from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope

    exact_second = {"start":2,"end":2,"quote":"R-2. Отобразить дату."}
    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_individual":
            for item in result["requirements"]:
                for check in item["checks"]:
                    if check["criterion"] == "Корректность":
                        check["status"] = "fail"
            result["findings"] = [
                {"kind":"other", "severity":"high", "issue":"Неверная привязка.",
                 "recommendation":"Уточнить.", "requirement_ids":["R-001"],
                 "criteria":["Корректность"], "evidence":[exact_second]},
                {"kind":"ambiguous", "severity":"medium", "issue":"Не уточнён формат даты.",
                 "recommendation":"Уточнить формат.", "requirement_ids":["R-002"],
                 "criteria":["Корректность"], "evidence":[exact_second]},
            ]
            result["counterexamples"] = [
                {"finding_index":0,"scenario":"Неподтверждено.","outcomes":"Неподтверждено."},
                {"finding_index":1,"scenario":"Ввод даты.","outcomes":"Разные форматы."},
            ]

    chat = VariantChat(variant)
    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    with checkpoint_scope(checkpoint):
        report = await review_specification(source(), chat, review_mode="multi_agent")
    assert len([data for data in chat.stages if data["stage"] == "multi_logic_inspector_individual"]) == 2
    assert next(c for c in report.requirements[0].checks if c.criterion == "Корректность").status == "uncertain"
    assert next(c for c in report.requirements[1].checks if c.criterion == "Корректность").status == "fail"
    assert any(f.issue == "Не уточнён формат даты." for f in report.findings)
    assert not any(f.issue == "Неверная привязка." for f in report.findings)
    assert all("Неподтверждено" not in (n.description or "") for n in report.evidence_graph.nodes)
    assert any(n.kind == "counterexample" and "Ввод даты." in (n.description or "") for n in report.evidence_graph.nodes)
    assert report.verdict.status != "ready"
    resumed = VariantChat(variant)
    with checkpoint_scope(checkpoint):
        rerun = await review_specification(source(), resumed, review_mode="multi_agent")
    assert not resumed.stages
    assert rerun.evidence_graph == report.evidence_graph


@pytest.mark.asyncio
async def test_repeated_invalid_risk_finding_blocks_ready_even_without_other_defects():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"] = []
        if data["stage"] == "multi_risk_inspector_set":
            result["findings"] = [{"kind":"other", "severity":"high", "issue":"Неподтверждённый риск.",
                "recommendation":"Уточнить.", "requirement_ids":[], "criteria":[],
                "evidence":[{"start":1,"end":1,"quote":"Выдуманный риск."}]}]
    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent")
    assert not report.findings
    assert report.verdict.status != "ready"
    assert all(row.status == "uncertain" for row in report.readiness_checklist
               if row.id in {"rollback","operational_risk","information_security"})


@pytest.mark.asyncio
async def test_missing_required_check_still_fails_closed_after_correction():
    def variant(data,result):
        if data["stage"] == "multi_logic_inspector_individual":
            result["requirements"][0]["checks"].pop()
    with pytest.raises(ValueError, match="Missing, duplicate or unexpected review criteria"):
        await review_specification(source(), VariantChat(variant), review_mode="multi_agent")


def test_semantic_groups_cannot_chain_through_only_one_original_anchor():
    from agent.services.spec_multi_agent import Group, Observation, _merge_candidates, merge_exact
    from agent.services.spec_review import Evidence, Finding

    a = Evidence(start=1,end=1,quote="Общее требование")
    b = Evidence(start=2,end=2,quote="Связанный пункт")
    def observation(id_, issue, evidence):
        return Observation(id_, "logic_inspector", Finding(
            kind="missing",severity="high",issue=issue,recommendation="Уточнить.",
            requirement_ids=["R-001"],criteria=["Полнота"],evidence=evidence,
        ))
    first = observation("O-1","Не указан срок.",[a])
    bridge = observation("O-2","Срок не определён.",[a,b])
    last = observation("O-3","Не указан срок.",[b])
    already_merged = Group([first,bridge])
    assert not _merge_candidates([already_merged,Group([last])])
    # A merged group's representative may match a later finding, but the
    # hidden original issue must not be deterministically discarded.
    union_duplicate = observation("O-4","Не указан срок.",[a,b])
    assert len(merge_exact([already_merged,Group([union_duplicate])])) == 2


@pytest.mark.asyncio
async def test_scalar_adjudication_cannot_confirm_multiple_distinct_checks():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"] = []
        if data["stage"] == "multi_logic_inspector_individual":
            result["findings"] = [{"kind":"ambiguous","severity":"medium","issue":"Нет определения формата.",
                "recommendation":"Уточнить.", "requirement_ids":["R-001"],
                "criteria":["Однозначность","Корректность"],"evidence":[ANCHOR]}]
        elif data["stage"] == "multi_recheck":
            assert len(data["disagreements"]) == 2
            result.update(status="agrees",comment="Один вывод подтверждён.")
    report = await review_specification(source(), VariantChat(variant),review_mode="multi_agent")
    checked = {c.criterion:c.status for c in report.requirements[0].checks}
    assert checked["Однозначность"] == checked["Корректность"] == "uncertain"


@pytest.mark.asyncio
async def test_unaddressed_set_omission_is_not_published_in_multi_report():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"] = []
        if data["stage"] == "multi_gap_hunter_set":
            for row in result["set_checks"]:
                row["evidence"] = []
            result["findings"] = [{"kind":"missing","severity":"high","issue":"Не задан документ-wide срок.",
                "recommendation":"Уточнить.", "requirement_ids":[],"criteria":["Полнота набора"],"evidence":[]}]
    report = await review_specification(source(), VariantChat(variant),review_mode="multi_agent")
    assert not report.findings
    assert report.verdict.status != "ready"
    assert next(c for c in report.set_checks if c.criterion == "Полнота набора").status == "uncertain"


@pytest.mark.asyncio
async def test_unaddressed_set_omission_is_not_published_in_legacy_report():
    class Chat(ExpertChat):
        async def ainvoke(self,messages,**kwargs):
            data=json.loads(messages[1].content)
            if data["stage"] == "individual":
                result={"requirements":[{"id":req["id"],"checks":[{"criterion":c,"status":"pass","comment":"Проверено."} for c in data["criteria"]]} for req in data["requirements"]],"findings":[]}
            elif data["stage"] == "set":
                result={"summary":"Проверено.","set_checks":[{"criterion":c,"status":"fail" if c=="Полнота набора" else "pass","comment":"Проверено.","evidence":[]} for c in data["criteria"]],
                        "findings":[{"kind":"missing","severity":"high","issue":"Не задан срок.",
                                     "recommendation":"Уточнить.","requirement_ids":[],"criteria":["Полнота набора"],"evidence":[]}]}
            else:
                return await super().ainvoke(messages,**kwargs)
            return AIMessage(content=json.dumps(result,ensure_ascii=False),response_metadata={"finish_reason":"stop"})
    report = await review_specification(source(),Chat(),review_mode="legacy")
    assert not report.findings
    assert report.verdict.status != "ready"
    assert next(c for c in report.set_checks if c.criterion=="Полнота набора").status=="uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode",["legacy","multi_agent"])
async def test_empty_registry_blocking_finding_cites_readable_document(mode):
    class EmptyChat(ExpertChat):
        async def ainvoke(self,messages,**kwargs):
            data=json.loads(messages[1].content)
            stage=data["stage"]
            if stage=="extraction":
                result={"requirements":[]}
            elif stage.endswith("_set") or stage=="set":
                result={"summary":"Нет реестра.","set_checks":[{"criterion":c,"status":"pass","comment":"Проверено.","evidence":[]} for c in data["criteria"]],
                        "readiness_checklist":[{"id":k,"availability":"uncertain","status":"uncertain","comment":"Уточнить."} for k in data.get("readiness_criteria",{})],"findings":[]}
            else:
                raise AssertionError(stage)
            return AIMessage(content=json.dumps(result,ensure_ascii=False),response_metadata={"finish_reason":"stop"})
    specification=extract_specification("intro.txt","text/plain","Вводный раздел".encode())
    report=await review_specification(specification,EmptyChat(),review_mode=mode)
    assert report.verdict.status=="not_ready"
    assert report.findings
    for finding in report.findings:
        assert finding.evidence
        for quote in finding.evidence:
            specification.verify_quote(quote.start,quote.end,quote.quote)


@pytest.mark.asyncio
async def test_repeated_set_finding_with_unrelated_anchor_is_discarded():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"]=[]
        if data["stage"]=="multi_logic_inspector_set":
            result["findings"]=[{"kind":"ambiguous","severity":"high","issue":"Неверная привязка.",
                "recommendation":"Уточнить.","requirement_ids":["R-001"],"criteria":["Согласованность"],
                "evidence":[{"start":2,"end":2,"quote":"R-2. Отобразить дату."}]}]
    report=await review_specification(source(),VariantChat(variant),review_mode="multi_agent")
    assert not report.findings
    assert report.verdict.status != "ready"
    assert next(c for c in report.set_checks if c.criterion=="Согласованность").status=="uncertain"


@pytest.mark.asyncio
async def test_full_same_anchors_consider_different_requirement_ids_for_semantic_merge():
    second = {"start":2,"end":2,"quote":"R-2. Отобразить дату."}
    def variant(data,result):
        if data["stage"] == "multi_logic_inspector_set":
            finding = result["findings"][0]
            finding.update(kind="conflict", issue="Конфликт формата даты выписки.", evidence=[ANCHOR,second], criteria=["Согласованность"])
        elif data["stage"] == "multi_gap_hunter_set":
            finding = result["findings"][0]
            finding.update(kind="conflict", issue="Формат даты выписки противоречив.", evidence=[ANCHOR,second],
                           requirement_ids=["R-002"],criteria=["Полнота набора"])
        elif data["stage"] == "multi_finalizer":
            assert len(data["candidate_pairs"]) == 1
            result["merges"] = [data["candidate_pairs"][0]]
    report=await review_specification(source(),VariantChat(variant),review_mode="multi_agent")
    assert len(report.findings)==1
    assert set(report.findings[0].requirement_ids)=={"R-001","R-002"}
    assert len([n for n in report.evidence_graph.nodes if n.kind=="observation" and n.severity])==2


@pytest.mark.asyncio
async def test_typed_adjudication_updates_only_its_own_confirmed_row():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"]=[]
        if data["stage"]=="multi_logic_inspector_individual":
            result["findings"]=[{"kind":"ambiguous","severity":"medium","issue":"Нет определения формата.",
                "recommendation":"Уточнить.","requirement_ids":["R-001"],"criteria":["Однозначность","Корректность"],"evidence":[ANCHOR]}]
        elif data["stage"]=="multi_recheck":
            result.clear()
            result["assessments"]=[{"id":row["id"],"status":"agrees" if row["criterion"]=="Однозначность" else "uncertain","comment":"Отдельная оценка."}
                                   for row in data["disagreements"]]
            assert data["source_context"]["units"]
    report=await review_specification(source(),VariantChat(variant),review_mode="multi_agent")
    rows={c.criterion:c.status for c in report.requirements[0].checks}
    assert rows["Однозначность"]=="fail"
    assert rows["Корректность"]=="uncertain"


@pytest.mark.asyncio
async def test_long_single_unit_scoped_experts_cover_tail_and_keep_uncertainty():
    omitted = "Особое обязательство из дальнего конца документа."
    text = ANCHOR["quote"] + (" Продолжение документа." * 1500) + " " + omitted
    specification = extract_specification("long.txt","text/plain",(text+"\nR-2. Отобразить дату.").encode())
    def variant(data,result):
        stage = data["stage"]
        if stage=="extraction":
            result["requirements"] = [item for item in result["requirements"]
                                      if data["primary_start"] <= item["evidence"]["start"] <= data["primary_end"]]
        elif stage.endswith("_set"):
            result["findings"]=[]
            if data["role"]=="gap_hunter" and data.get("scope")=="section" and any(omitted in unit["text"] for unit in data["units"]):
                result["extraction_gaps"]=[{"start":1,"end":1,"quote":omitted}]
    chat=VariantChat(variant)
    report=await review_specification(specification,chat,review_mode="multi_agent")
    assert any(e.quote==omitted for e in report.extraction_gaps)
    assert report.verdict.status!="ready"
    assert all(c.status!="pass" for c in report.set_checks)
    stages=[item for item in chat.stages if item["stage"].endswith(("_individual","_set"))]
    assert stages
    assert all(sum(len(unit["text"]) for unit in item["units"]) <= 20000 for item in stages)
    windows=[unit for item in stages if item.get("role")=="gap_hunter" and item.get("scope")=="section"
             for unit in item["units"] if unit.get("index")==1]
    assert any(unit.get("excerpt") for unit in windows)
    assert any(omitted in unit["text"] for unit in windows)


def test_materialized_graph_and_task_coverage_reject_lost_or_reassigned_origins():
    from agent.services.spec_multi_agent import (
        ExpertTask, ExpertResult, Group, Observation, validate_task_coverage,
        materialize_evidence_index, validate_reduced_index,
    )
    from agent.services.spec_review import Evidence, Finding, Requirement, ReviewPipelineError

    requirement=Requirement(id="R-001",source_id="R-1",text="Выписка",evidence=Evidence(**ANCHOR),checks=[])
    task=ExpertTask("task", "logic_inspector","individual",[requirement])
    with pytest.raises(ReviewPipelineError,match="task coverage"):
        validate_task_coverage([task],[])
    with pytest.raises(ReviewPipelineError,match="role mismatch"):
        validate_task_coverage([task],[ExpertResult("task","scenario_builder")])
    observation=Observation("O-1","logic_inspector",Finding(kind="missing",severity="high",issue="Нет периода.",
        recommendation="Уточнить.",requirement_ids=["R-001"],criteria=["Полнота"],evidence=[Evidence(**ANCHOR)]))
    group=Group([observation])
    index=materialize_evidence_index(source(),[requirement],[group])
    validate_reduced_index(index,[group])
    with pytest.raises(ReviewPipelineError,match="lost"):
        validate_reduced_index(index,[])
    observation.finding.evidence=[Evidence(start=2,end=2,quote="R-2. Отобразить дату.")]
    with pytest.raises(ReviewPipelineError,match="changed validated evidence"):
        validate_reduced_index(index,[group])


@pytest.mark.asyncio
async def test_severe_unanimous_missing_claim_gets_source_aware_audit_without_discarding_provenance():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"]=[]
        if data["stage"]=="multi_gap_hunter_individual":
            result["requirements"][0]["checks"][0]["status"]="fail"
            result["findings"]=[{"kind":"missing","severity":"high","issue":"Не задана выписка.",
                "recommendation":"Уточнить.","requirement_ids":["R-001"],"criteria":["Полнота"],"evidence":[ANCHOR]}]
        elif data["stage"]=="multi_recheck":
            assert data.get("missing_claim_audit")
            assert any(ANCHOR["quote"] in unit["text"] for unit in data["source_context"]["units"])
            result.update(status="disagrees",comment="Условие явно указано.")
    report=await review_specification(source(),VariantChat(variant),review_mode="multi_agent")
    assert len(report.findings)==1
    assert report.evidence_graph.unresolved_count==1
    assert any(n.kind=="finding" and "не подтвердила заявленный пропуск" in (n.description or "") for n in report.evidence_graph.nodes)
    assert report.verdict.status!="ready"


def test_graph_citation_cannot_cross_unrelated_reference_ids():
    from agent.services.spec_evidence_graph import EvidenceGraph
    from pydantic import ValidationError
    nodes=[
      {"id":"S-001","source_id":"S-001","kind":"reference","title":"A"},
      {"id":"S-002","source_id":"S-002","kind":"reference","title":"B"},
      {"id":"RF-001","finding_id":"RF-001","source_id":"S-001","kind":"finding","title":"Finding"},
      {"id":"E-1","source_id":"S-002","evidence":ANCHOR,"kind":"evidence","title":"Quote"},
    ]
    with pytest.raises(ValidationError,match="unrelated reference"):
        EvidenceGraph.model_validate({"schema_version":1,"nodes":nodes,
              "edges":[{"source":"RF-001","target":"E-1","kind":"cites"}],
              "experts":[],"finalization_status":"completed","unresolved_count":0})


def test_reducer_does_not_merge_hidden_trailing_issue_or_overload_context():
    from agent.services.spec_multi_agent import (
        Group,Observation,_merge_candidates,select_reducer_context,MAX_CONSOLIDATION_PAYLOAD_CHARACTERS,
    )
    from agent.services.spec_review import Evidence, Finding
    quote=Evidence(**ANCHOR)
    def item(id_,issue):
        return Observation(id_,"logic_inspector",Finding(kind="missing",severity="high",issue=issue,
             recommendation="Уточнить.",requirement_ids=["R-001"],criteria=["Полнота"],evidence=[quote]))
    prefix="Один общий длинный контекст. "*30
    assert not _merge_candidates([Group([item("O-1",prefix+"Первый дефект.")]),
                                  Group([item("O-2",prefix+"Второй дефект.")])])
    groups=[]
    for n in range(32):
        observations=[item(f"O-{n:02d}-{j:02d}", ("Общий контекст. "*25) + chr(1040+n)) for j in range(8)]
        groups.append(Group(observations))
    payload, limited=select_reducer_context(groups,True)
    assert len(json.dumps(payload,ensure_ascii=False)) <= MAX_CONSOLIDATION_PAYLOAD_CHARACTERS
    assert len(payload["groups"]) < len(groups)
    assert limited


def test_reducer_considers_cross_kind_duplicate_with_identical_source_anchors():
    from agent.services.spec_multi_agent import Group, Observation, _merge_candidates
    from agent.services.spec_review import Evidence, Finding

    evidence = [Evidence(**ANCHOR)]
    left = Finding(kind="missing", severity="high", issue="Договор не определяет срок выписки.",
                   recommendation="Уточнить срок.", evidence=evidence, requirement_ids=["R-001"])
    right = left.model_copy(update={"kind": "ambiguous", "issue": "Не определён срок выписки по договору."})
    assert _merge_candidates([Group([Observation("O-001", "logic_inspector", left)]),
                              Group([Observation("O-002", "gap_hunter", right)])]) == [("O-001", "O-002")]


def test_reducer_considers_same_root_with_overlapping_multi_line_quotes_and_different_scopes():
    from agent.services.spec_multi_agent import Group, Observation, _merge_candidates
    from agent.services.spec_review import Evidence, Finding

    first = Evidence(start=1, end=1, quote="Приложение читает данные через сервис.")
    second = Evidence(start=2, end=2, quote="Операция сохранения данных описана отдельно.")
    combined = Evidence(start=1, end=2, quote=first.quote + "\n" + second.quote)
    left = Finding(kind="missing", severity="high", issue="Не описан контракт чтения данных.",
                   recommendation="Уточнить чтение.", evidence=[first, second], requirement_ids=["R-001"])
    right = left.model_copy(update={"kind": "ambiguous", "issue": "Контракт чтения данных не определён.",
                                    "evidence": [combined, second], "requirement_ids": ["R-002"]})
    assert _merge_candidates([Group([Observation("O-001", "logic_inspector", left)]),
                              Group([Observation("O-002", "gap_hunter", right)])]) == [("O-001", "O-002")]


def test_semantic_candidates_normalize_grouped_numbers_ids_and_absence_synonyms():
    from agent.services.spec_multi_agent import Group, Observation, _merge_candidates
    from agent.services.spec_review import Evidence, Finding

    evidence = [Evidence(start=1, end=1, quote="Лимит 50 000 тенге; адрес окружения: —.")]
    left = Finding(kind="missing", severity="high", issue="R-001: для лимита 50 000 тенге адрес окружения отсутствует.",
                   recommendation="Указать адрес.", evidence=evidence, requirement_ids=["R-001"])
    right = left.model_copy(update={"issue": "REQ-25: для лимита 50000 тенге не задан адрес окружения."})
    assert _merge_candidates([Group([Observation("O-001", "logic_inspector", left)]),
                              Group([Observation("O-002", "gap_hunter", right)])]) == [("O-001", "O-002")]


def test_deterministic_range_root_merges_only_same_proven_defect():
    from agent.services.spec_multi_agent import Group, Observation, merge_verified_static_roots
    from agent.services.spec_review import Evidence, Finding

    evidence = Evidence(start=3, end=3, quote="Допустимый диапазон лимита от 12 000 до 8 000 тенге.")
    code = Finding(kind="untestable", severity="high", issue="Допустимый диапазон пуст: нижняя граница превышает верхнюю.",
                   recommendation="Согласовать диапазон.", evidence=[evidence], requirement_ids=["R-003"])
    remarks = ["Верхняя граница 8 000 тенге ниже нижней 12 000 тенге.",
               "Диапазон лимита задан в обратном порядке, допустимых значений нет.",
               "Нижняя граница диапазона выше верхней: 12000 и 8000 тенге."]
    findings = [Group([Observation("O-code-0001", "code_checks", code)])]
    findings.extend(Group([Observation(f"O-{i:04d}", "logic_inspector", code.model_copy(update={"issue": issue}))])
                    for i, issue in enumerate(remarks, 1))
    other = code.model_copy(update={"issue": "Не определена единица измерения диапазона."})
    findings.append(Group([Observation("O-0004", "gap_hunter", other)]))
    groups = merge_verified_static_roots(findings)
    assert sorted(len(group.observations) for group in groups) == [1, 4]
    assert {obs.id for group in groups for obs in group.observations} == {"O-code-0001", "O-0001", "O-0002", "O-0003", "O-0004"}
    assert len(next(group for group in groups if len(group.observations) == 4).finding().evidence) == 1


def test_literal_empty_field_duplicates_merge_without_hiding_other_issues():
    from agent.services.spec_multi_agent import Group, Observation, merge_empty_field_roots
    from agent.services.spec_review import Evidence, Finding

    source = Evidence(start=11, end=11, quote="Dev: /api/read. Prod: —")
    blank = Finding(kind="missing", severity="medium", issue="Для Prod не задан адрес.",
                    recommendation="Указать Prod.", evidence=[source], requirement_ids=["R-010"])
    paraphrase = blank.model_copy(update={"issue": "URL Prod обозначен прочерком. Адрес интеграции отсутствует."})
    distinct = blank.model_copy(update={"issue": "Метод чтения для Dev не задан."})
    other_location = blank.model_copy(update={"evidence": [Evidence(start=12, end=12, quote=source.quote)]})
    groups = merge_empty_field_roots([Group([Observation(f"O-{index}", "logic_inspector", finding)])
                                      for index, finding in enumerate([blank, paraphrase, distinct, other_location], 1)])
    assert sorted(len(g.observations) for g in groups) == [1, 1, 2]
    assert {obs.id for g in groups if len(g.observations) == 2 for obs in g.observations} == {"O-1", "O-2"}


def test_consolidation_batches_fit_dense_duplicate_clusters_without_pair_truncation():
    from agent.services.spec_multi_agent import Group, Observation, consolidation_batches, _merge_candidates, MAX_MERGE_CANDIDATES
    from agent.services.spec_review import Evidence, Finding

    evidence = Evidence(start=1, end=1, quote="Операция не имеет определённого срока.")
    groups = [Group([Observation(f"O-{i:04d}", "logic_inspector", Finding(kind="missing", severity="high",
        issue="Не определён срок операции.", recommendation="Уточнить срок.", evidence=[evidence], requirement_ids=["R-001"]))]) for i in range(12)]
    batches = consolidation_batches(groups)
    assert sum(len(batch) for batch in batches) == len(groups)
    assert all(len(_merge_candidates(batch, limit=None)) <= MAX_MERGE_CANDIDATES for batch in batches)


@pytest.mark.asyncio
async def test_limited_consolidation_is_not_reported_as_provider_failure():
    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_set":
            template = result["findings"][0]
            result["findings"] = [{**template, "kind": "other", "severity": "low", "issue": f"Различный аспект {n}."} for n in range(35)]
        elif data["stage"] == "multi_gap_hunter_set":
            result["findings"] = []
    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    assert len(report.findings) == 35
    assert report.evidence_graph.finalization_status == "limited"


@pytest.mark.asyncio
async def test_three_medium_pairs_and_finalizer_execute_even_without_findings():
    def variant(data,result):
        if data["stage"].endswith("_set"):
            result["findings"]=[]
    chat=VariantChat(variant)
    report=await review_specification(source(),chat,review_mode="multi_agent")
    stages={data["stage"]:data for data in chat.stages if data["stage"].startswith("multi_pair_") or data["stage"]=="multi_finalizer"}
    assert set(stages)=={"multi_pair_1","multi_pair_2","multi_pair_3","multi_finalizer"}
    assert not report.findings
    assert stages["multi_pair_1"]["groups"]==[]
    assert {card["role"] for card in stages["multi_pair_1"]["coverage_cards"]}=={"logic_inspector","scenario_builder"}
    assert {card["role"] for card in stages["multi_pair_2"]["coverage_cards"]}=={"gap_hunter","contract_inspector"}
    third=stages["multi_pair_3"]["coverage_cards"]
    assert {card["role"] for card in third}=={"risk_inspector","reference_inspector"}
    assert next(card for card in third if card["role"]=="reference_inspector")["status"]=="skipped"
    assert len(stages["multi_finalizer"]["paired_summaries"])==3
    assert report.evidence_graph.finalization_status=="completed"


@pytest.mark.asyncio
async def test_finalizer_selects_verified_cards_without_merge_candidates_and_rejects_unknown_ids():
    def make_variant(invalid):
        def variant(data,result):
            if data["stage"].endswith("_set"):
                result["findings"]=[]
            if data["stage"]=="multi_logic_inspector_individual":
                result["findings"]=[{"kind":"ambiguous","severity":"medium","issue":"Неподтверждён формат выписки.",
                    "recommendation":"Зафиксировать формат.","clarification_question":"Какой формат выписки нужен?",
                    "requirement_ids":["R-001"],"criteria":["Однозначность"],"evidence":[ANCHOR]}]
            if data["stage"]=="multi_finalizer":
                assert not data["candidate_pairs"]
                assert len(data["groups"])==1
                id_=data["groups"][0]["id"]
                result.update(summary="Выдуманная декларация.",summary_ids=["UNKNOWN"] if invalid else [id_],
                              priority_ids=[id_],question_ids=[id_])
        return variant
    normal=await review_specification(source(), VariantChat(make_variant(False)),review_mode="multi_agent")
    assert len(normal.findings)==1
    assert normal.evidence_graph.finalization_status=="completed"
    assert "D-001" in normal.summary
    assert "Неподтверждён формат выписки." in normal.summary
    assert "Зафиксировать формат." in normal.summary
    assert "Какой формат выписки нужен?" in normal.summary
    assert "Выдуманная декларация" not in normal.summary
    invalid=await review_specification(source(), VariantChat(make_variant(True)),review_mode="multi_agent")
    assert invalid.findings==normal.findings
    assert invalid.verdict==normal.verdict
    assert invalid.evidence_graph.finalization_status=="deterministic_fallback"
    assert "UNKNOWN" not in invalid.summary


@pytest.mark.asyncio
async def test_repeated_bad_optional_gap_retains_valid_peer_and_uncertain_completeness(tmp_path):
    from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope
    specification=extract_specification("fixture.txt","text/plain",(SOURCE+"R-3. Сохранять статус.\n").encode())
    valid={"start":3,"end":3,"quote":"R-3. Сохранять статус."}
    invalid={"start":3,"end":3,"quote":"Выдуманная цитата."}
    def variant(data,result):
        if data["stage"]=="multi_gap_hunter_set":
            result["extraction_gaps"]=[valid,invalid]
    chat=VariantChat(variant)
    checkpoint=StageCheckpoint(tmp_path,provider_identity="fixture")
    with checkpoint_scope(checkpoint):
        report=await review_specification(specification,chat,review_mode="multi_agent")
    assert sum(item["stage"]=="multi_gap_hunter_set" for item in chat.stages)==2
    assert [item.model_dump() for item in report.extraction_gaps if item.start==3]==[valid]
    assert next(c for c in report.set_checks if c.criterion=="Полнота набора").status=="uncertain"
    assert next(c for c in report.set_checks if c.criterion=="Отсутствие дублирования").status=="pass"
    assert report.verdict.status!="ready"
    assert all(node.evidence is None or node.evidence.quote!="Выдуманная цитата." for node in report.evidence_graph.nodes)
    resumed=VariantChat(variant)
    with checkpoint_scope(checkpoint):
        second=await review_specification(specification,resumed,review_mode="multi_agent")
    assert not resumed.stages
    assert second.evidence_graph==report.evidence_graph


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_row",["empty","quote","mismatch"])
async def test_repeated_invalid_readiness_evidence_marks_only_owned_row_uncertain(bad_row):
    def variant(data,result):
        if data["stage"]=="multi_gap_hunter_set":
            row=result["readiness_checklist"][0]
            if bad_row=="empty":
                row["evidence"]=[]
            elif bad_row=="quote":
                row["evidence"]=[{"start":1,"end":1,"quote":"Выдуманная цитата."}]
            else:
                row["availability"]="missing"
    report=await review_specification(source(),VariantChat(variant),review_mode="multi_agent")
    rows={row.id:row for row in report.readiness_checklist}
    assert len(rows)==len(READINESS_CRITERIA)
    assert rows["functional_structure"].status=="uncertain"
    assert rows["functional_structure"].availability=="uncertain"
    assert rows["versioning"].status=="pass"
    assert all(item.quote!="Выдуманная цитата." for row in rows.values() for item in row.evidence)
    assert report.verdict.status!="ready"


@pytest.mark.asyncio
async def test_repeated_bad_set_check_citation_preserves_other_owned_row():
    def variant(data,result):
        if data["stage"]=="multi_gap_hunter_set":
            result["set_checks"][0]["evidence"]=[{"start":1,"end":1,"quote":"Выдуманная цитата."}]
    report=await review_specification(source(),VariantChat(variant),review_mode="multi_agent")
    rows={item.criterion:item for item in report.set_checks}
    assert len(rows)==len(SET_CRITERIA)
    assert rows["Полнота набора"].status=="uncertain"
    assert rows["Отсутствие дублирования"].status=="pass"
    assert rows["Полнота набора"].evidence==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid",["missing","duplicate"])
async def test_readiness_identity_coverage_remains_fail_closed(invalid):
    def variant(data,result):
        if data["stage"]=="multi_gap_hunter_set":
            if invalid=="missing":
                result["readiness_checklist"].pop()
            else:
                result["readiness_checklist"].append(dict(result["readiness_checklist"][0]))
    with pytest.raises(ValueError,match="Invalid or duplicate readiness checklist item"):
        await review_specification(source(),VariantChat(variant),review_mode="multi_agent")


@pytest.mark.asyncio
async def test_set_expert_recovers_readiness_identity_on_third_validated_attempt():
    attempts = 0

    def variant(data, result):
        nonlocal attempts
        if data["stage"] == "multi_scenario_builder_set":
            attempts += 1
            if attempts < 3:
                result["readiness_checklist"].append(dict(result["readiness_checklist"][0]))

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    assert attempts == 3
    assert {row.id for row in report.readiness_checklist} == set(READINESS_CRITERIA)


async def test_configured_review_defaults_to_multi_agent_and_old_passport_remains_legacy():
    from agent.config.settings import Settings
    from agent.services.spec_review import Passport, review_specification_configured

    report = await review_specification_configured(
        source(), ExpertChat(), Settings(_env_file=None, spec_review_use_nace=False),
    )
    assert report.passport.review_mode == "multi_agent"
    assert report.evidence_graph is not None
    historical = report.passport.model_dump(exclude={"review_mode"})
    assert Passport.model_validate(historical).review_mode == "legacy"


def test_candidate_retrieval_includes_narrower_repair_with_overlapping_scope():
    from agent.services.spec_multi_agent import Group, Observation, _merge_candidates
    from agent.services.spec_review import Evidence, Finding

    a = Evidence(start=11, end=11, quote="Сервис должен вернуть результат отчёта.")
    b = Evidence(start=12, end=12, quote="В ответе предусмотрены код 200 и код 400.")
    narrow = Finding(kind="ambiguous", severity="medium", issue="Не определён код ответа 200 для результата отчёта.",
                     recommendation="Уточнить код ответа для результата отчёта.", evidence=[b], requirement_ids=["R-002"])
    broad = Finding(kind="conflict", severity="high", issue="Для результата отчёта не определены коды ответа 200 и 400.",
                    recommendation="Уточнить код ответа для результата отчёта.", evidence=[a,b], requirement_ids=["R-001"])
    assert _merge_candidates([Group([Observation("O-1", "logic_inspector", broad)]),
                              Group([Observation("O-2", "scenario_builder", narrow)])]) == [("O-1", "O-2")]


@pytest.mark.asyncio
async def test_conflict_links_both_source_requirements_and_replaces_contradictory_comment():
    def variant(data, result):
        if data["stage"].endswith("_set"):
            result["findings"] = []
        if data["stage"] == "multi_logic_inspector_set":
            result["findings"] = [{
                "kind":"conflict", "severity":"critical", "issue":"Правила R-1 и R-2 несовместимы.",
                "recommendation":"Согласовать правило.", "criteria":["Корректность"],
                "requirement_ids":["R-002"], "evidence":[ANCHOR, {"start":2,"end":2,"quote":"R-2. Отобразить дату."}]
            }]
        if data["stage"] == "multi_logic_inspector_individual":
            for item in result["requirements"]:
                for row in item["checks"]:
                    if row["criterion"] == "Корректность":
                        row["comment"] = "Оба правила согласованы."
        if data["stage"] == "multi_adjudicate":
            result["decisions"][0]["evidence"] = data["findings"][0]["evidence"]
        if data["stage"] == "multi_recheck":
            result.clear()
            result["assessments"] = [{"id":row["id"], "status":"agrees", "comment":"Подтверждено расхождение исходных правил."}
                                     for row in data["disagreements"]]

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    assert set(report.findings[0].requirement_ids) == {"R-001", "R-002"}
    for requirement in report.requirements:
        check = next(row for row in requirement.checks if row.criterion == "Корректность")
        assert check.status == "fail"
        assert "согласованы" not in check.comment
        assert "расхождение" in check.comment
    # Initial expert comment is retained for audit, not presented as final reasoning.
    assert any(node.kind == "observation" and "Оба правила согласованы" in (node.description or "")
               for node in report.evidence_graph.nodes)


@pytest.mark.asyncio
async def test_unresolved_recheck_does_not_repeat_positive_comment_as_own_conclusion():
    report = await review_specification(source(), ExpertChat(), review_mode="multi_agent_direct")
    row = next(check for check in report.requirements[0].checks if check.criterion == "Полнота")
    assert row.status == "uncertain"
    assert "Проверено." not in row.comment
    assert "ручн" in row.comment


def test_candidate_retrieval_counts_distinct_shared_units_not_excerpt_objects():
    from agent.services.spec_multi_agent import Group, Observation, _merge_candidates
    from agent.services.spec_review import Evidence, Finding

    a = Evidence(start=10, end=10, quote="Описание метода обещает получение информации.")
    b = Evidence(start=11, end=11, quote="Тип запроса задан для обновления информации.")
    c = Evidence(start=12, end=12, quote="Указана операция записи данных.")
    d = Evidence(start=13, end=13, quote="Приложение запрашивает информацию.")
    combined = Evidence(start=10, end=11, quote=a.quote + "\n" + b.quote)
    narrow = Finding(kind="missing", severity="high", issue="Контракт получения информации не описан.",
                     recommendation="Указать параметры чтения.", evidence=[a,b,c], requirement_ids=["R-010"])
    broader = Finding(kind="conflict", severity="high", issue="Через API не определено получение информации.",
                      recommendation="Разделить операции и установить маршруты.", evidence=[combined,d], requirement_ids=["R-011"])
    assert _merge_candidates([Group([Observation("O-1", "gap_hunter", narrow)]),
                              Group([Observation("O-2", "contract_inspector", broader)])]) == [("O-1", "O-2")]


@pytest.mark.asyncio
async def test_failed_adjudication_batch_retries_individual_groups_without_losing_valid_decisions():
    def variant(data, result):
        if data["stage"] == "multi_logic_inspector_set":
            template = result["findings"][0]
            result["findings"] = [{**template, "issue": "Не определён предел 10."},
                                  {**template, "issue": "Не определён предел 20."}]
        elif data["stage"] == "multi_gap_hunter_set":
            result["findings"] = []
        elif data["stage"] == "multi_adjudicate":
            for finding, decision in zip(data["findings"], result["decisions"], strict=True):
                if "20" in finding["issue"]:
                    decision["evidence"] = [{"start":1,"end":1,"quote":"Несуществующая цитата."}]
    chat = VariantChat(variant)
    report = await review_specification(source(), chat, review_mode="multi_agent_direct")
    assert len(report.findings) == 2
    assert report.evidence_graph.adjudication.confirmed == 1
    assert report.evidence_graph.adjudication.unreviewed == 1
    assert any(len(stage["findings"]) == 1 for stage in chat.stages if stage["stage"] == "multi_adjudicate")


def test_merged_observations_do_not_create_cartesian_disputes_or_attach_context_only_requirements():
    from agent.services.spec_multi_agent import Group, Observation, _find_disputes, link_conflict_sources
    from agent.services.spec_review import Check, Evidence, Finding, Requirement

    a = Evidence(start=1, end=1, quote="Первое правило обработки.")
    b = Evidence(start=2, end=2, quote="Второе правило обработки.")
    c = Evidence(start=3, end=3, quote="Контекст параметра запроса.")
    reqs = [Requirement(id=f"R-{i:03}", source_id=None, text=e.quote, evidence=e,
                        checks=[Check(criterion=criterion, status="pass", comment="Проверено.")
                                for criterion in ["Корректность", "Полнота"]]) for i,e in enumerate([a,b,c],1)]
    conflict = Finding(kind="conflict", severity="high", issue="Правила несовместимы.",
                       recommendation="Согласовать правило.", evidence=[a,b], requirement_ids=["R-001"], criteria=["Корректность"])
    omission = Finding(kind="missing", severity="medium", issue="Не описано правило.",
                       recommendation="Согласовать правило.", evidence=[b,c], requirement_ids=["R-002"], criteria=["Полнота"])
    groups = [Group([Observation("O-1","logic_inspector",conflict),Observation("O-2","gap_hunter",omission)])]
    link_conflict_sources(groups, reqs)
    assert "R-003" not in groups[0].finding().requirement_ids
    assert {(d.requirement_id,d.criterion) for d in _find_disputes(groups,reqs,[])} == {
        ("R-001","Корректность"), ("R-002","Корректность"), ("R-002","Полнота")
    }


@pytest.mark.asyncio
async def test_counterevidence_resolves_inapplicable_finding_to_specific_check():
    def variant(data, result):
        if data["stage"] == "multi_recheck":
            result.clear()
            result["assessments"] = [{"id": row["id"], "status": "disagrees",
                                      "comment": "Этот критерий подтверждён указанным правилом.",
                                      "evidence": [ANCHOR]} for row in data["disagreements"]]
    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent_direct")
    row = next(c for c in report.requirements[0].checks if c.criterion == "Полнота")
    assert row.status == "pass"
    assert report.evidence_graph.unresolved_count == 0
    assert len(report.findings) == 1  # the separate source-grounded finding is retained


@pytest.mark.asyncio
async def test_residual_pair_review_recovers_duplicate_missed_by_broad_finalizer():
    def variant(data, result):
        if data["stage"] == "multi_gap_hunter_set":
            result["findings"][0]["issue"] = "Период выписки не определён."
        elif data["stage"] == "multi_finalizer" and data.get("repair_pair_check"):
            assert len(data["groups"]) == 2
            assert len(data["candidate_pairs"]) == 1
            result["merges"] = [list(data["candidate_pairs"][0])]
    chat = VariantChat(variant)
    report = await review_specification(source(), chat, review_mode="multi_agent_direct")
    assert len(report.findings) == 1
    assert any(stage.get("repair_pair_check") for stage in chat.stages)
    assert {node.role for node in report.evidence_graph.nodes if node.kind=="observation" and node.severity} == {
        "logic_inspector", "gap_hunter"
    }

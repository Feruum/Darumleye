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
        else:
            raise AssertionError(stage)
        return AIMessage(content=json.dumps(result, ensure_ascii=False), response_metadata={"finish_reason":"stop"})


@pytest.mark.asyncio
async def test_multi_agent_parallel_coverage_and_provenance():
    chat = ExpertChat()
    source = extract_specification("fixture.txt", "text/plain", SOURCE.encode())
    report = await review_specification(source, chat)
    assert report.passport.review_mode == "multi_agent"
    assert report.evidence_graph is not None
    for req in report.requirements:
        assert [c.criterion for c in req.checks] == list(INDIVIDUAL_CRITERIA)
    assert [c.criterion for c in report.set_checks] == list(SET_CRITERIA)
    assert [c.id for c in report.readiness_checklist] == list(READINESS_CRITERIA)
    assert chat.peak <= 3
    assert report.verdict.status != "ready"
    assert len(report.findings) == 1
    graph = report.evidence_graph
    node_ids = {node.id for node in graph.nodes}
    assert len(node_ids) == len(graph.nodes)
    assert all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)
    observations = [n for n in graph.nodes if n.kind == "observation"]
    assert {n.role for n in observations} == {"logic_inspector", "gap_hunter"}
    assert all(any(e.source == n.id and e.kind == "contributes_to" and e.target == "D-001" for e in graph.edges) for n in observations)
    assert all("already_identified" not in data for data in chat.stages if data["stage"].endswith(("_individual", "_set")))
    assert any(node.kind == "evidence" and node.evidence.quote == ANCHOR["quote"] for node in graph.nodes)


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
async def test_invalid_finalizer_merge_falls_back_without_losing_findings():
    def variant(data, result):
        if data["stage"] == "multi_gap_hunter_set":
            result["findings"][0]["issue"] = "Период получения выписки не определён."
        elif data["stage"] == "multi_finalizer":
            result["merges"] = [["O-unknown", "O-0001"]]

    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent")
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
@pytest.mark.parametrize("invalid_reference",[False,True])
async def test_reference_pair_keeps_normative_findings_separate(monkeypatch,invalid_reference):
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
    report = await review_specification(source(), VariantChat(variant), review_mode="multi_agent",
                                        references=[ReferenceDocument(reference_source, document)])
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

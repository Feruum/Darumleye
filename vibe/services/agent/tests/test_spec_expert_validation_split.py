import asyncio
import json
from collections import Counter

import pytest

from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope
from agent.services.spec_review import INDIVIDUAL_CRITERIA, review_specification
from test_spec_multi_agent import VariantChat, source


STAGE = "multi_logic_inspector_individual"


def split_fixture(*, invalid_leaf=False, chat_type=VariantChat):
    def variant(data, result):
        if data["stage"] != STAGE:
            return
        batch = data["requirements"]
        if len(batch) > 1:
            result["requirements"].pop()
            return
        requirement = batch[0]
        if invalid_leaf and requirement["id"] == "R-002":
            result["requirements"][0]["checks"].pop()
            return
        result["findings"] = [{
            "kind": "ambiguous", "severity": "medium",
            "issue": f"Уточнить условие {requirement['id']}.",
            "recommendation": "Указать критерий.", "evidence": [requirement["evidence"]],
            "requirement_ids": [requirement["id"]], "criteria": ["Однозначность"],
        }]
        result["counterexamples"] = [{
            "finding_index": 0, "scenario": requirement["id"], "outcomes": "Две интерпретации.",
        }]
    return chat_type(variant)


def called_batches(chat, stage=STAGE):
    return Counter(tuple(r["id"] for r in data["requirements"])
                   for data in chat.stages if data["stage"] == stage)


@pytest.mark.parametrize("mode", ["multi_agent", "multi_agent_direct"])
async def test_split_after_failed_correction_validates_all_rows_and_preserves_example_links(tmp_path, mode):
    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    chat = split_fixture()
    with checkpoint_scope(checkpoint):
        report = await review_specification(source(), chat, review_mode=mode, concurrency=3)
    assert called_batches(chat) == {("R-001", "R-002"): 2, ("R-001",): 1, ("R-002",): 1}
    assert chat.peak <= 3
    assert all(tuple(row.criterion for row in req.checks) == INDIVIDUAL_CRITERIA
               for req in report.requirements)
    graph = report.evidence_graph
    examples = [n for n in graph.nodes if n.kind == "counterexample"]
    assert len(examples) == 2
    for example in examples:
        req_id = example.description.splitlines()[0]
        assert any(edge.source == example.finding_id and edge.kind == "about" and edge.target == req_id
                   for edge in graph.edges)
    resumed = split_fixture()
    with checkpoint_scope(checkpoint):
        repeated = await review_specification(source(), resumed, review_mode=mode, concurrency=3)
    assert not resumed.stages
    assert repeated.evidence_graph == report.evidence_graph


async def test_resume_split_retries_only_unfinished_leaf_and_cannot_accept_missing_criterion(tmp_path):
    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    second_rejected = asyncio.Event()

    class DelayedValidSibling(VariantChat):
        async def ainvoke(self, messages, **kwargs):
            data = json.loads(messages[1].content)
            batch = tuple(req["id"] for req in data.get("requirements", []))
            if data["stage"] == STAGE and batch == ("R-001",):
                await asyncio.wait_for(second_rejected.wait(), 5)
                await asyncio.sleep(.01)
            response = await super().ainvoke(messages, **kwargs)
            if data["stage"] == STAGE and batch == ("R-002",) and "\nCORRECTION:" in messages[0].content:
                second_rejected.set()
            return response

    first = split_fixture(invalid_leaf=True, chat_type=DelayedValidSibling)
    with checkpoint_scope(checkpoint), pytest.raises(ValueError, match="Missing, duplicate or unexpected review criteria"):
        await review_specification(source(), first, review_mode="multi_agent_direct")
    assert called_batches(first) == {("R-001", "R-002"): 2, ("R-001",): 1, ("R-002",): 2}
    retry = split_fixture()
    with checkpoint_scope(checkpoint):
        report = await review_specification(source(), retry, review_mode="multi_agent_direct")
    assert called_batches(retry) == {("R-002",): 1}
    assert len([node for node in report.evidence_graph.nodes if node.kind == "counterexample"]) == 2


async def test_set_review_does_not_replace_global_coverage_with_split_local_passes():
    stage = "multi_logic_inspector_set"
    def variant(data, result):
        if data["stage"] == stage:
            result["set_checks"].pop()
    chat = VariantChat(variant)
    with pytest.raises(ValueError, match="Missing, duplicate or unexpected review criteria"):
        await review_specification(source(), chat, review_mode="multi_agent")
    assert called_batches(chat, stage) == {("R-001", "R-002"): 2}

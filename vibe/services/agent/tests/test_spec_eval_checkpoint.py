import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from openai import RateLimitError
from pydantic import BaseModel

from agent.config.settings import Settings
from agent.services.spec_review import configured_review_reasoning, invoke_stage, validated_retry
from agent.services.spec_eval_checkpoint import (
    StageCheckpoint,
    checkpoint_scope,
    RateLimitWaitingChat,
)


class Output(BaseModel):
    value: str


class FakeChat:
    model_name = "fixture"

    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        return AIMessage(content=json.dumps({"value": next(self.values)}))


@pytest.mark.parametrize("model", ["glm-5.3", "gpt-6-luna"])
@pytest.mark.parametrize(("stage", "expected"), [
    *((f"multi_{role}_set", "low") for role in (
        "logic_inspector", "scenario_builder", "gap_hunter", "contract_inspector", "risk_inspector")),
    ("reference", "low"), ("fix", None),
    *((f"multi_pair_{index}", "medium") for index in range(1, 4)),
    ("multi_finalizer", "medium"), ("multi_recheck", "medium"), ("multi_adjudicate", "medium"), ("individual", "low"),
])
async def test_provider_receives_stage_effort(model, stage, expected):
    class Capture:
        model_name = model

        async def ainvoke(self, messages, **kwargs):
            assert kwargs.get("reasoning_effort") == expected
            return AIMessage(content='{"value":"ok"}')

    assert (await invoke_stage(Capture(), stage, "prompt", {}, Output)).value == "ok"


async def test_unknown_model_keeps_default_effort():
    class Capture(FakeChat):
        async def ainvoke(self, *args, **kwargs):
            assert "reasoning_effort" not in kwargs
            return await super().ainvoke(*args, **kwargs)

    assert (await invoke_stage(Capture(["ok"]), "multi_finalizer", "prompt", {}, Output)).value == "ok"


@pytest.mark.parametrize("override,expected", [("auto", "medium"), ("inherit", "low"), ("low", "low")])
async def test_adjudication_reasoning_is_independent_of_expert_budget(override, expected):
    class Capture:
        model_name = "gpt-6-luna"

        async def ainvoke(self, messages, **kwargs):
            assert kwargs.get("reasoning_effort") == expected
            return AIMessage(content='{"value":"ok"}')

    settings = Settings(_env_file=None, spec_review_reasoning_effort="low",
                        spec_review_adjudication_reasoning_effort=override)
    with configured_review_reasoning(settings):
        assert (await invoke_stage(Capture(), "multi_adjudicate", "prompt", {}, Output)).value == "ok"


def test_changed_reasoning_effort_cannot_reuse_an_old_checkpoint(tmp_path):
    cache = StageCheckpoint(tmp_path, provider_identity="local-test")
    keys = {cache.key("glm-5.3", "expert", "prompt", {}, Output, reasoning_effort=effort)
            for effort in (None, "low", "medium")}
    assert len(keys) == 3


async def test_schema_correction_supplies_exact_contract_and_keeps_enclosed_json_strict():
    class WrappedJson:
        model_name = "glm-5.3"
        calls = 0

        async def ainvoke(self, messages, **options):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content='{"wrong":"value"}')
            assert '"properties":{"value":' in messages[0].content
            return AIMessage(content="```json\n{\"value\":\"ok\"}\n```")

    llm = WrappedJson()
    result = await validated_retry("multi_logic_inspector_set", "prompt",
        lambda prompt: invoke_stage(llm, "multi_logic_inspector_set", prompt, {}, Output))
    assert result.value == "ok" and llm.calls == 2


async def test_checkpoint_reuses_only_validated_result(tmp_path):
    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    first = FakeChat(["bad", "good"])

    async def run(llm, data):
        async def attempt(instructions):
            result = await invoke_stage(llm, "test", instructions, data, Output)
            if result.value != "good":
                raise ValueError("Quote is absent from the cited source")
            return result

        return await validated_retry("test", "Return value.", attempt)

    with checkpoint_scope(checkpoint):
        assert (await run(first, {"input": "a"})).value == "good"
    assert first.calls == 2
    assert len(list(tmp_path.glob("*.json"))) == 1
    second = FakeChat(["good"])
    with checkpoint_scope(checkpoint):
        assert (await run(second, {"input": "a"})).value == "good"
        assert second.calls == 0
        assert (await run(second, {"input": "b"})).value == "good"
        assert second.calls == 1


async def test_failed_validation_never_persisted(tmp_path):
    first = FakeChat(["bad", "bad"])

    async def attempt(instructions):
        await invoke_stage(first, "test", instructions, {"input": "a"}, Output)
        raise ValueError("Quote is absent from the cited source")

    with checkpoint_scope(StageCheckpoint(tmp_path, provider_identity="fixture")):
        with pytest.raises(ValueError):
            await validated_retry("test", "Return value.", attempt)
    assert not list(tmp_path.glob("*.json"))


async def test_rate_limit_waits_and_retries_once(monkeypatch):
    sleeps = []

    async def wait(seconds):
        sleeps.append(seconds)

    request = httpx.Request("POST", "https://fixture.invalid")
    response = httpx.Response(429, request=request, headers={"retry-after": "3"})
    calls = 0

    async def invoke(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitError(
                "limited",
                response=response,
                body={"error": {"code": "rate_limit_exceeded"}},
            )
        return "ok"

    wrapped = RateLimitWaitingChat(
        SimpleNamespace(model_name="fixture", ainvoke=invoke), sleep=wait
    )
    assert await wrapped.ainvoke([]) == "ok"
    assert calls == 2
    assert sleeps == [3.0]


def _contract(system_prompt):
    return json.loads(system_prompt.rsplit("GLM-5.3 authored output contract v1:\n",1)[1])


async def test_glm_first_individual_contract_uses_literal_registry_without_source_text():
    from agent.services.spec_multi_agent import ExpertIndividualDraft
    data={"criteria":("Проверяемость",),"allowed_finding_criteria":("Проверяемость","Однозначность"),
          "requirements":[{"id":"R-001","text":"PRIVATE_SOURCE_SENTENCE"},{"id":"R-002","text":"PRIVATE_REQUIREMENT"}],
          "units":[{"index":1,"text":"PRIVATE_UNIT"}]}
    class Capture:
        model_name="glm-5.3"
        async def ainvoke(self,messages,**kwargs):
            contract=_contract(messages[0].content); defs=contract["$defs"]
            assert "PRIVATE_" not in messages[0].content
            assert defs["IndividualReview"]["properties"]["id"]["enum"]==["R-001","R-002"]
            assert defs["Check"]["properties"]["criterion"]["enum"]==["Проверяемость"]
            assert defs["IndividualReview"]["properties"]["checks"]["minItems"]==defs["IndividualReview"]["properties"]["checks"]["maxItems"]==1
            assert contract["properties"]["requirements"]["minItems"]==contract["properties"]["requirements"]["maxItems"]==2
            assert defs["Finding"]["properties"]["criteria"]["items"]["enum"]==["Проверяемость","Однозначность"]
            assert kwargs["response_format"]=={"type":"json_object"} and kwargs["max_tokens"]==8192
            return AIMessage(content='{"requirements":[],"findings":[]}')
    await invoke_stage(Capture(),"multi_scenario_builder_individual","prompt",data,ExpertIndividualDraft)
    assert isinstance(data["criteria"],tuple)
    assert "enum" not in ExpertIndividualDraft.model_json_schema()["$defs"]["Check"]["properties"]["criterion"]


async def test_glm_set_contract_distinguishes_full_scoped_and_unassigned_rows():
    from agent.services.spec_multi_agent import ExpertSetDraft
    from agent.services.spec_review import SetDraft,SET_CRITERIA
    class Capture:
        model_name="glm-5.3"
        def __init__(self,scope,legacy=False):self.scope=scope;self.legacy=legacy
        async def ainvoke(self,messages,**kwargs):
            contract=_contract(messages[0].content); props=contract["properties"]
            assert props["set_checks"]["minItems"]==props["set_checks"]["maxItems"]==(6 if self.legacy else 0)
            if self.legacy:
                assert contract["$defs"]["SetCheck"]["properties"]["criterion"]["enum"]==list(SET_CRITERIA)
            assert props["readiness_checklist"]["maxItems"]==(0 if self.scope=="cross_section" else 2)
            if self.scope is None:
                assert props["readiness_checklist"]["minItems"]==2
                assert "readiness_checklist" in contract["required"]
            else:
                assert props["readiness_checklist"].get("minItems",0)==0
            return AIMessage(content='{"summary":"ok","set_checks":[],"findings":[]}')
    for scope in (None,"section","cross_section"):
        data={"criteria":(),"requirements":[],"readiness_criteria":{} if scope=="cross_section" else {"rollback":"PRIVATE_TEXT","operational_risk":"PRIVATE_TEXT"}}
        if scope:data["scope"]=scope
        await invoke_stage(Capture(scope),"multi_risk_inspector_set","prompt",data,ExpertSetDraft)
    await invoke_stage(Capture(None,True),"set","prompt",{"criteria":SET_CRITERIA,"requirements":[],"readiness_criteria":{"rollback":"PRIVATE","operational_risk":"PRIVATE"}},SetDraft)


async def test_glm_medium_contract_bounds_visible_candidate_ids_and_row_count():
    from agent.services.spec_multi_agent import ConsolidationDraft,RecheckDraft
    class Capture:
        model_name="glm-5.3"
        def __init__(self,stage):self.stage=stage
        async def ainvoke(self,messages,**kwargs):
            contract=_contract(messages[0].content);props=contract["properties"]
            assert kwargs["reasoning_effort"]=="medium"
            if self.stage=="multi_finalizer":
                assert props["summary_ids"]["items"]["enum"]==["O-0001","O-0002"]
                assert props["question_ids"]["items"]["enum"]==["O-0002"]
                assert props["merges"]["items"]["items"]["enum"]==["O-0001","O-0002"]
                assert props["disagreements"]["maxItems"]==0
                return AIMessage(content="{}")
            assert props["assessments"]["minItems"]==props["assessments"]["maxItems"]==2
            assert contract["$defs"]["RecheckAssessment"]["properties"]["id"]["enum"]==["C-001","C-002"]
            return AIMessage(content='{"assessments":[{"id":"C-001","status":"uncertain","comment":"ok"},{"id":"C-002","status":"uncertain","comment":"ok"}]}')
    await invoke_stage(Capture("multi_finalizer"),"multi_finalizer","prompt",
        {"groups":[{"id":"O-0001","question_available":False,"issue":"PRIVATE"},{"id":"O-0002","question_available":True}],
         "candidate_pairs":[["O-0001","O-0002"]],"disagreement_candidates":[],"coverage_cards":[{"role":"logic_inspector"}]},ConsolidationDraft)
    await invoke_stage(Capture("multi_recheck"),"multi_recheck","prompt",{"disagreements":[{"id":"C-001"},{"id":"C-002"}]},RecheckDraft)


async def test_prompt_contract_revision_does_not_reuse_precontract_checkpoint(tmp_path):
    cache=StageCheckpoint(tmp_path,provider_identity="fixture")
    old_key=cache.key("glm-5.3","individual","Return value.",{},Output,reasoning_effort="low")
    cache.save(old_key,Output(value="old"))
    class Chat(FakeChat):model_name="glm-5.3"
    async def run(chat):
        with checkpoint_scope(cache):
            return await validated_retry("individual","Return value.",
                lambda prompt: invoke_stage(chat,"individual",prompt,{},Output))
    first=Chat(["new"])
    assert (await run(first)).value=="new" and first.calls==1
    resumed=Chat([])
    assert (await run(resumed)).value=="new" and resumed.calls==0
    assert len(list(tmp_path.glob("*.json")))==2


async def test_correction_hint_only_contains_authored_schema_paths_and_type_codes():
    from agent.services.spec_multi_agent import ExpertIndividualDraft
    class Capture:
        model_name="glm-5.3"
        calls=0
        async def ainvoke(self,messages,**kwargs):
            self.calls+=1
            if self.calls==1:
                return AIMessage(content='{"requirements":[],"findings":[],"counterexamples":[{"finding_index":"SECRET_RAW_INPUT","scenario":"abc","outcomes":"def"}],"SECRET_RAW_EXTRA_KEY":"SECRET_RAW_VALUE"}')
            assert '"path":["counterexamples",0,"finding_index"],"code":"int_type"' in messages[0].content
            assert '<extra>' in messages[0].content
            assert "SECRET_RAW_" not in messages[0].content
            return AIMessage(content='{"requirements":[],"findings":[]}')
    chat=Capture()
    await validated_retry("multi_scenario_builder_individual","prompt",
        lambda prompt: invoke_stage(chat,"multi_scenario_builder_individual",prompt,{},ExpertIndividualDraft))
    assert chat.calls==2


@pytest.mark.parametrize(("risk", "complete", "mixed_conflict", "expected"), [
    ("low", True, False, "low"), ("high", True, False, "medium"),
    ("low", False, False, "medium"), ("low", True, True, "medium"),
])
async def test_auto_adjudication_spends_less_only_on_routine_complete_cases(risk, complete, mixed_conflict, expected):
    class Capture:
        model_name = "gpt-6-luna"

        async def ainvoke(self, messages, **options):
            assert options["reasoning_effort"] == expected
            return AIMessage(content='{"value":"ok"}')

    await invoke_stage(Capture(), "multi_adjudicate", "prompt", {
        "findings": [{"severity": risk, "kind": "other", "check_conflict_resolution": mixed_conflict}],
        "source_complete": complete,
    }, Output)

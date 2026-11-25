import asyncio
import json

import httpx
import pytest

from agent.services.spec_document import Specification
from agent.services.spec_reference_review import (
    ReferenceManifestEntry,
    ReferenceObservation,
    prepare_references,
)
from agent.services.spec_reference_decision import NaceReferenceVerifier


@pytest.fixture
def materials():
    specification = Specification(
        "spec.txt",
        "txt",
        ["Клиент получает уведомление.", "Срок: пятнадцать минут."],
    )
    metadata = ReferenceManifestEntry(
        category="bank_policy",
        title="Не подтверждённое утверждение о политике",
        issuer="Издатель",
        version="Редакция",
        scope="Учебное описание",
        source_url=None,
    )
    references = prepare_references(
        [metadata, metadata],
        [
            (
                "policy.txt",
                "text/plain",
                "Правило относится к уведомлениям.\nСрок: пять минут.".encode(),
            ),
            ("unrelated.txt", "text/plain", "Другой документ.".encode()),
        ],
    )
    first = ReferenceObservation(
        id="RF-001",
        source_id="S-001",
        status="potential_discrepancy",
        issue="Разные сроки уведомления.",
        recommendation="Уточнить срок.",
        requirement_ids=["R-001"],
        specification_evidence=[
            {"start": 2, "end": 2, "quote": "Срок: пятнадцать минут."}
        ],
        reference_evidence=[{"start": 2, "end": 2, "quote": "Срок: пять минут."}],
    )
    return specification, references, [first]


def noul(score):
    return {"type": "noul", "noul": score}


def provider(calls, answers, *, code=200, key="fixture", model="fixture-model"):
    async def respond(request):
        calls.append(request)
        return httpx.Response(code, json={"model": "actual-model", "answers": answers})

    return NaceReferenceVerifier(
        api_key=key,
        model=model,
        timeout=5,
        transport=httpx.MockTransport(respond),
    )


async def test_reference_verifier_reads_complete_origins_and_checks_every_rf_id(
    materials,
):
    specification, references, observations = materials
    second = observations[0].model_copy(
        update={"id": "RF-002", "status": "missing_evidence"}
    )
    third = observations[0].model_copy(
        update={"id": "RF-003", "status": "unclear_applicability"}
    )
    calls = []
    verifier = provider(
        calls,
        {
            "RF-003": noul(0.399),
            "RF-001": noul(0.8),
            "RF-002": noul(0.4),
        },
    )
    report = await verifier.verify(
        specification, references, [*observations, second, third]
    )
    assert report.status == "completed"
    assert report.provider == "nace"
    assert report.model == "actual-model"
    assert [(item.id, item.probability, item.status) for item in report.findings] == [
        ("RF-001", 0.8, "supported"),
        ("RF-002", 0.4, "uncertain"),
        ("RF-003", 0.399, "unsupported"),
    ]
    assert len(calls) == 1
    payload = json.loads(calls[0].content)
    assert list(payload["questions"]) == ["RF-001", "RF-002", "RF-003"]
    state = payload["state"]
    assert state["specification"]["units"] == specification.numbered_units()
    assert state["references"] == [
        {
            "id": "S-001",
            "source": references[0].document.source,
            "units": references[0].document.numbered_units(),
            "unreadable_pages": [],
        }
    ]
    assert "Не подтверждённое утверждение" not in json.dumps(state, ensure_ascii=False)
    assert (
        state["observations"][0]["specification_evidence"][0]["quote"]
        == "Срок: пятнадцать минут."
    )
    assert (
        state["observations"][0]["reference_evidence"][0]["quote"]
        == "Срок: пять минут."
    )
    assert state["observations"][0]["source_id"] == "S-001"


@pytest.mark.parametrize(
    "answers,code",
    [
        ({}, 200),
        ({"RF-001": noul(True)}, 200),
        ({"RF-001": noul(1.1)}, 200),
        ({"RF-001": noul(0.7), "RF-002": noul(0.9)}, 200),
        ({}, 503),
    ],
)
async def test_reference_verifier_is_unavailable_on_provider_failure(
    materials, answers, code
):
    calls = []
    report = await provider(calls, answers, code=code).verify(*materials)
    assert report.status == "unavailable"
    assert report.findings == []
    assert len(calls) == 1


async def test_reference_verifier_skips_empty_unconfigured_invalid_and_over_limit(
    materials, monkeypatch
):
    specification, references, observations = materials
    calls = []
    assert (
        await provider(calls, {}).verify(specification, references, [])
    ).status == "not_applicable"
    assert (
        await provider(calls, {}, key="").verify(*materials)
    ).status == "unavailable"
    first = observations[0]
    wrong_source = first.model_copy(update={"source_id": "S-003"})
    wrong_quote = first.model_copy(
        update={
            "reference_evidence": [
                first.reference_evidence[0].model_copy(
                    update={"quote": "Несуществующая цитата"}
                )
            ]
        }
    )
    for invalid in [[wrong_source], [wrong_quote], [first, first]]:
        result = await provider(calls, {}).verify(specification, references, invalid)
        assert result.status == "unavailable"
        assert result.findings == []
    assert (
        await provider(calls, {}).verify(specification, references, observations * 129)
    ).status == "unavailable"
    monkeypatch.setattr("agent.services.spec_reference_decision.MAX_REQUEST_BYTES", 1)
    assert (await provider(calls, {}).verify(*materials)).status == "unavailable"
    assert not calls


async def test_reference_verifier_respects_deadline_and_external_cancellation(
    materials,
):
    calls = []

    async def delayed(request):
        calls.append(request)
        await asyncio.sleep(10)

    verifier = NaceReferenceVerifier(
        api_key="fixture",
        model="fixture",
        timeout=0.01,
        transport=httpx.MockTransport(delayed),
    )
    result = await verifier.verify(
        *materials, deadline=asyncio.get_running_loop().time()
    )
    assert result.status == "unavailable"
    assert not calls
    result = await verifier.verify(*materials)
    assert result.status == "unavailable"
    assert len(calls) == 1
    task = asyncio.create_task(verifier.verify(*materials))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

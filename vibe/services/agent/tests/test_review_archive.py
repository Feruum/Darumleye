import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from agent.routes.spec_review import get_completed_spec_review, get_completed_reference_source, list_spec_reviews
from agent.server_auth import UserProfile
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.infrastructure.database import Base
from agent.services.review_archive import ReviewArchive, archive_specification, review_context
from agent.services.spec_reference_review import ReferenceManifestEntry, prepare_references
from agent.services.spec_document import Specification


@pytest.mark.parametrize("mode", ["multi_agent", "multi_agent_direct"])
async def test_archive_rejects_multi_agent_report_without_evidence_graph(mode):
    source = Specification("requirements.md", "md", ["REQ-1"])
    report = SimpleNamespace(model_dump=lambda **_: {"passport": {"review_mode": mode}, "evidence_graph": None})
    with pytest.raises(ValueError, match="evidence graph"):
        await ReviewArchive(None).save("owner", source, report)


async def test_archive_rejects_incomplete_and_unverified_multi_agent_graphs():
    source = Specification("requirements.md", "md", ["REQ-1"])
    graph = {"schema_version": 1, "nodes": [], "edges": [], "experts": [],
             "finalization_status": "completed", "unresolved_count": 0}
    data = {"passport": {"review_mode": "multi_agent"}, "requirements": [{"id": "R-001"}],
            "findings": [], "evidence_graph": graph}
    report = SimpleNamespace(model_dump=lambda **_: data)
    with pytest.raises(ValueError, match="graph.*coverage"):
        await ReviewArchive(None).save("owner", source, report)
    graph["nodes"] = [
        {"id": "R-001", "kind": "requirement", "title": "REQ-1", "requirement_id": "R-001"},
        {"id": "E-001", "kind": "evidence", "title": "fake", "evidence": {"start": 1, "end": 1, "quote": "not in source"}},
    ]
    with pytest.raises(ValueError, match="absent"):
        await ReviewArchive(None).save("owner", source, report)


@pytest.mark.asyncio
async def test_completed_reviews_are_persistent_and_owner_scoped(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'reviews.sqlite'}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    review_id = str(uuid4())
    source = Specification("payments.md", "md", ["Payment limits are 42.", "Retry requires confirmation."])
    report = {
        "filename": "payments.md", "passport": {"run_id": review_id, "run_at": "2026-11-25T12:00:00+00:00"},
        "summary": "Retry is not fully specified.", "statistics": {"requirements": 2, "violations": 1, "passed": 1, "needs_revision": 1},
        "findings": [{"severity": "high", "issue": "Missing timeout in retry requirement.", "recommendation": "Clarify retry.", "evidence": [{"start": 2, "end": 2, "quote": "Retry requires confirmation."}]}],
        "verdict": {"status": "needs_revision", "reason": "Missing timeout", "priorities": ["Clarify retry"]},
        "report_markdown": "# Review\nMissing timeout in retry requirement.",
    }
    async with sessions() as session:
        await ReviewArchive(session).save("owner", source, SimpleNamespace(model_dump=lambda **_: report))
    await engine.dispose()
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            archive = ReviewArchive(session)
            assert (await archive.list("owner"))[0]["id"] == review_id
            assert (await archive.list("owner"))[0]["analytics"] == {"passed": 1, "needs_revision": 1, "severity": {"critical": 0, "high": 1, "medium": 0, "low": 0}}
            listing = await list_spec_reviews(UserProfile("owner"), session)
            assert listing.headers["cache-control"] == "no-store"
            assert json.loads(listing.body)["reviews"][0]["id"] == review_id
            assert await archive.list("other") == []
            assert await archive.get("other", review_id) is None
            record = await archive.get("owner", review_id)
            assert record is not None
            detail = await get_completed_spec_review(UUID(review_id), UserProfile("owner"), session)
            assert detail.headers["cache-control"] == "no-store"
            payload = json.loads(detail.body)
            assert payload["report"] == report
            assert payload["source_units"] == source.units
            with pytest.raises(HTTPException) as denied:
                await get_completed_spec_review(UUID(review_id), UserProfile("other"), session)
            assert denied.value.status_code == 404
            result = review_context(record, "retry")
            assert "Retry requires confirmation" in result["context"]
            assert "Missing timeout" in result["context"]
            assert {source["filename"] for source in result["sources"]} == {"payments.md", "Отчёт: payments.md"}
    finally:
        await engine.dispose()


def test_retrieval_prioritizes_report_evidence_for_inflected_russian_query():
    record = SimpleNamespace(
        filename="payment.txt", source_format="txt",
        source_units=["Тестовое ТЗ.", "Оплата заказа в тенге.", "История платежей.", "Дневной лимит 100 000 тенге.", "Платёж проходит до 150 000 тенге за день."],
        report={"summary": "Есть противоречие.", "statistics": {}, "verdict": {},
                "report_markdown": "# Паспорт проверки\nМодель: fixture",
                "findings": [{"kind": "conflict", "issue": "Дневной лимит противоречит критерию 150 000 тенге.",
                              "recommendation": "Согласовать единое значение лимита.",
                              "evidence": [{"start": 4, "end": 4, "quote": "Дневной лимит 100 000 тенге."},
                                           {"start": 5, "end": 5, "quote": "Платёж проходит до 150 000 тенге за день."}]}]}
    )
    result = review_context(record, "Какие два дневных лимита указаны в исходном ТЗ, на каких строках?")
    snippets = [source.get("snippet", "") for source in result["sources"]]
    assert any("строка 4" in snippet and "100 000" in snippet for snippet in snippets)
    assert any("строка 5" in snippet and "150 000" in snippet for snippet in snippets)
    assert "Согласовать единое значение лимита" in result["context"]
    assert not any("Модель: fixture" in snippet for snippet in snippets)


async def test_reference_source_snapshot_is_complete_private_and_legacy_safe(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reference.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entry = ReferenceManifestEntry(category="bank_policy", title="Policy", issuer="Bank", version="2026", scope="Payments", source_url=None)
    references = prepare_references([entry], [("policy.txt", "text/plain", b"Context before\nExact quote\nContext after")])
    reference = references[0]
    source = Specification("spec.txt", "txt", ["Requirement"])
    data = {"passport": {"run_id": str(uuid4())}, "statistics": {"requirements": 1, "violations": 0},
            "verdict": {"status": "ready"}, "reference_review": {
                "sources": [reference.source.model_dump(mode="json")],
                "observations": [{"source_id": reference.source.id,
                                  "reference_evidence": [{"start": 2, "end": 2, "quote": "Exact quote"}]}]}}
    try:
        async with sessions() as session:
            archive = ReviewArchive(session)
            review_id = await archive.save("owner", archive_specification(source, references), SimpleNamespace(model_dump=lambda **_: data))
            result = await get_completed_reference_source(UUID(review_id), reference.source.id, UserProfile("owner"), session)
            assert result.headers["cache-control"] == "no-store"
            assert json.loads(result.body) == {"source_id": "S-001", "sha256": reference.source.sha256,
                                              "source_units": ["Context before", "Exact quote", "Context after"]}
            detail = await get_completed_spec_review(UUID(review_id), UserProfile("owner"), session)
            assert "Context before" not in detail.body.decode()
            for owner, source_id in [("other", "S-001"), ("owner", "S-999")]:
                with pytest.raises(HTTPException) as denied:
                    await get_completed_reference_source(UUID(review_id), source_id, UserProfile(owner), session)
                assert denied.value.status_code == 404
            data["passport"]["run_id"] = str(uuid4())
            legacy_id = await archive.save("owner", source, SimpleNamespace(model_dump=lambda **_: data))
            with pytest.raises(HTTPException) as missing:
                await get_completed_reference_source(UUID(legacy_id), "S-001", UserProfile("owner"), session)
            assert missing.value.status_code == 404
            data["passport"]["run_id"] = str(uuid4())
            data["reference_review"]["observations"][0]["reference_evidence"][0]["quote"] = "Invented"
            with pytest.raises(ValueError, match="absent"):
                await archive.save("owner", archive_specification(source, references), SimpleNamespace(model_dump=lambda **_: data))
    finally:
        await engine.dispose()

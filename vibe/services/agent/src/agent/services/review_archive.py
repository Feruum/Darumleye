"""Private completed specification reviews and bounded retrieval for follow-up chat."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
import json
from typing import TYPE_CHECKING
from uuid import uuid4

from langchain_core.documents import Document
from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import deferred

from agent.infrastructure.database import Base
from agent.retrieval.ranking import keyword_rank, tokens
from agent.services.spec_document import Specification
from agent.services.spec_evidence_graph import EvidenceGraph
from agent.services.spec_reference_review import ReferenceDocument
from agent.services.spec_review_mode import ACTIVE_REVIEW_MODES

if TYPE_CHECKING:
    from agent.services.spec_review import ReviewReport


class CompletedReview(Base):
    __tablename__ = "agent_completed_reviews"
    __table_args__ = (Index("ix_agent_completed_reviews_owner_created", "owner_id", "created_at"),)

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), nullable=False)
    filename = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    source_format = Column(String(8), nullable=False)
    verdict = Column(String(32), nullable=False)
    requirements = Column(Integer, nullable=False)
    findings = Column(Integer, nullable=False)
    source_units = Column(JSON, nullable=False)
    report = Column(JSON, nullable=False)
    # Large original snapshots are not fetched with general archive queries.
    reference_source_units = deferred(Column(JSON, nullable=True))


@dataclass(frozen=True)
class ArchiveSpecification(Specification):
    # Carry the validated inputs through the existing durable worker's
    # (Specification, ReviewReport) result, without changing its transaction.
    reference_documents: tuple[ReferenceDocument, ...] = ()


def archive_specification(specification: Specification, references: list[ReferenceDocument]) -> ArchiveSpecification:
    return ArchiveSpecification(**{field.name: getattr(specification, field.name) for field in fields(Specification)},
                                reference_documents=tuple(references))


def reference_snapshots(specification: Specification, data: dict) -> dict | None:
    if not isinstance(specification, ArchiveSpecification):
        return None  # Historic callers did not provide the reference originals.
    references = {item.source.id: item for item in specification.reference_documents}
    review = data.get("reference_review") or {}
    sources = {source["id"]: source for source in review.get("sources", [])}
    if set(references) != set(sources):
        raise ValueError("Reference snapshot sources do not match the report")
    for source_id, ref in references.items():
        if sources[source_id]["sha256"] != ref.source.sha256 or sources[source_id]["source"] != ref.document.source:
            raise ValueError("Reference snapshot identity does not match the report")
    for observation in review.get("observations", []):
        document = references[observation["source_id"]].document
        for evidence in observation["reference_evidence"]:
            document.verify_quote(evidence["start"], evidence["end"], evidence["quote"])
    for node in (data.get("evidence_graph") or {}).get("nodes", []):
        if node["kind"] == "evidence" and node.get("source_id") and node.get("evidence"):
            evidence = node["evidence"]
            references[node["source_id"]].document.verify_quote(evidence["start"], evidence["end"], evidence["quote"])
    return {source_id: {"sha256": ref.source.sha256, "source_units": ref.document.units}
            for source_id, ref in references.items()} or None


def _summary(record: CompletedReview) -> dict:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    summary = {
        "id": record.id,
        "filename": record.filename,
        "created_at": created_at.isoformat(),
        "verdict": record.verdict,
        "requirements": record.requirements,
        "findings": record.findings,
    }
    stats = record.statistics or {}
    if "passed" in stats and "needs_revision" in stats:
        severity = dict.fromkeys(("critical", "high", "medium", "low"), 0)
        for finding in record.report_findings or []:
            level = finding.get("severity")
            if level in severity:
                severity[level] += 1
        summary["analytics"] = {"passed": stats["passed"], "needs_revision": stats["needs_revision"], "severity": severity}
    return summary


def validate_archive_graph(specification: Specification, data: dict) -> None:
    """Prevent incomplete or unverified evidence from becoming an archived report."""
    raw = data.get("evidence_graph")
    if raw is None:
        if data["passport"].get("review_mode") in ACTIVE_REVIEW_MODES:
            raise ValueError("A multi-agent report must preserve its evidence graph")
        return
    graph = EvidenceGraph.model_validate(raw)
    reference = data.get("reference_review") or {}
    expected = {
        "requirement": {item["id"] for item in data.get("requirements", [])},
        "finding": {f"D-{index:03d}" for index, _ in enumerate(data.get("findings", []), 1)}
                   | {item["id"] for item in reference.get("observations", [])},
        "question": {item["id"] for item in data.get("clarification_questions", [])},
        "reference": {item["id"] for item in reference.get("sources", [])},
    }
    actual = {kind: {node.id for node in graph.nodes if node.kind == kind} for kind in expected}
    if actual != expected:
        raise ValueError("Evidence graph does not meet report item coverage")
    for node in graph.nodes:
        if node.kind == "evidence" and node.source_id is None and node.evidence:
            specification.verify_quote(node.evidence.start, node.evidence.end, node.evidence.quote)


class ReviewArchive:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, owner_id: str, specification: Specification, report: ReviewReport, *, commit: bool = True) -> str:
        if not owner_id:
            raise ValueError("An authenticated owner is required")
        data = report.model_dump(mode="json")
        validate_archive_graph(specification, data)
        review_id = data["passport"].get("run_id") or str(uuid4())
        record = CompletedReview(
            id=review_id,
            owner_id=owner_id,
            filename=specification.filename,
            created_at=datetime.now(UTC),
            source_format=specification.source,
            verdict=data["verdict"]["status"],
            requirements=data["statistics"]["requirements"],
            findings=data["statistics"]["violations"],
            source_units=specification.units,
            report=data,
            reference_source_units=reference_snapshots(specification, data),
        )
        self.session.add(record)
        if commit:
            await self.session.commit()
        return review_id

    async def list(self, owner_id: str) -> list[dict]:
        if not owner_id:
            return []
        records = await self.session.execute(
            select(CompletedReview.id, CompletedReview.filename, CompletedReview.created_at,
                   CompletedReview.verdict, CompletedReview.requirements, CompletedReview.findings,
                   CompletedReview.report["statistics"].label("statistics"),
                   CompletedReview.report["findings"].label("report_findings"))
            .where(CompletedReview.owner_id == owner_id)
            .order_by(CompletedReview.created_at.desc(), CompletedReview.id)
            .limit(200)
        )
        return [_summary(record) for record in records]

    async def get(self, owner_id: str, review_id: str) -> CompletedReview | None:
        if not owner_id or not review_id:
            return None
        return await self.session.scalar(
            select(CompletedReview).where(CompletedReview.owner_id == owner_id, CompletedReview.id == review_id)
        )


    async def reference_source(self, owner_id: str, review_id: str, source_id: str) -> dict | None:
        if not owner_id or not review_id or not source_id:
            return None
        # Retrieve only the requested source and only for the authenticated owner.
        snapshot = await self.session.scalar(
            select(CompletedReview.reference_source_units[source_id])
            .where(CompletedReview.owner_id == owner_id, CompletedReview.id == review_id)
        )
        return {"source_id": source_id, **snapshot} if isinstance(snapshot, dict) else None


CHUNK_CHARACTERS = 2200


def _chunks(text: str):
    for start in range(0, len(text), CHUNK_CHARACTERS):
        chunk = text[start:start + CHUNK_CHARACTERS].strip()
        if chunk:
            yield chunk


def _source_documents(record: CompletedReview) -> list[Document]:
    documents = []
    specification = Specification(record.filename, record.source_format, record.source_units)
    for index, unit in enumerate(record.source_units, 1):
        location = specification.location_text(index, index)
        for chunk in _chunks(unit):
            documents.append(Document(page_content=chunk, metadata={
                "filename": record.filename, "location": location, "unit": index,
                **({"page": index} if record.source_format == "pdf" else {}),
            }))
    return documents


def _report_documents(record: CompletedReview) -> list[Document]:
    return [Document(page_content=chunk, metadata={"filename": f"Отчёт: {record.filename}"})
            for chunk in _chunks(record.report["report_markdown"])]


def _finding_documents(record: CompletedReview) -> list[Document]:
    documents = []
    for index, finding in enumerate(record.report.get("findings", []), 1):
        evidence = finding.get("evidence", [])
        quotes = "\n".join(f"Место {item['start']}–{item['end']}: {item['quote']}" for item in evidence)
        documents.append(Document(page_content=(
            f"D-{index:03d}: {finding['issue']}\nРекомендация: {finding['recommendation']}\n{quotes}"
        ), metadata={"filename": f"Отчёт: {record.filename}", "evidence": evidence}))
    return documents


_GENERIC_TERMS = {"какие", "каких", "какой", "какая", "какое", "котор", "что", "как", "где", "два", "указа", "исход", "докум", "отчет", "строк", "требо", "ответ", "кратк", "прове"}


def _ranking_text(text: str) -> str:
    terms = (token.replace("ё", "е")[:5] for token in tokens(text) if len(token) >= 3)
    return " ".join(term for term in terms if term not in _GENERIC_TERMS)


def _rank_review_documents(documents: list[Document], query: str, count: int) -> list[Document]:
    """Use short lexical prefixes for inflected Russian terms in review questions."""
    normalized = [Document(page_content=_ranking_text(doc.page_content), metadata={"index": index})
                  for index, doc in enumerate(documents)]
    return [documents[doc.metadata["index"]] for doc in keyword_rank(normalized, _ranking_text(query), count)]


def review_context(record: CompletedReview, query: str) -> dict:
    """Keep the scope to the chosen review, with both original and generated evidence."""
    original = _source_documents(record)
    report = _report_documents(record)
    report_name = f"Отчёт: {record.filename}"
    overview = json.dumps({
        "summary": record.report["summary"],
        "statistics": record.report["statistics"],
        "verdict": record.report["verdict"],
    }, ensure_ascii=False)
    findings = _finding_documents(record)
    report_hits = _rank_review_documents(findings, query, 3)
    if not report_hits:
        report_hits = _rank_review_documents(report, query, 3) or report[:3]
    source_hits = []
    # Verified evidence locations attached to a matching finding are stronger than word overlap.
    for finding in report_hits:
        for evidence in finding.metadata.get("evidence", []):
            for doc in original:
                if evidence["start"] <= doc.metadata["unit"] <= evidence["end"] and doc not in source_hits:
                    source_hits.append(doc)
                    if len(source_hits) >= 4:
                        break
            if len(source_hits) >= 4:
                break
        if len(source_hits) >= 4:
            break
    for doc in (_rank_review_documents(original, query, 4) or original[:4]):
        if len(source_hits) >= 4:
            break
        if doc not in source_hits:
            source_hits.append(doc)
    hits = [*source_hits, *report_hits]
    context = [f"[S1] Источник: {report_name}\nСводка проверки: {overview}"]
    incomplete = bool(record.report.get("unreadable_pages") or record.report.get("unreadable_units"))
    if incomplete:
        context.append("Ограничение: часть исходного документа не прочитана; не делайте выводы о пропущенном содержимом.")
    if record.report.get("ocr_pages") or record.report.get("ocr_units"):
        context.append("Часть текста распознана OCR; цитаты могут отличаться от фотографий оригинала.")
    sources = [{"id": "S1", "filename": report_name, "snippet": record.report["summary"][:280]}]
    specification = Specification(record.filename, record.source_format, record.source_units)
    numbered_source = "\n".join(
        f"{specification.location_text(index, index)}: {unit}"
        for index, unit in enumerate(record.source_units, 1) if unit.strip()
    )
    # A short checked document fits in one request and avoids lexical misses caused by inflection.
    if len(numbered_source) <= 8_000:
        context.append(f"[S2] Источник: {record.filename}\n{'Извлечённый неполный текст' if incomplete else 'Полный текст документа'}:\n{numbered_source}")
        unit_label = 'страниц' if record.source_format == 'pdf' else 'блоков' if record.source_format == 'docx' else 'изображений' if record.source_format in {'jpg', 'jpeg', 'png'} else 'строк'
        sources.append({"id": "S2", "filename": record.filename, "snippet": f"{'Неполный текст' if incomplete else 'Полный текст'} · {len(record.source_units)} {unit_label}"})
    for hit in hits:
        metadata = hit.metadata
        name = metadata["filename"]
        location = metadata.get("location")
        source_id = f"S{len(sources) + 1}"
        context.append(f"[{source_id}] Источник: {name}{', ' + location if location else ''}\n{hit.page_content}")
        source = {"id": source_id, "filename": name, "snippet": f"{location}: {hit.page_content[:240]}" if location else hit.page_content[:240]}
        if "page" in metadata:
            source["page"] = metadata["page"]
        sources.append(source)
    return {"context": "\n\n".join(context), "sources": sources}

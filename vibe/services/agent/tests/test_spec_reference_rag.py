from hashlib import sha256

import chromadb
import pytest

from agent.services.spec_document import Specification
from agent.services.spec_reference_review import ReferenceDocument, ReferenceSource
from agent.services.spec_reference_rag import ReferenceRetriever


def reference(identifier, text, source="txt"):
    units = text if isinstance(text, list) else text.splitlines()
    source_hash = sha256("\n".join(units).encode()).hexdigest()
    return ReferenceDocument(
        ReferenceSource(
            id=identifier,
            category="bank_policy",
            title="Synthetic rule",
            issuer="Test",
            version="1",
            scope="Fixture",
            source_url=None,
            filename="rule.txt",
            source=source,
            sha256=source_hash,
            unreadable_pages=[],
            applicability="not_reviewed",
            summary="Not reviewed.",
        ),
        Specification("rule.txt", source, units),
    )


def specification(text):
    return Specification("spec.txt", "txt", text.splitlines())


def requirement(text):
    return {"id": "R-001", "text": text, "evidence": {"quote": text}}


def test_chroma_retrieves_bounded_relevant_passages_and_preserves_source_numbering(
    tmp_path,
):
    client = chromadb.PersistentClient(path=str(tmp_path / "index"))
    lines = ["Договор о техническом обслуживании. " * 25 for _ in range(10)]
    lines += ["Лимит операции проверяется до списания. Превышение лимита отклоняется."]
    a = reference("S-001", lines)
    b = reference("S-002", "Отчётность хранится согласно внутреннему регламенту.")
    reviewer = ReferenceRetriever(
        client, owner_id="owner-a", max_context_characters=1800
    )
    result = reviewer.retrieve(
        specification("Проверка лимита списания"),
        [requirement("Лимит операции проверяется до списания")],
        [a, b],
    )
    assert result.mode == "chroma_hybrid_local_features"
    assert set(result.passages) == {"S-001", "S-002"}
    assert all(result.passages.values())
    assert (
        sum(len(p["text"]) for rows in result.passages.values() for p in rows) <= 1800
    )
    assert result.coverage["S-001"]["partial"]
    assert (
        result.coverage["S-001"]["total_chunks"]
        > result.coverage["S-001"]["selected_chunks"]
    )
    assert result.coverage["S-002"]["partial"] is False
    result.verify_quote("S-001", 11, 11, "Лимит операции проверяется до списания.")
    with pytest.raises(ValueError):
        result.verify_quote("S-002", 1, 1, "Лимит операции проверяется до списания.")
    with pytest.raises(ValueError):
        result.verify_quote("S-001", 1, 1, "Лимит операции проверяется до списания.")
    with pytest.raises(ValueError):
        result.verify_quote("S-001", 11, 11, "Не существующая цитата")


def test_persistent_chunks_are_deduplicated_and_selected_owner_scoped(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "index"))
    a = reference("S-001", "Код лимита L1")
    a_again = reference("S-002", "Код лимита L1")
    b = reference("S-001", "ЧУЖАЯ ПОЛИТИКА: секретный код Q2")
    old = reference("S-001", "СТАРАЯ ПОЛИТИКА: код L1")
    doc = specification("Код лимита")
    first = ReferenceRetriever(client, owner_id="owner-a")
    first.retrieve(doc, [requirement("Код лимита")], [a])
    first.retrieve(doc, [requirement("Код лимита")], [old])
    second = ReferenceRetriever(client, owner_id="owner-b")
    second.retrieve(doc, [requirement("Код лимита")], [b])
    collection = client.list_collections()[0]
    assert client.get_collection(collection.name, embedding_function=None).count() == 3
    # Current selection excludes the same owner's previous document and all other owners.
    reopened = ReferenceRetriever(
        chromadb.PersistentClient(path=str(tmp_path / "index")), owner_id="owner-a"
    )
    result = reopened.retrieve(doc, [requirement("Код лимита")], [a_again])
    assert result.passages == {
        "S-002": [{"start": 1, "end": 1, "text": "Код лимита L1"}]
    }
    assert client.get_collection(collection.name, embedding_function=None).count() == 3


def test_long_pdf_page_is_chunked_without_losing_original_page_coordinates(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "index"))
    long_page = (
        "Нейтральная инструкция. " * 130
    ) + "Журнал транзакций документирует отмену."
    a = reference(
        "S-001", ["  ", long_page, "Порядок рассмотрения обращения"], source="pdf"
    )
    doc = specification("Журнал транзакций документирует отмену")
    reviewer = ReferenceRetriever(
        client, owner_id="owner-a", max_context_characters=1500
    )
    result = reviewer.retrieve(doc, [], [a])
    result.verify_quote("S-001", 2, 2, "Журнал транзакций документирует отмену.")
    assert result.coverage["S-001"]["partial"]
    assert all(row["start"] >= 2 for row in result.passages["S-001"])


def test_explicit_embedding_provider_separate_index_and_reranker_gets_only_selected_candidates(
    tmp_path,
):
    class Embedder:
        def __init__(self):
            self.calls = []

        def embed_documents(self, texts):
            self.calls.append(list(texts))
            return [[1.0, 0.0] if "лимит" in t.lower() else [0.0, 1.0] for t in texts]

    class Reranker:
        def __init__(self):
            self.seen = []

        def rerank(self, query, documents, top_k):
            assert top_k == len(documents)
            self.seen = [d.page_content for d in documents]
            return [(d, 1.0) for d in reversed(documents)]

    client = chromadb.PersistentClient(path=str(tmp_path / "index"))
    a = reference("S-001", "Лимит операций")
    b = reference("S-002", "Приватный источник")
    spec = specification("Лимит")
    ReferenceRetriever(client, owner_id="owner-a").retrieve(spec, [], [a])
    embedder = Embedder()
    reranker = Reranker()
    reviewer = ReferenceRetriever(
        client,
        owner_id="owner-a",
        embedder=embedder,
        embedding_identity="fixture-provider:model-v1",
        reranker=reranker,
    )
    reviewer.retrieve(spec, [], [b])
    result = reviewer.retrieve(spec, [requirement("Лимит")], [a])
    assert result.mode == "chroma_hybrid_configured_embeddings"
    assert len(client.list_collections()) == 2
    assert "Лимит операций" in reranker.seen
    assert "Приватный источник" not in reranker.seen
    assert len(embedder.calls) >= 3
    assert result.passages == {
        "S-001": [{"start": 1, "end": 1, "text": "Лимит операций"}]
    }


def test_blank_line_rich_reference_keeps_bounded_chunks_and_exact_locations(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "index"))
    lines = [line for _ in range(800) for line in ("Правило.", "")]
    lines += ["Лимит операции проверяется до списания."]
    a = reference("S-001", lines)
    doc = specification("Лимит операции проверяется до списания.")
    result = ReferenceRetriever(client, owner_id="owner-a").retrieve(doc, [], [a])
    assert result.coverage["S-001"]["total_chunks"] < 40
    result.verify_quote("S-001", 1601, 1601, lines[-1])

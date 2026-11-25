"""Owner-scoped Chroma retrieval for explicitly selected reference sources.

The original references remain the evidence authority. Chroma stores numbered,
content-addressed excerpts, and retrieves only the current owner's selected
versions. Without a configured embedding provider, the vector leg uses local
lexical feature vectors, not learned semantic embeddings.
"""

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import TYPE_CHECKING, Protocol

from langchain_core.documents import Document

from agent.retrieval.ranking import fuse_rankings, keyword_rank, tokens
from agent.services.spec_document import Specification, normalize_quote

if TYPE_CHECKING:
    from chromadb.api import ClientAPI

    from agent.services.spec_reference_review import ReferenceDocument

CHUNK_CHARACTERS = 750
MAX_CONTEXT_CHARACTERS = 12_000
MAX_QUERY_CHARACTERS = 1_000
MAX_RERANK_QUERY_CHARACTERS = 5_000
MAX_RERANK_CANDIDATES = 32
LOCAL_VECTOR_DIMENSIONS = 512
COLLECTION_PREFIX = "spec_reference_chunks_v1"


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def rerank(
        self, query: str, documents: list[Document], top_k: int
    ) -> list[tuple[Document, float]]: ...


@dataclass(frozen=True)
class RetrievedReferences:
    passages: dict[str, list[dict[str, int | str]]]
    coverage: dict[str, dict[str, int | bool]]
    mode: str

    def verify_quote(self, source_id: str, start: int, end: int, quote: str) -> None:
        """Evidence must occur in a supplied, correctly located retrieved passage.

        The original document's verifier must additionally check the exact
        cited unit range (a passage may span several original units).
        """
        normalized = normalize_quote(quote)
        if not normalized or start < 1 or start > end:
            raise ValueError("Invalid retrieved citation")
        for passage in self.passages.get(source_id, []):
            if int(passage["start"]) <= start <= end <= int(
                passage["end"]
            ) and normalized in normalize_quote(str(passage["text"])):
                return
        raise ValueError("Citation is absent from the retrieved source context")


class LocalFeatureEmbedder:
    """Deterministic multilingual lexical features (words and character prefixes).

    Hashing avoids a downloaded model, changing vocabulary, or network access.
    Character prefixes help match inflected terms in languages such as Russian.
    This is not a trained semantic embedding model.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        counts: Counter[str] = Counter()
        for term in tokens(text):
            counts[f"word:{term}"] += 2
            if len(term) >= 4:
                counts[f"prefix:{term[:4]}"] += 1
                for i in range(len(term) - 2):
                    counts[f"tri:{term[i : i + 3]}"] += 1
        vector = [0.0] * LOCAL_VECTOR_DIMENSIONS
        for feature, frequency in counts.items():
            digest = sha256(feature.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % LOCAL_VECTOR_DIMENSIONS
            sign = 1 if digest[4] % 2 else -1
            vector[index] += sign * (1 + math.log(frequency))
        length = math.sqrt(sum(value * value for value in vector))
        # A deterministic nonzero vector for non-word input keeps cosine valid.
        if not length:
            vector[0] = 1.0
            return vector
        return [value / length for value in vector]


def _split_unit(unit: str) -> list[str]:
    """Partition long lines/pages without rewriting original text."""
    parts = []
    start = 0
    while start < len(unit):
        end = min(start + CHUNK_CHARACTERS, len(unit))
        if end < len(unit):
            # Prefer a word boundary, without pathological tiny chunks.
            match = list(re.finditer(r"\s", unit[start + CHUNK_CHARACTERS // 2 : end]))
            if match:
                end = start + CHUNK_CHARACTERS // 2 + match[-1].end()
        part = unit[start:end]
        if part.strip():
            parts.append(part)
        start = end
    return parts


def _document_key(reference: "ReferenceDocument") -> str:
    # Include the extracted revision and source type. A parser change cannot
    # accidentally make old excerpts candidates for the new extraction.
    serialized = json.dumps(
        [reference.source.sha256, reference.document.source, reference.document.units, *([reference.document.unreadable_units] if reference.document.unreadable_units else [])],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(serialized.encode()).hexdigest()


def _chunks(reference: "ReferenceDocument", owner_id: str) -> list[Document]:
    document_id = _document_key(reference)
    ranges: list[tuple[int, int, str]] = []
    pending: list[str] = []
    first = last = 0

    def flush() -> None:
        nonlocal pending
        if pending:
            ranges.append((first, last, "\n".join(pending)))
            pending = []

    for index, unit in enumerate(reference.document.units, 1):
        if index in reference.document.unreadable_units:
            flush()
            continue
        if not unit.strip():
            # Text paragraph breaks are original, numbered units. Preserve them
            # within a chunk when possible rather than creating thousands of
            # tiny chunks. An unreadable PDF page must never be cited as part
            # of a readable passage.
            if (
                reference.document.source == "pdf"
                or not pending
                or sum(map(len, pending)) + len(pending) + len(unit) > CHUNK_CHARACTERS
            ):
                flush()
            else:
                pending.append(unit)
                last = index
            continue
        if len(unit) > CHUNK_CHARACTERS:
            flush()
            ranges.extend((index, index, part) for part in _split_unit(unit))
            continue
        if (
            pending
            and sum(map(len, pending)) + len(pending) + len(unit) > CHUNK_CHARACTERS
        ):
            flush()
        if not pending:
            first = index
        pending.append(unit)
        last = index
    flush()
    result = []
    for ordinal, (start, end, text) in enumerate(ranges, 1):
        identifier = sha256(
            f"{owner_id}\0{document_id}\0{ordinal}\0{start}\0{end}\0{text}".encode()
        ).hexdigest()
        result.append(
            Document(
                id=identifier,
                page_content=text,
                metadata={
                    "owner_id": owner_id,
                    "source_sha256": reference.source.sha256,
                    "document_id": document_id,
                    "start": start,
                    "end": end,
                    "ordinal": ordinal,
                },
            )
        )
    return result


def _queries(specification: Specification, requirements: list[dict]) -> list[str]:
    result = []
    for requirement in requirements:
        parts = [str(requirement.get("text") or "")]
        evidence = requirement.get("evidence")
        if isinstance(evidence, dict):
            quote = evidence.get("quote")
            if isinstance(quote, str):
                parts.append(quote)
        query = normalize_quote(" ".join(parts))[:MAX_QUERY_CHARACTERS]
        if query and query not in result:
            result.append(query)
    if not result:
        # The complete specification is partitioned for document-level fallback.
        text = "\n".join(specification.units)
        result = [
            text[index : index + MAX_QUERY_CHARACTERS]
            for index in range(0, len(text), MAX_QUERY_CHARACTERS)
            if text[index : index + MAX_QUERY_CHARACTERS].strip()
        ]
    return result or ["requirements"]


def _round_robin(rankings: list[list[Document]]) -> list[Document]:
    """Favor each requirement's first match before its lower-ranked matches."""
    selected = []
    seen = set()
    for rank in range(max(map(len, rankings), default=0)):
        for ranking in rankings:
            if rank < len(ranking):
                doc = ranking[rank]
                if doc.id not in seen:
                    selected.append(doc)
                    seen.add(doc.id)
    return selected


class ReferenceRetriever:
    """Index a selected source set and query it through the actual Chroma API."""

    def __init__(
        self,
        client: "ClientAPI",
        *,
        owner_id: str,
        embedder: Embedder | None = None,
        embedding_identity: str | None = None,
        reranker: Reranker | None = None,
        max_context_characters: int = MAX_CONTEXT_CHARACTERS,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("A trusted owner is required")
        if embedder is not None and not embedding_identity:
            raise ValueError("A configured embedding provider needs a stable identity")
        if not 0 < max_context_characters <= MAX_CONTEXT_CHARACTERS:
            raise ValueError("Invalid reference context budget")
        self.client = client
        self.owner_id = owner_id
        self.embedder = embedder or LocalFeatureEmbedder()
        self.reranker = reranker
        self.max_context_characters = max_context_characters
        if embedder is not None:
            identity = sha256(str(embedding_identity).encode()).hexdigest()[:16]
            self.collection_name = f"{COLLECTION_PREFIX}_embed_{identity}"
            self.mode = "chroma_hybrid_configured_embeddings"
        else:
            self.collection_name = f"{COLLECTION_PREFIX}_local"
            self.mode = "chroma_hybrid_local_features"

    def _source_filter(self, refs: list["ReferenceDocument"]) -> dict:
        return {
            "$and": [
                {"owner_id": self.owner_id},
                {"source_sha256": {"$in": sorted({ref.source.sha256 for ref in refs})}},
                {"document_id": {"$in": sorted({_document_key(ref) for ref in refs})}},
            ]
        }

    def _ordered_candidates(
        self, candidates: list[Document], queries: list[str]
    ) -> list[Document]:
        if self.reranker is None or not candidates:
            return candidates
        head = candidates[:MAX_RERANK_CANDIDATES]
        query = "\n".join(queries)[:MAX_RERANK_QUERY_CHARACTERS]
        # Reranking is optional: failures must not discard retrieval results.
        try:
            scores = self.reranker.rerank(query, head, top_k=len(head))
            known = {document.id: document for document in head}
            seen = set()
            reordered = []
            for document, _score in scores:
                if document.id in known and document.id not in seen:
                    reordered.append(known[document.id])
                    seen.add(document.id)
            return reordered + [
                document for document in candidates if document.id not in seen
            ]
        except Exception:
            return candidates

    def retrieve(
        self,
        specification: Specification,
        requirements: list[dict],
        references: list["ReferenceDocument"],
    ) -> RetrievedReferences:
        if not references:
            return RetrievedReferences({}, {}, self.mode)
        if len({ref.source.id for ref in references}) != len(references):
            raise ValueError("Duplicate reference source identity")
        by_source = {ref.source.id: _chunks(ref, self.owner_id) for ref in references}
        if not all(by_source.values()):
            raise ValueError("A selected reference contains no readable text")
        expected = {doc.id: doc for chunks in by_source.values() for doc in chunks}
        where = self._source_filter(references)
        collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
        existing = collection.get(ids=list(expected), where=where, include=[])
        missing = [
            expected[identifier]
            for identifier in expected.keys() - set(existing["ids"])
        ]
        if missing:
            # Keep insertion order stable, independent of Python hash randomization.
            missing.sort(key=lambda item: item.id or "")
            vectors = self.embedder.embed_documents(
                [item.page_content for item in missing]
            )
            collection.upsert(
                ids=[str(item.id) for item in missing],
                documents=[item.page_content for item in missing],
                metadatas=[item.metadata for item in missing],
                embeddings=vectors,
            )
        # Both the vector and BM25 legs operate on the selected Chroma corpus.
        records = collection.get(
            ids=list(expected), where=where, include=["documents", "metadatas"]
        )
        stored = {}
        for identifier, text, metadata in zip(
            records["ids"],
            records["documents"] or [],
            records["metadatas"] or [],
            strict=True,
        ):
            original = expected.get(identifier)
            if (
                original is None
                or text != original.page_content
                or metadata != original.metadata
            ):
                raise ValueError("Indexed reference no longer matches original source")
            stored[identifier] = Document(
                id=identifier, page_content=text, metadata=metadata
            )
        if set(stored) != set(expected):
            raise ValueError("Selected reference index is incomplete")
        queries = _queries(specification, requirements)
        query_vectors = self.embedder.embed_documents(queries)
        initial = min(len(stored), 12)
        vector_results = collection.query(
            query_embeddings=query_vectors,
            where=where,
            n_results=initial,
            include=[],
        )
        if len(vector_results["ids"]) != len(queries):
            raise ValueError("Unexpected retrieval result count")
        corpus = list(stored.values())
        rankings = []
        for query, vector_ids in zip(queries, vector_results["ids"], strict=True):
            dense = [
                stored[identifier] for identifier in vector_ids if identifier in stored
            ]
            lexical = keyword_rank(corpus, query, initial)
            rankings.append(fuse_rankings([dense, lexical], initial))
        candidates = _round_robin(rankings)
        candidates = self._ordered_candidates(candidates, queries)
        rank = {doc.id: index for index, doc in enumerate(candidates)}
        # Include at least one passage from each selected source, even when its
        # content did not rank globally. A partial source is never a negative
        # applicability determination by itself.
        passages: dict[str, list[Document]] = {source: [] for source in by_source}
        remaining = self.max_context_characters
        for source, chunks in by_source.items():
            top = min(chunks, key=lambda doc: rank.get(doc.id, len(candidates)))
            chosen = stored[str(top.id)]
            remaining -= len(chosen.page_content)
            if remaining < 0:
                raise ValueError(
                    "Reference context budget cannot represent every selected source"
                )
            passages[source].append(chosen)
        source_by_chunk: dict[str, list[str]] = {}
        for source, chunks in by_source.items():
            for chunk in chunks:
                source_by_chunk.setdefault(str(chunk.id), []).append(source)
        # Ranked passages, then remaining chunks in their original order. This
        # returns full coverage when the selected corpus fits the budget.
        seen = {doc.id for doc in candidates}
        ordered = candidates + [doc for doc in corpus if doc.id not in seen]
        for document in ordered:
            for source in source_by_chunk[str(document.id)]:
                if any(row.id == document.id for row in passages[source]):
                    continue
                if len(document.page_content) <= remaining:
                    passages[source].append(document)
                    remaining -= len(document.page_content)
        coverage = {}
        response = {}
        for source, chunks in by_source.items():
            selected = sorted(
                passages[source], key=lambda doc: int(doc.metadata["ordinal"])
            )
            response[source] = [
                {
                    "start": int(doc.metadata["start"]),
                    "end": int(doc.metadata["end"]),
                    "text": doc.page_content,
                }
                for doc in selected
            ]
            coverage[source] = {
                "total_chunks": len(chunks),
                "selected_chunks": len(selected),
                "total_characters": sum(len(doc.page_content) for doc in chunks),
                "selected_characters": sum(len(doc.page_content) for doc in selected),
                "partial": len(selected) < len(chunks),
            }
        return RetrievedReferences(response, coverage, self.mode)

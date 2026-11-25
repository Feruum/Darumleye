import json

import httpx
import pytest
from langchain_core.documents import Document

from agent.config.settings import Settings, settings
from agent.modules.rag import search_user_documents
from agent.retrieval.nace_reranker import NaceReranker
from agent.retrieval.reranker import Reranker, get_reranker


def documents():
    return [
        Document(
            page_content="Отчёт о рекламной кампании",
            metadata={"source": "ads.txt", "owner_id": "owner-a"},
        ),
        Document(
            page_content="Месячный лимит расходов карты 50 000 тенге",
            metadata={
                "source": "limits.pdf",
                "page": 1,
                "owner_id": "owner-a",
                "document_id": "doc-2",
            },
        ),
        Document(
            page_content="Уведомление при достижении лимита",
            metadata={"source": "limits.pdf", "page": 2, "owner_id": "owner-a"},
        ),
    ]


def answer(*scores):
    return {
        "model": "drex-max-v1.1",
        "answers": {
            f"p{i}": {"type": "noul", "noul": score} for i, score in enumerate(scores)
        },
        "usage": {"input_tokens": 200, "output_tokens": 20},
    }


def client_for(body, status=200, calls=None, key="test-key"):
    def handle(request):
        if calls is not None:
            calls.append(request)
        return httpx.Response(status, json=body)

    return NaceReranker(
        api_key=key, model="drex-latest", top_n=2, transport=httpx.MockTransport(handle)
    )


def test_nace_scores_candidates_once_and_preserves_exact_source_objects():
    calls = []
    docs = documents()
    result = client_for(answer(0.03, 0.98, 0.61), calls=calls).rerank(
        "Какой лимит?", docs
    )
    assert result == [(docs[1], 0.98), (docs[2], 0.61)]
    assert result[0][0] is docs[1]
    assert len(calls) == 1
    request = calls[0]
    assert request.url == "https://console.nace.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "drex-latest"
    assert payload["state"] == {
        "query": "Какой лимит?",
        "passages": {f"p{i}": doc.page_content for i, doc in enumerate(docs)},
    }
    assert set(payload["questions"]) == {"p0", "p1", "p2"}
    assert all(question["type"] == "noul" for question in payload["questions"].values())
    assert "owner_id" not in request.content.decode()
    assert "document_id" not in request.content.decode()


@pytest.mark.parametrize(
    "body,status",
    [
        (answer(0.4, 0.8), 200),
        (answer(0.4, True, 0.1), 200),
        (answer(0.4, 1.1, 0.1), 200),
        (answer(0.4, "0.8", 0.1), 200),
        ({"error": "unavailable"}, 503),
        ({"error": "no credits"}, 402),
        ({"error": "rate limit"}, 429),
    ],
)
def test_invalid_or_unavailable_provider_keeps_full_baseline_order(body, status):
    docs = documents()
    assert client_for(body, status).rerank("question", docs, top_k=2) == [
        (docs[0], 0.0),
        (docs[1], 0.0),
    ]


def test_empty_key_and_large_state_do_not_send_requests():
    calls = []
    docs = documents()
    assert client_for(answer(0.0, 1.0, 0.0), calls=calls, key="").rerank(
        "question", docs
    ) == [(docs[0], 0.0), (docs[1], 0.0)]
    oversized = [Document(page_content="x" * 40_000), docs[1]]
    assert client_for(answer(0.0, 1.0), calls=calls).rerank("question", oversized) == [
        (oversized[0], 0.0),
        (oversized[1], 0.0),
    ]
    assert calls == []


def test_ties_keep_original_order_and_empty_input_is_free():
    calls = []
    docs = documents()
    client = client_for(answer(0.5, 0.5, 0.5), calls=calls)
    assert client.rerank("question", [], top_k=2) == []
    assert client.rerank("question", docs, top_k=3) == [(d, 0.5) for d in docs]
    assert len(calls) == 1


def test_timeout_keeps_original_order_without_retry():
    calls = []
    docs = documents()

    def unavailable(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture timeout", request=request)

    client = NaceReranker(
        api_key="test-key",
        model="drex-latest",
        top_n=2,
        transport=httpx.MockTransport(unavailable),
    )
    assert client.rerank("question", docs) == [(docs[0], 0.0), (docs[1], 0.0)]
    assert len(calls) == 1


def test_existing_generic_provider_remains_available(monkeypatch):
    monkeypatch.setattr(settings, "rerank_provider", "generic")
    assert isinstance(get_reranker(), Reranker)


def test_nace_mode_only_reranks_candidates_from_authenticated_owner(monkeypatch):
    docs = documents()
    calls = {}

    class FakeIndex:
        def search(self, **kwargs):
            calls.update(kwargs)
            return docs

    monkeypatch.setattr("agent.modules.rag.get_knowledge_index", lambda: FakeIndex())
    monkeypatch.setattr(settings, "use_reranker", True)
    monkeypatch.setattr(settings, "rerank_provider", "nace")
    monkeypatch.setattr(settings, "retrieval_top_k_after_rerank", 2)
    monkeypatch.setattr(settings, "nace_api_key", "test-key")
    assert isinstance(get_reranker(), NaceReranker)
    monkeypatch.setattr(
        "agent.retrieval.reranker.get_reranker",
        lambda: client_for(answer(0.03, 0.98, 0.61)),
    )
    result = json.loads(
        search_user_documents.invoke({"query": "Какой лимит?", "user_id": "owner-a"})
    )
    assert calls["filter"] == {"owner_id": "owner-a"}
    assert result["sources"][0] == {
        "filename": "limits.pdf",
        "page": 2,
        "document_id": "doc-2",
    }
    assert "рекламной кампании" not in result["context"]


def test_nace_environment_key_is_independent_from_llm(monkeypatch):
    monkeypatch.setenv("NACE_API_KEY", "fixture-only")
    cfg = Settings(_env_file=None)
    assert cfg.nace_api_key == "fixture-only"
    assert cfg.nace_model == "drex-latest"

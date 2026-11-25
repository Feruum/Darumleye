"""Construct reference retrieval with explicitly configured providers."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from agent.config.settings import Settings
from agent.retrieval.nace_reranker import NaceReranker
from agent.services.spec_reference_rag import ReferenceRetriever

if TYPE_CHECKING:
    from chromadb.api import ClientAPI

ReferenceRetrieverFactory = Callable[[], ReferenceRetriever]


def configured_reference_retriever(
    configuration: Settings,
    owner_id: str,
    client_factory: "Callable[[], ClientAPI]",
) -> ReferenceRetrieverFactory:
    """Delay all network and index work until the optional branch starts."""

    def create() -> ReferenceRetriever:
        embedder = None
        identity = None
        if configuration.embedding_model:
            from langchain_openai import OpenAIEmbeddings

            url = configuration.embedding_base_url or configuration.llm_base_url
            embedder = OpenAIEmbeddings(
                model=configuration.embedding_model,
                api_key=configuration.embedding_api_key or configuration.llm_api_key,
                base_url=url,
                request_timeout=min(20.0, configuration.llm_timeout_seconds),
                max_retries=0,
                check_embedding_ctx_length=False,
            )
            identity = f"{url}|{configuration.embedding_model}"
        reranker = None
        if configuration.spec_review_use_nace:
            reranker = NaceReranker(
                api_key=configuration.nace_api_key,
                model=configuration.nace_model,
                top_n=32,
                timeout=min(8.0, configuration.nace_timeout_seconds),
            )
        return ReferenceRetriever(
            client_factory(),
            owner_id=owner_id,
            embedder=embedder,
            embedding_identity=identity,
            reranker=reranker,
        )

    return create


def local_reference_retriever() -> ReferenceRetriever:
    """In-process index for direct library use; CLI and HTTP use persistent clients."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return ReferenceRetriever(
        chromadb.EphemeralClient(ChromaSettings(anonymized_telemetry=False)),
        owner_id="local-review",
    )

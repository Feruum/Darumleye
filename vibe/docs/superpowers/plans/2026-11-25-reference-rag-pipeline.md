# Spec Review: complete pipeline and reference RAG

**Goal:** Keep complete, traceable requirements analysis while making the normative branch use the existing Chroma infrastructure.

**Architecture:** Validate and fingerprint the document, assign a run identifier, extract all requirements from original numbered units, plan independent batches, run deterministic checks and independent individual/set quality checks under a shared concurrency limit. The normative branch indexes explicitly selected reference documents in a separate owner-filtered Chroma collection and ranks bounded original passages per requirement using vector and keyword search. Drex may rerank the selected reference candidates and independently assess both core and normative findings. Original source evidence is checked by code; report status and coverage are explicit. A reference outage must not erase the core report.

**Tech stack:** FastAPI, existing ChromaDB and BM25, LangChain chat model, Nace System One, Bun transport, React report.

- [x] Test Chroma indexing, duplicate uploads, owner isolation, bounded retrieval, exact retrieved/source evidence, and local feature-vector fallback.
- [x] Test deterministic duplicate/identifier checks and bounded parallel batches.
- [x] Test reference retrieval orchestration, partial coverage, failed Chroma, timed-out provider, and stable primary verdict.
- [x] Add source hash and run ID to the report; clarify local lexical features versus configured semantic embeddings.
- [x] Add independent Drex assessments for normative findings and coverage to Markdown, JSON contracts, and the existing report UI.
- [x] Run Python tests, TypeScript contracts, Bun proxy, web tests, type checks, production build and template checks.
- [x] Run a small real review with the configured providers; distinguish successful stages from unavailable stages.

Only explicitly selected documents are indexed/retrieved. An uploaded reference is cached in a dedicated owner-scoped persistent collection; another owner's or unselected material must never be queried. Initial interface retains explicit reference selection by upload; cross-run library selection is not automatic. The CLI keeps its own local Chroma index. No approval of a legal obligation follows merely from retrieval.

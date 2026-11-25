# Python agent service

This service hosts the local modular agent runtime. FastAPI exposes internal endpoints, LangGraph runs registered modules, SQLAlchemy stores document metadata and graph checkpoints, and Chroma stores RAG vectors. The first module answers questions over private PDF and TXT uploads.

For the complete stack, use the root [README quick start](../../README.md#quick-start). The normal path builds and starts this service with Docker Compose as part of `bun run dev`.

## Local runtime

Requirements for direct Python development: Python 3.12+ and `uv`. Docker Compose supplies PostgreSQL and Chroma for the complete local scenario.

Service address: `http://localhost:8001`. The container listens on port `8000` and Compose publishes it on loopback only. Do not expose this internal service to the network or call it from the browser. Use the authenticated Bun API at `http://localhost:3000/api/agent`.

Bun validates the user's session before forwarding a request. It adds `X-API-Key` and the authenticated user's `X-User-ID`. FastAPI requires both headers on chat, module, and document endpoints. It uses the trusted user ID to scope documents, vector retrieval, thread ownership, and checkpoint state. Never accept a user ID from a model tool or browser payload as an owner assertion.

The following env values come from the root `.env.example` and `docker-compose.yml`:

| Setting | Purpose |
| --- | --- |
| `AGENT_API_KEY` | Internal shared key between Bun and FastAPI |
| `AGENT_LLM_BASE_URL` | OpenAI-compatible chat API origin |
| `AGENT_LLM_API_KEY` | Chat API credential |
| `AGENT_LLM_MODEL` | Chat model name; examples use `gpt-6-luna` through WikiVibe |
| `AGENT_LLM_TIMEOUT_SECONDS` / `AGENT_LLM_MAX_RETRIES` | Per-call timeout and retry policy; defaults 45 seconds / 2 retries |
| `AGENT_SPEC_REVIEW_TIMEOUT_SECONDS` | Total time limit for a specification review; default `230`, allowed range `30..240` |
| `AGENT_SPEC_REVIEW_CONCURRENCY` | Primary parallel calls, default 3, range 1..3; normative comparison is independent |
| `AGENT_SPEC_REVIEW_USE_NACE` | Independent Drex support checks of core and normative findings, plus reference candidate reranking; defaults to `false` |
| `AGENT_EMBEDDING_BASE_URL` | Optional embedding API origin; defaults to chat URL |
| `AGENT_EMBEDDING_API_KEY` | Optional embedding API credential; defaults to chat key |
| `AGENT_EMBEDDING_MODEL` | Embedding model name used when indexing/searching |
| `AGENT_USE_RERANKER` | Optional reranking; defaults to `false` |
| `AGENT_RERANK_PROVIDER` | `generic` (default) or `nace` |
| `NACE_API_KEY` | Separate Nace API credential, required only for Nace reranking |
| `AGENT_NACE_MODEL` | Nace decision model, defaults to `drex-latest` |
| `AGENT_NACE_TIMEOUT_SECONDS` | Nace HTTP timeout, defaults to `8`, allowed range `1..20` |
| `AGENT_USE_HYBRID_SEARCH` | Owner-filtered BM25 keyword + dense retrieval; defaults to `true` |
| `AGENT_RETRIEVAL_LEXICAL_MAX_CHUNKS` | Maximum owner corpus for local lexical ranking; defaults to `2000`, allowed range `1..10000` |

The chat API origin, key, and model must all be configured to enable responses. Without them, FastAPI still starts but chat returns `LLM_NOT_CONFIGURED`. Indexing also needs a compatible embedding model configured. Requests and retrieved text snippets are sent to the configured model provider.

Inside Compose, PostgreSQL is reached at `postgres:5432`, database `agent`; Chroma is reached at `chroma:8000`. The Postgres service creates the `agent` database on its first initialization. FastAPI creates its own missing SQLAlchemy tables at startup. Chroma keeps vectors in its `chroma_1_3_data` volume; uploaded files and related local state use `agent_local_data`.

## API shape

The web application talks to the Bun proxy, not these internal routes:

| Bun route | Internal FastAPI route | Purpose |
| --- | --- | --- |
| `GET /api/agent/modules` | `GET /api/v1/modules` | List registered modules |
| `GET /api/agent/documents` | `GET /admin/documents` | List the current user's documents |
| `POST /api/agent/documents` | `POST /admin/ingest` | Upload a PDF/TXT file, up to 25 MiB |
| `GET /api/agent/documents/:id` | `GET /admin/documents/:id` | Read indexing status |
| `DELETE /api/agent/documents/:id` | `DELETE /admin/documents/:id` | Remove one owned document |
| `POST /api/agent/spec-reviews` | `POST /api/v1/spec-reviews` | Requirements review (PDF, TXT, MD, DOCX, XLSX, CSV, JPG/JPEG, PNG up to 25 MiB; local OCR for scans and embedded Word photos) |
| `POST /api/agent/chat/stream` | `POST /api/v1/chat/stream` | Stream chat events as SSE |
| `GET /api/agent/memories` | `GET /api/v1/memories` | List the user's explicit private notes |
| `POST /api/agent/memories` | `POST /api/v1/memories` | Save `{ "content": "..." }`; owner comes from Bun |
| `DELETE /api/agent/memories/:id` | `DELETE /api/v1/memories/:id` | Delete one owned note |

The streaming route emits module/thread information, token chunks, tool status, sources, and errors. Health endpoints are available at `/health/live` and `/health/ready` for local process checks.

## Standalone specification review

New competition reviews default to the `multi_agent` LangGraph workflow on GPT-6 Luna in the example configuration, with independent experts, paired evidence validators, consolidation and Finalizer. Select `multi_agent_direct` to route expert results straight to global validation. Historical reports retain their stored workflow. Both execute extraction, individual quality checks, and set-wide review. The reviewer verifies exact quotes against original source positions, assigns stable report IDs, computes counts and readiness, and renders a Russian Markdown report; both active routes preserve an Evidence Graph. The selected default is a product decision, not proof of universal improvement from preliminary comparisons. The criteria are inspired by ISO/IEC/IEEE 29148; the report is not a certification. No result is silently limited to the top ten findings. An incomplete or invalid mandatory result fails the run. PDF pages without readable embedded text, standalone JPG/PNG photos, and embedded Word pictures use local OCR. A damaged page does not discard the remaining readable pages; when the text parser cannot open a file, an independent Poppler page-count probe can enable full-page OCR of an unencrypted PDF. Unreadable pages or image blocks prevent a ready verdict, and evidence cannot span missing content.

From the service directory, install dependencies and configure the chat API in `vibe/.env` (starting from `vibe/.env.example`). Run the actual source file through the agent:

```bash
uv sync --frozen
uv run python -m agent.spec_review_cli --input /path/to/requirements.md --output /path/to/report.md --env-file ../../.env
```

Use `--review-mode multi_agent_direct` for direct validation. Omitting the flag selects paired validation. A new request with the compatibility value `legacy` runs direct validation. Specify the mode explicitly during benchmarking. Use `.json` for the output extension to save the full structured response including the Markdown report. The CLI uses the same pipeline as the web application, but does not require Docker, PostgreSQL, Chroma, login, or an embedding model. It returns a nonzero exit code on failure and writes the report only after completing the mandatory stages. Source files are never overwritten. Set a model that supports JSON-mode chat completion and sufficient context for the document. The source contents are sent to the configured model provider.

Set `AGENT_SPEC_REVIEW_USE_NACE=true` with `NACE_API_KEY` to independently estimate whether the original text supports each final finding. The assessment is separate from the primary verdict and remains optional. If the decision provider is unavailable or the complete assessment cannot finish within the limits, the primary report is still written. The output includes the actual decision model and named probabilities, or an explicit unavailable/not-applicable status. See [SPEC_REVIEW.md](../../docs/SPEC_REVIEW.md#independent-decision-review-with-nace-drex).

Review limits: 25 MiB, 100,000 extracted characters, 150 PDF pages or DOCX images, 500 extracted requirements. JPG/PNG images are limited to 30 megapixels and 16,000 pixels per edge. Extraction scans source units; the legacy individual-review pipeline uses batches of six, and the multi-agent experts use their own bounded batches. Large documents use bounded original context, section checks and candidate cross-section comparisons; unresolved global relations remain uncertain. PDF OCR uses local Poppler and Tesseract (`rus`, `kaz`, `eng`) installed in the agent image; standalone CLI requires the same binaries and language packs. Rebuild the agent and apply migrations through `20261125_0007` for Office support and durable web review jobs. Files exceeding a limit stop with an error instead of returning a partial report.

Verify the pipeline and CLI with the controlled local provider tests:

```bash
uv run pytest tests/test_spec_review.py
```

These tests verify behavior and report structure, not the quality of a real model on the competition document.

## Durable web review jobs

The asynchronous primary web review uses PostgreSQL-owned admission, input, mode, status and renewable worker leases. Accepted source/reference bytes survive restarts. Validated stage checkpoints are saved under `AGENT_UPLOAD_DIR/spec-review-checkpoints` on `agent_local_data`. Both durable stores must be retained. A graceful shutdown requeues work; an abrupt stop permits another worker to claim after lease expiration. The same job UUID and selected mode are retained, the final archive is committed atomically, and temporary input/checkpoints are removed after terminal processing. New repeats retain a paired or direct passport mode. A historical legacy report, including a passport without mode metadata, repeats with `multi_agent_direct`. Resuming an existing durable job uses its stored execution mode, including legacy. See [SPEC_REVIEW.md](../../docs/SPEC_REVIEW.md#web-workspace).

## Retrieval and explicit memory

The RAG module merges Chroma dense retrieval with BM25L keyword matches through reciprocal rank fusion. Both paths use the authenticated owner filter. Existing vectors need no reindex. If the owner has more chunks than the configured lexical bound, the query uses dense-only results and logs `lexical_corpus_limit`; it does not rank a partial corpus. Optional reranking remains independent: choose `generic` for the existing `/rerank` provider, or `nace` for the Nace Drex decision model.

To enable Nace, obtain an API key in the [Nace console](https://console.nace.ai/dashboard), then set `NACE_API_KEY`, `AGENT_RERANK_PROVIDER=nace`, `AGENT_USE_RERANKER=true` and `AGENT_NACE_MODEL=drex-latest` in `vibe/.env`. `drex-max-finance` may be selected if it is available to the account. The model receives the query and candidate passage text only, not local document metadata. It ranks existing candidates by their relevance probabilities and preserves the source objects. On a missing key, provider error, timeout or incomplete response it returns the original retrieval order. Recreate the agent with `docker compose up -d --build agent` from `vibe/`. See [NACE_RAG.md](../../docs/NACE_RAG.md).

The **Your memory** card in the workspace saves explicit user notes in PostgreSQL. Notes survive restarts and can be deleted by their owner. The agent has a read-only `search_user_memory` tool; it cannot save/delete notes or copy uploaded content into memory. Notes supply context/preferences and are not document sources. Saving and managing notes needs no AI provider; using them in generated answers requires chat configuration.

The canonical implementation is here. The outer original project was removed after source reconciliation. See [RAG consolidation](../../docs/RAG_CONSOLIDATION.md) for provenance, limits, tests, and rebuilding an already-running agent image.

## Add a module

Each module defines its own prompt, tools, routing keywords, and source reporting. Add a module definition under `src/agent/modules/` using `AgentModule` from `agent.modules.base`, then return it from `get_modules()` in `agent.modules.registry`.

```python
from agent.modules.base import AgentModule

MY_MODULE = AgentModule(
    id="my-module",
    name="My module",
    description="A short description shown in the module catalog.",
    instructions="Module-specific system instructions.",
    tools=(my_tool,),
    routing_keywords=("keyword", "ключевое слово"),
)
```

Add the module to the registry. Explicit `module_id` values are validated against that registry. Automatic routing scores each module's keywords and uses the module marked `default_for_auto` when no keywords match. Keep one default module.

For tools that need a user identity, list the trusted argument name in `trusted_tool_arguments`. The orchestrator overwrites that tool argument with the authenticated `user_id` from graph state. Never use a model-provided owner ID. For tools that return citations, add their names to `source_tools` and return JSON containing `context` and `sources` so the streaming API can emit citation events.

## Develop and verify

Install dependencies and run the Python service tests from this directory:

```powershell
uv sync --frozen
uv run pytest
```

The project targets Python 3.12+. Integration-marked tests are excluded by default (`pytest.ini_options.addopts`); provision and select their external services explicitly when adding integration coverage.

To inspect local service logs from the application directory, `vibe/`:

```powershell
docker compose logs --tail 100 agent
docker compose logs --tail 100 chroma
```

To stop local infrastructure while keeping state, run `docker compose stop agent chroma postgres` from `vibe/`. `docker compose down -v` removes the persistent database, vector index, and uploads.

## Scope

This service accepts development/test configuration only and requires the Chroma backend. Azure Search and Azure identity/storage/Service Bus adapters remain inactive legacy source. The separate original Go authorization server and Next.js dashboard were retired; Bun and React own those application boundaries. Cloud deployment is not part of this local setup.

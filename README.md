# Spec Review — FinTech requirements validator

Spec Review uses **`multi_agent` (paired validation)** by default on GPT-6 Luna. Select **Прямая валидация** in the workspace or `--review-mode multi_agent_direct` in the CLI to send the independent experts’ observations straight to the global evidence validator. Both routes use the same Evidence Graph, Finalizer and verdict rules. [Spec Review architecture](vibe/docs/MULTI_AGENT_REVIEW.ru.md)

Spec Review is a static requirements-review agent for the BCC-HUB × MNU FinTech Hackathon. It reads PDF, Markdown, UTF-8 text, DOCX, XLSX, CSV, JPG/JPEG and PNG, extracts individual requirements, checks each requirement and the set as a whole, validates source quotations, and writes a Russian-language report. Scanned PDF pages, photos and embedded DOCX pictures use local OCR; unreadable pages or image units prevent a ready verdict. The React workspace adds an interactive report inspector and report download. The original TemplateHack RAG and account modules remain available.

## Reproduce the hackathon run

The document-only CLI requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and one OpenAI-compatible model with JSON output. It does not require Docker, PostgreSQL, Chroma, Bun, a browser, or embeddings. The official Markdown case needs no OCR. To review images and scanned PDFs locally, install Tesseract with Russian, Kazakh and English language packs and Poppler for PDF rasterization; see [input limits and verification](vibe/docs/SPEC_REVIEW.md#limits-and-verification).

1. Obtain a WikiVibe key for `gpt-6-luna`. Multi-agent extraction and expert reviews use low reasoning effort; paired evidence validators, Finalizer and targeted re-review use medium. Other compatible JSON-capable models (including `glm-5.3`) can be selected with a matching gateway and key.
2. Copy the root `.env.example` to `.env` and fill `AGENT_LLM_API_KEY`. Change the base URL and model if needed. Keep `.env` private.
3. Obtain the [organizers' source document](https://github.com/BCC-HUB/ai-requirements-validator-task/blob/main/docs/requirements.md). For example, save it as `.artifacts/requirements.md` (this directory is ignored by Git).
4. From the repository root, run:

```bash
uv run --project vibe/services/agent --frozen python -m agent.spec_review_cli --input .artifacts/requirements.md --output .artifacts/report.md --env-file .env
```

The agent writes `.artifacts/report.md` using the current `multi_agent` default. Set `--review-mode multi_agent_direct` for direct validation. On a new request, the compatibility spelling `legacy` selects direct validation and the passport records `multi_agent_direct`. Existing legacy reports retain their original mode. Set `--output .artifacts/report.json` for the structured representation with the same Markdown report. For a shorter command, use `cd vibe && bun run review ../.artifacts/requirements.md` (Bun is optional; the direct CLI above remains supported). Without `--output`, a Markdown report named `<input>.review.md` is saved alongside the document; existing reports get numbered suffixes. The CLI shows real stage/request progress, elapsed time, and a summary. Use `--format json` for automatic JSON output, `--quiet` for just the saved path, or `--print-report` for the complete report on stdout, with progress on stderr. On invalid input or incomplete model output the CLI exits nonzero without writing a new report. For a full document on a slow or rate-limited provider, see the [complete organizer evaluation](vibe/docs/SPEC_REVIEW.md#complete-organizer-evaluation) with opt-in checkpoints and the independent report audit. Submit the generated report and the source document alongside the repository link, not as replacements for agent source.

Optional independent finding verification: set `AGENT_SPEC_REVIEW_USE_NACE=true` and `NACE_API_KEY` in the same configuration file. Drex scores the support of each finding against the original document; disagreement is reported without removing findings or changing the main verdict. See [decision review and source boundaries](vibe/docs/SPEC_REVIEW.md#independent-decision-review-with-nace-drex).

Optional reference review: pass `--reference-manifest PATH` for a JSON manifest of explicitly selected standards, approved bank policies, API/testing documents and legal/regulatory source files. Selected references are indexed in ChromaDB, ranked per requirement with hybrid retrieval, and optionally reranked by Drex. Each candidate discrepancy cites both documents, retrieval coverage is explicit, and the separate section does not alter the core report. A local lexical-feature fallback works without a trained embedding model. See the [reference corpus guide](vibe/docs/REGULATORY_CONTEXT.md) and [manifest example](vibe/docs/examples/reference-manifest.example.json). Omit the option for the official document-only run.

For the web workspace, configure the same model values in `vibe/.env`, follow the local stack setup below, and open `/app/assistant`. See [review architecture, limits, and checks](vibe/docs/SPEC_REVIEW.md).

Optional web workflow: select a finding in a completed report, request an AI correction or analyst clarification, accept individual proposals, export a separate revised specification, and repeat the original review. The original source and report are unchanged. See [correction and re-review](vibe/docs/SPEC_REVIEW.md#optional-correction-and-re-review) and the [quality evaluation guide](vibe/docs/QUALITY_EVALUATION.md) for the paired synthetic benchmark and measured-effort methodology.

For the hackathon business-value hypothesis, see the [Kazakhstan-based cost and productivity scenario](vibe/docs/BUSINESS_VALUE.ru.md). Observed model runtime is separated from explicitly estimated analyst effort and public comparable API prices.

---

## Original application foundation

A local hackathon application with a public site, account registration, a private workspace, and a modular AI-agent backend. Its initial RAG module processes each user's PDF and TXT documents and streams answers with source citations when a model provider is configured.

Repository: [Feruum/TemplateHack](https://github.com/Feruum/TemplateHack).

Interface conventions and library responsibilities are recorded in [design.md](vibe/design.md).

## Interface libraries

The active site and workspace use **Arc Free** copied MIT components. Chats stay on **assistant-ui**. Missing Free equivalents use **shadcn/ui**: Sidebar/Sheet support, Field, Item, Separator, Spinner, and specialized AlertDialog. The [migration and fallback report](vibe/docs/ARC_MIGRATION.md) records what is used and why; [design.md](vibe/design.md) defines maintenance rules.

The Arc migration checks passed locally on 2026-10-16: full `bun run check`, followed by **97 webapp tests**, final types/lint/builds, **18 distinct workspace browser scenarios across the full run and targeted reruns**, and **7 production-site browser cases**. Mobile focus/Escape regressions passed three repetitions at each of two widths. [Verification evidence](vibe/docs/VERIFICATION.md) records scope, repaired defects, and remaining AI configuration.

The signed-in RAG chat uses **assistant-ui 0.15.29** for its runtime, composer, streaming messages, and cancellation. **Arc Free EmptyState and Badge**, with **Motion 14.3.0**, provide empty states and status badges. React/Vite, the authenticated Bun proxy, and the Python/LangGraph backend keep their existing roles. assistant-ui Cloud is not configured.

Earlier RAG integration checks on 2026-10-16 passed: **91 webapp tests and 4 Chromium scenarios**, including mobile, sources/tools, uploads, explicit note save/reload/delete, cancellation, and HTTP errors. Type checks and production builds passed. Private agent caches clear on sign-out and account replacement.

See [the chat UI guide](vibe/docs/CHAT_UI.md) for architecture, behavior, limits, and checks; [Arc source provenance](vibe/webapp/src/components/arc/README.md) records the MIT license and local theme/icon/Typography adaptations.

## Datathon preparation

The repository includes a standalone [tabular data workflow](vibe/datathon/README.md): CSV/TSV/JSON loading, EDA charts, classification/regression baselines, holdout evaluation, group/time splits, and optional unlabeled predictions. It runs without Docker or AI keys after installing its isolated Python environment.

From `vibe/`, with Python 3.12+ available:

```powershell
bun run datathon:setup
bun run datathon:test
bun run datathon:demo
```

Demo creates explicitly synthetic reports under ignored `.artifacts/datathon-demo-*`. For competition data, use `bun run datathon:eda` and `bun run datathon` with the actual target, task, and metric. The [workflow guide](vibe/datathon/README.md) explains leakage precautions, artifacts, and submission limits. Confirm the organizer's rules for prepared code; synthetic success does not prove readiness for the unreleased case.

## What works and what still needs configuration

The RAG implementation is consolidated inside `vibe/services/agent`. It includes owner-filtered dense/keyword search and explicit private memory notes, with controls in the React workspace. The outer `agentic-rag/` reference directory has been retired. See the [source reconciliation and operating guide](vibe/docs/RAG_CONSOLIDATION.md); an already-running older agent image must be rebuilt to load these changes.

The active application is `vibe/`. The combined RAG/interface check on **2026-10-16** passed **566 Bun tests and 2 build-contract tests**, types, lint, workspace builds, architecture validation, and the dependency audit. The agent passed **43 Python tests** with one PostgreSQL-only case skipped in that run; the separate PostgreSQL suite passed **5 tests**, including that concurrency case. Four Chromium scenarios passed against the combined frontend. The earlier datathon check passed **24 Python tests**, and both synthetic tasks produced reports and reproducibility artifacts. See [dated verification evidence](vibe/docs/VERIFICATION.md) for test boundaries and remaining provider requirements.

The React/Vite migration check on **2026-10-12** also verified the website Storybook build and public-site browser behavior: desktop/mobile navigation, FAQ controls, hydration, and static fallback with JavaScript blocked, reduced motion, and WebGL disabled. Those browser scenarios were not rerun for the standalone data workflow.

The broader **2026-10-07** audit also passed 34 Python tests, 10 account/workspace Chromium scenarios, and 12 RAG runtime checks with a controlled local AI provider. Those results retain their original date; the frontend migration did not rerun the Python or RAG suites. See the [verification report](vibe/docs/VERIFICATION.md) for scope and evidence.

The complete local stack was started and its endpoints checked again on **2026-10-16**. Implemented AI features still require real provider settings.

| Capability | Status | Evidence or remaining requirement |
| --- | --- | --- |
| Public website and signed-in workspace | Working locally | Both Vite applications start and return HTTP 200 |
| Datathon loading, EDA, baseline, and evaluation | Working locally on supported tabular data | Classification/regression demos and 24 focused tests passed; the actual case still determines the target, split, metric, and submission format |
| Account registration, sign-in, sessions, and logout | Working | Covered by real browser and backend integration scenarios |
| Profile editing and avatars | Working with local storage | Upload, reload, replacement, removal, and retry passed in Chromium |
| User/admin roles and protected routes | Working | Access separation and session revocation after role changes were tested; admin screens require an admin account |
| Bun API and background scheduler | Working locally | API health returns HTTP 200; the outbox scheduler starts and runs |
| Python agent service | Running | Liveness returns HTTP 200; full readiness returns HTTP 503 in the current configuration without embeddings |
| PDF/TXT ingestion, private vectors, and document isolation | Implemented and integration-tested | Text-based PDF/TXT extraction, indexing, owner isolation, and deletion passed with test embeddings; real indexing requires an embedding provider |
| AI chat, streaming, sources, and saved conversations | Implemented and integration-tested | RAG streaming, PDF page citations, PostgreSQL checkpoints, and thread isolation passed with a test provider; real chat currently returns `LLM_NOT_CONFIGURED` |
| Password-reset email | Needs configuration | Forms and error handling are covered; delivery defaults to `disabled`, so real reset emails are not sent until a mail driver is configured |
| Cloud storage and deployment | Available source; not deployed or verified against real cloud accounts | The normal launch uses local filesystem storage and Docker volumes |
| Browser payments and checkout | Not implemented in the active application | Recorded as absent in the capability registry |
| Mobile app | Inactive | Only its guide is present on this launch path; it is not started or checked here |
| Private agent memory | Implemented and locally tested | Explicit note creation/deletion, PostgreSQL persistence, trusted-owner retrieval, and cross-user isolation; the agent has read-only memory tools |
| Original Go auth service / Next.js dashboard | Retired | Replaced by the existing Bun/React application; original source remains in the upstream TemplateHack history |

No external AI or email provider is configured in the current local environment. Account features work independently of those integrations. A healthy agent process does not mean that real AI indexing and answers are ready.

## Technology stack

| Layer | Technologies | Responsibility |
| --- | --- | --- |
| Languages and runtimes | TypeScript 6, Bun 1.4+, Node.js 22.13+ LTS, Python 3.12+ | Web/API development, task orchestration, and the Python service |
| Public website | React 19, Vite 8, Tailwind CSS 4, React Three Fiber / Three.js | Pre-rendered static React page and a lazy 3D scene |
| Private workspace | React 19, Vite 8, TanStack Router / Query / Form | Account routes, server state, forms, profile/admin screens, and agent UI |
| UI primitives | shadcn-style components, Radix / Base UI, Tailwind CSS 4 | Shared controls, layout, and theme; shared CSS is stored locally with its MIT license |
| Interface and chat UI | Arc Free, shadcn/ui fallbacks, assistant-ui 0.15.29, Motion 14.3.0, Hugeicons | Arc for active controls/panels; assistant-ui for chat; see [design.md](vibe/design.md) and [fallback inventory](vibe/docs/ARC_MIGRATION.md) |
| Browser API | Bun, Hono 4, Zod, JOSE, Drizzle ORM | Request validation, account/session handling, permissions, storage, and trusted agent proxy |
| Relational storage | PostgreSQL 18, Drizzle migrations, SQLAlchemy, asyncpg / psycopg | Account data, document metadata, thread ownership, and conversation checkpoints |
| AI and retrieval | FastAPI, Uvicorn, LangChain, LangGraph, Chroma, pypdf | PDF/TXT processing, owner-filtered dense/BM25L retrieval with rank fusion, private memory, orchestration, and streaming |
| Background work | Croner and the PostgreSQL task outbox | Scheduled job execution and retryable queued work |
| Local infrastructure | Docker Compose, uv / `uv.lock`, Bun workspaces, Turborepo | Local services, locked dependencies, ordered tasks, and local build caching |
| Checks and tooling | Bun test, pytest, Playwright / Chromium, ESLint, TypeScript, Storybook | Automated checks and optional component catalogs |
| Datathon analysis | pandas, NumPy, scikit-learn, SciPy, matplotlib | Isolated EDA, dummy/linear baselines, holdouts, and reproducible reports |

AI uses configurable OpenAI-compatible chat and embedding endpoints. No paid provider is automatically selected or provisioned. The original Go, Kratos, SpiceDB, and Next.js applications were retired during [RAG consolidation](vibe/docs/RAG_CONSOLIDATION.md).

## Workspace

| Directory | Status | Purpose |
| --- | --- | --- |
| [vibe](vibe/README.md) | Active application | Application commands, Compose services, shared configuration, and local development guides |
| [vibe/website](vibe/website/README.md) | Active frontend | Public React/Vite site |
| [vibe/webapp](vibe/webapp/README.md) | Active frontend | Registration, sign-in, private workspace, profile/admin pages, and agent UI |
| [vibe/backend](vibe/backend/README.md) | Active backend | Bun API, authentication, permissions, file storage, scheduler, and agent proxy |
| [vibe/services/agent](vibe/services/agent/README.md) | Active internal service | Python agent modules, RAG ingestion/retrieval, and conversation state |
| [vibe/packages/contracts](vibe/packages/contracts/README.md) | Shared package | Zod contracts and TypeScript types used by the API and workspace |
| [vibe/datathon](vibe/datathon/README.md) | Local data workflow | EDA, classification/regression baselines, quality evaluation, and synthetic verification |
| [vibe/infra](vibe/infra/README.md) | Optional deployment source | Infrastructure definitions and release helpers; outside the verified local launch |

Run the application commands from `vibe/`. Git is configured at the parent workspace root. The Python agent is part of the same application and repository.

## Quick start on Windows

Requirements: Bun 1.4+, Node.js 22.13+ LTS, and Docker Desktop with its engine running. Direct Python development also requires Python 3.12+ and `uv`; the normal launch runs Python in Docker.

```powershell
Set-Location vibe
bun install --frozen-lockfile
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `vibe/.env`. Set a random `JWT_SECRET` of at least 32 characters and a private `AGENT_API_KEY`. The latter must be shared by the Bun API and Python service. To enable AI answers and indexing, configure `AGENT_LLM_BASE_URL`, `AGENT_LLM_API_KEY`, `AGENT_LLM_MODEL`, and `AGENT_EMBEDDING_MODEL`. Configure a separate embedding URL/key only if your provider needs one. Prompts and retrieved document text go to that provider.

If port `3000` is occupied, set both `PORT=3100` and `VITE_API_URL=http://localhost:3100`. This workspace's local `.env` uses that alternative port. See [port configuration](vibe/README.md#port-configuration) for the other services.

```powershell
bun run dev
```

Setup starts PostgreSQL, Chroma, and the Python agent and applies database migrations. Turbo then runs the API, scheduler, Vite app, and React/Vite site.

| Service | Default address |
| --- | --- |
| Public site | `http://localhost:4321` |
| Signed-in workspace | `http://localhost:5173` |
| Bun API | `http://localhost:3000` (`3100` in the local audit configuration) |
| Internal Python agent | `http://localhost:8001` |

Open the [public site](http://localhost:4321/), [registration](http://localhost:5173/signup), or [RAG workspace](http://localhost:5173/app/assistant) on the computer running the stack. The RAG workspace requires an account. These are local development addresses, not a hosted deployment.

The current local environment uses API port `3100` and PostgreSQL port `25432`; these machine-specific settings are in the ignored `vibe/.env`. A new environment copied from `.env.example` uses the defaults. Keep `PORT` aligned with `VITE_API_URL`, and `POSTGRES_PORT` aligned with `DATABASE_URL`. If Windows reserves the default database port, choose another free port as described in the [local database guide](vibe/docs/LOCAL_DATABASE.md).

Register an account, upload a PDF or TXT file up to 25 MiB, wait for `COMPLETED`, and ask a question. The browser calls the agent through the authenticated Bun API. Documents, vectors, and threads are scoped to their owner.

Without a configured model provider, the web applications and account features still start. AI chat returns `LLM_NOT_CONFIGURED`, and indexing requires valid embedding settings.

Ctrl+C stops the web applications and Bun processes. Docker services and volumes remain. To stop the infrastructure without deleting data, run `docker compose stop agent chroma postgres` from `vibe/`.

On Windows, if Turbo keeps printing `tasks shutting down`, press Ctrl+C again to finish exiting.

## Verification

Run these commands from `vibe/`:

```powershell
bun run check
bun run typecheck
bun run lint
bun run test
bun run test:build-contracts --force
bun run --cwd webapp e2e:install
$env:TEST_DATABASE_URL = "postgresql://superuser:superpassword@127.0.0.1:25433/web_app_demo_test"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
bun run e2e:webapp
Remove-Item Env:TEST_DATABASE_URL
Remove-Item Env:DATABASE_URL
```

The explicit test database prevents the browser harness from inheriting the development `DATABASE_URL` from the root `.env`. Backend integration tests create and remove their own isolated test database.

The example uses port `25433`; choose another free port if needed. Windows may reserve a port even when nothing listens on it. Inspect reservations with `netsh interface ipv4 show excludedportrange protocol=tcp`.

The application dependency audit passes with no known vulnerabilities. Shared shadcn CSS is kept locally with its MIT license, so builds do not install the component generator or its vulnerable dependency tree. The pinned generator remains available on demand; see the [style provenance and tool limitation](vibe/styles/vendor/shadcn/README.md). The [verification report](vibe/docs/VERIFICATION.md) records actual results, commands, and remaining limits.

Python tests and the manual RAG scenario are documented in [TESTING.md](vibe/docs/TESTING.md). The complete command reference is [COMMANDS.md](vibe/docs/COMMANDS.md).

## Git and local files

The project repository is [Feruum/Darumleye](https://github.com/Feruum/Darumleye), branch `main`. Keep credentials and local runtime data in the ignored workspace paths.

`.gitignore` excludes dependencies, generated builds, local `.env` files, test artifacts, uploads, and infrastructure state. Environment examples remain versionable. Keep API keys and private data in local configuration.

## Documentation and license

Start with the [application README](vibe/README.md), [architecture](vibe/docs/ARCHITECTURE.md), and [local database guide](vibe/docs/LOCAL_DATABASE.md). Active product decisions are recorded in [CHECKLIST.md](vibe/CHECKLIST.md).

The Vibe-derived application uses Apache License 2.0. Preserve [LICENSE](vibe/LICENSE), [NOTICE](vibe/NOTICE), and the original attribution when distributing it. The [consolidation guide](vibe/docs/RAG_CONSOLIDATION.md) records the original agent source and its recovery from the upstream TemplateHack history.

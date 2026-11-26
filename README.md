# Spec Review

**BCC HUB × MNU FinTech Hackathon** · AI-powered specification review with traceable evidence.

<p align="center">
  <img src="vibe/docs/assets/spec-review-architecture.svg" alt="Spec Review architecture — React, Bun/Hono, Python/LangGraph, PostgreSQL, Chroma and configurable AI providers" width="100%">
</p>

[Run the CLI](#reproduce-the-hackathon-run) · [Start the web app](#quick-start) · [Technology stack](#technology-stack) · [Project structure](#workspace)

Spec Review checks FinTech requirements before development. It extracts requirements from PDF, MD, TXT, DOCX, XLSX, CSV, JPG/JPEG and PNG documents, checks their quality and consistency, and produces a Russian-language report with verified source quotations.

- **Review:** source-grounded requirement checks, mandatory coverage and code-based readiness verdict.
- **Validation:** the upload form offers paired validation (`multi_agent`, default) and direct validation (`multi_agent_direct`). Both use five independent experts, a shared global validator, Evidence Graph and Finalizer.
- **Workspace:** interactive findings, source document inspector, correction proposals, report download and chat over completed reviews.
- **Optional:** review against selected regulations, independent Nace Drex verification, and Langfuse run telemetry.

## Reproduce the hackathon run

The document-only CLI requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and an OpenAI-compatible model that supports JSON output. Docker, Bun, PostgreSQL, Chroma and embeddings are not needed for this run.

1. Copy the root `.env.example` to `.env` and set `AGENT_LLM_API_KEY`. The example selects `openai/gpt-6-luna` through OpenRouter with low reasoning effort for every review stage. Keep credentials private.
2. Download the [organizers' specification](https://github.com/BCC-HUB/ai-requirements-validator-task/blob/main/docs/requirements.md) to `.artifacts/requirements.md`.
3. From the repository root, run:

```bash
uv run --project vibe/services/agent --frozen python -m agent.spec_review_cli --input .artifacts/requirements.md --output .artifacts/report.md --env-file .env
```

All three modes allow up to nine simultaneous primary model calls per review by default. Set `AGENT_SPEC_REVIEW_CONCURRENCY` to change that limit. When you select references, the Reference Inspector can make one additional call concurrently.

The CLI defaults to paired validation. Add `--review-mode multi_agent_direct` for direct validation or `--review-mode legacy` for classic review. Use a `.json` output filename for the structured report. The standalone CLI's OCR requires Tesseract (Russian, Kazakh and English) and Poppler for scanned documents and images.

See [CLI options, input limits and evaluation](vibe/docs/SPEC_REVIEW.md), [review architecture](vibe/docs/MULTI_AGENT_REVIEW.ru.md), and [reference review](vibe/docs/REGULATORY_CONTEXT.md) for the full workflow.

## Quick start

The full application uses Bun 1.4+, Node.js 22.13+ LTS and Docker with Compose. Follow the [application setup](vibe/README.md#quick-start) to install dependencies, configure the model and run the stack.

| Application | Default address |
| --- | --- |
| Main Spec Review workspace | [localhost:5173/app/assistant](http://localhost:5173/app/assistant) |
| Public landing page | [localhost:4321](http://localhost:4321) |

Register or sign in, open **Проверка ТЗ**, upload a specification and select a validation mode. Inspect the report and source evidence, download the report, or open **Чат по проверкам**.

## Technology stack

| Layer | Stack |
| --- | --- |
| Webapp | React 19, TypeScript 6, Vite 8, TanStack Router/Query/Form, React Flow, PDF.js |
| Public website | React 19, Vite 8, pre-rendered HTML, Three.js |
| Interface | Tailwind CSS 4, Arc Free, shadcn/ui fallbacks, assistant-ui, Motion, Hugeicons, Blobatar |
| API | Bun, Hono, Zod, JOSE, Drizzle ORM, Croner |
| AI | Python 3.12+, FastAPI, LangChain, LangGraph, configurable OpenAI-compatible models |
| Parsing and retrieval | pypdf, Poppler, Tesseract, ChromaDB, BM25L and rank fusion |
| Data and jobs | PostgreSQL 18, SQLAlchemy, Drizzle, durable review queue and task outbox |
| Observability | OpenTelemetry, structlog, optional Langfuse |
| Development | Bun workspaces, Turborepo, Docker Compose, uv, pytest, Bun Test |

## Workspace

All application code is under `vibe/`.

| Path | Responsibility |
| --- | --- |
| [webapp/src](vibe/webapp/src) | **Main frontend:** authenticated Spec Review screens |
| [website/src](vibe/website/src) | Public landing page |
| [backend](vibe/backend) | HTTP API, sessions, accounts, jobs and agent proxy |
| [services/agent](vibe/services/agent) | Specification review, evidence validation, RAG and chat |
| [packages/contracts](vibe/packages/contracts) | Shared Zod schemas and TypeScript API contracts |
| [packages/mascot](vibe/packages/mascot) | Shared glass mascot and animations |
| [infra](vibe/infra) | Optional deployment infrastructure |
| [docs](vibe/docs) | Architecture, evaluations and operating guides |

Native mobile is not part of the active application. The browser accesses the AI service through the authenticated Bun API. Model keys stay on the server; document text is sent to the configured model provider.

## Documentation

- [Application setup and commands](vibe/README.md)
- [Multi-agent review architecture](vibe/docs/MULTI_AGENT_REVIEW.ru.md)
- [Document review, CLI and limits](vibe/docs/SPEC_REVIEW.md)
- [Quality evaluation](vibe/docs/QUALITY_EVALUATION.md) and [business value](vibe/docs/BUSINESS_VALUE.ru.md)
- [Reference corpus and retrieval](vibe/docs/REGULATORY_CONTEXT.md)
- [Observability](vibe/docs/LANGFUSE.md), [design system](vibe/design.md) and [verification history](vibe/docs/VERIFICATION.md)

The Vibe-derived application uses Apache License 2.0. Preserve [LICENSE](vibe/LICENSE), [NOTICE](vibe/NOTICE), and the original attribution when distributing it.

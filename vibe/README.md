# Spec Review — application workspace

<p align="center">
  <img src="docs/assets/spec-review-architecture.svg" alt="Spec Review architecture — public site, private workspace, authenticated API, shared AI review engine and data stores" width="100%">
</p>

Spec Review is a FinTech requirements-validation application. The repository-root `README.md` describes the project and its technology stack. See the [multi-agent architecture](docs/MULTI_AGENT_REVIEW.ru.md) for review details.

## Where the frontend lives

| Path | Role | Default local address |
| --- | --- | --- |
| [webapp/src](webapp/src) | **Main frontend** — dashboard, review, report inspector, evidence graph, corrections and chat | [localhost:5173](http://localhost:5173) |
| [website/src](website/src) | Public landing page, pre-rendered React/Vite | [localhost:4321](http://localhost:4321) |
| [backend](backend/README.md) | Bun/Hono API, sessions, application data and agent proxy | [localhost:3000](http://localhost:3000) |
| [services/agent](services/agent/README.md) | Python/FastAPI, LangGraph review engine and RAG | [localhost:8001](http://localhost:8001) (internal) |

Both frontends use React 19 and Vite 8. The [shared mascot](packages/mascot) and [Zod contracts](packages/contracts/README.md) are in `packages/`. See [design.md](design.md) for the violet/lavender palette, Arc Free controls, assistant-ui chat and shared interface conventions.

## Quick start

Requirements: Bun 1.4+, Node.js 22.13+ LTS, and Docker with Compose. Direct agent development also requires Python 3.12+ and uv.

From `vibe/` on Linux/macOS:

```bash
bun install --frozen-lockfile
test -f .env || cp .env.example .env
```

On Windows PowerShell:

```powershell
bun install --frozen-lockfile
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env`:

| Setting | Purpose |
| --- | --- |
| `JWT_SECRET` | Random session secret, at least 32 characters |
| `AGENT_API_KEY` | Private shared key for the Bun API and Python agent |
| `AGENT_LLM_BASE_URL`, `AGENT_LLM_API_KEY`, `AGENT_LLM_MODEL` | Specification review and chat; the example uses GPT-6 Luna via OpenRouter |
| `AGENT_SPEC_REVIEW_CONCURRENCY` | Maximum simultaneous primary calls per review in all three modes. Defaults to 9. The optional Reference Inspector has one separate slot. |
| `AGENT_EMBEDDING_MODEL` | Required for general RAG indexing; not required for document-only review |

Keep `.env` private. Configure embedding credentials separately if the provider requires them. Model requests include extracted document text.

```bash
bun run dev
```

Turborepo starts PostgreSQL, Chroma and the agent in Docker, applies Drizzle migrations, then starts the Bun API, scheduler and both Vite apps.

Open the [main workspace](http://localhost:5173/app/assistant), sign in and upload a specification. Paired validation is the default; classic (`legacy`) and direct validation are also available. Repeating a review preserves its recorded mode. Use **Чат по проверкам** for completed reviews. For CLI-only review without Docker or embeddings, see the [CLI guide](docs/SPEC_REVIEW.md).

Ctrl+C stops the application processes. To stop infrastructure while preserving data, run `docker compose stop agent chroma postgres`.

## Port configuration

Keep the paired environment settings aligned when changing default ports:

| Service | Settings |
| --- | --- |
| Bun API | `PORT` and `VITE_API_URL` |
| Python agent | `AGENT_PORT` and `AGENT_SERVICE_URL` |
| PostgreSQL | `POSTGRES_PORT` and `DATABASE_URL` |
| Landing page's workspace link | `PUBLIC_WEBAPP_URL` |

## Development commands

| Command | Purpose |
| --- | --- |
| `bun run dev` | Start the full application |
| `bun run dev:webapp` | Start only the main frontend; requires running services |
| `bun run dev:website` | Start only the landing page |
| `bun run build` | Build the workspaces |
| `bun run typecheck` | Type-check the workspaces |
| `bun run check` | Full local checks (requires Docker and registry access) |

Use focused checks for individual changes. See [commands](docs/COMMANDS.md), [testing](docs/TESTING.md) and [development rules](AGENTS.md).

## Guides

- [Spec Review workflow and limits](docs/SPEC_REVIEW.md)
- [Architecture](docs/ARCHITECTURE.md) and [multi-agent review](docs/MULTI_AGENT_REVIEW.ru.md)
- [Chat UI](docs/CHAT_UI.md) and [interface design](design.md)
- [Database](docs/LOCAL_DATABASE.md), [storage](docs/STORAGE.md), [deployment](docs/DEPLOYMENT.md)
- [Observability](docs/LANGFUSE.md) and [verification history](docs/VERIFICATION.md)

Apache License 2.0. Preserve [LICENSE](LICENSE), [NOTICE](NOTICE) and the original attribution.

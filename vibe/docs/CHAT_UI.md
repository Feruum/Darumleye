# Review chat interface

The signed-in workspace exposes **Проверка ТЗ** at `/app/assistant` and a separate **Чат по проверкам** at `/app/chat`. `@assistant-ui/react` 0.15.29 owns the conversation lifecycle, composer, suggestions, copying, scrolling and cancellation. The user selects an already completed review; no second upload is needed.

## Data and ownership

After a successful web review, the Python service stores the extracted, numbered original text and validated structured report in PostgreSQL (`agent_completed_reviews`). It lists up to 200 of the current authenticated owner's completed reviews through `GET /api/agent/spec-reviews`. The UI defaults to the newest review. The compact review-history list groups runs by local date and orders them newest first. Search matches filename, date/time, status and run ID; a separate verdict filter narrows the list without changing the active conversation. Selecting another review starts a fresh conversation. Previously completed reviews that were never stored, local demo reports and standalone CLI runs do not appear automatically. The archive does not retain the original binary PDF or uploaded reference binaries.

The browser sends the selected `review_id`, `module_id: review_chat`, and a review-scoped `thread_id` to `POST /api/agent/chat/stream`. The Bun proxy supplies the trusted authenticated owner. The Python route verifies that the record belongs to that owner and binds the thread to the selected review. The LangGraph review-chat node directly retrieves evidence with `search_user_review` before a single non-streaming model call; the tool receives both the owner and selected review ID from server-controlled state. Neither the model nor the browser can override the effective owner. Local keyword ranking with Russian word prefixes prioritizes matching findings and their verified evidence locations. The model receives the original source passages and report context, and selects only supporting citation IDs in its structured reply. The browser shows selected citations grouped by source in a collapsed disclosure, with no source panel for clarification-only replies. SSE keepalive comments retain the connection during silent model calls. It needs no embedding provider. The model is instructed to distinguish original evidence from its generated assessment.

The legacy `rag` module and private PDF/TXT document upload API remain available for existing API clients; review chat does not search those arbitrary uploads. The optional explicit personal notes remain separate and are not evidence. Session-scoped TanStack Query keys include the owner's ID and clear on sign-out/account replacement. The agent's PostgreSQL checkpoint storage owns conversation history; the client starts a new conversation after refresh.

## UI owners

| File | Responsibility |
| --- | --- |
| `webapp/src/features/agent/AgentWorkspace.tsx` | Specification review only |
| `webapp/src/features/agent/ReviewChatWorkspace.tsx` | Owner-scoped review list and selection, optional notes |
| `webapp/src/features/agent/AgentThread.tsx` | assistant-ui provider, LocalRuntime, composer, sources, copy and animated assistant identity |
| `webapp/src/features/agent/runtime.ts` | Authenticated SSE model adapter |
| `services/agent/src/agent/services/review_archive.py` | PostgreSQL archive and bounded original/report retrieval |
| `services/agent/src/agent/modules/review_chat.py` | Read-only review Q&A tool and instructions |

On desktop the chat fills the available workspace; the message history scrolls independently and the composer stays at the bottom. On smaller screens the review list stacks above the chat. Send with Ctrl/Cmd+Enter or the button; suggestions fill the composer. No question can be sent before a completed review is selected. Blobatar uses the current message run state for waiting/streaming animation and follows the pointer on the welcome avatar and latest response. Reduced-motion preferences are respected. Existing Arc Free MIT controls and the shared `Typography` own visual primitives. No assistant-ui Cloud account is required.

## Local checks

Run `bun run --cwd webapp test`, `bun run --cwd webapp typecheck` and `bun run --cwd webapp build` from `vibe/`. The agent's Python tests cover completed review storage, owner isolation, selected-review tool arguments, and the existing review pipeline. The Playwright `agent.spec.ts` scenarios use controlled responses to verify the separate route, review selection, sources, cancellation and memory; they do not measure real model quality. See [TESTING.md](TESTING.md) for isolated database requirements.

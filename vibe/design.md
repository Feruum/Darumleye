# Interface design and library decisions

This document describes the current TemplateHack interface. It is an implementation guide for maintaining a consistent UI, not a request to redesign every screen. See [architecture](docs/ARCHITECTURE.md), [chat behavior](docs/CHAT_UI.md), and the [capability registry](CHECKLIST.md) for their respective contracts.

## Current frontend stack

| Library or system | Current role |
| --- | --- |
| React 19, TypeScript, Vite 8 | Both browser applications; the public site is pre-rendered and the signed-in workspace is a client application |
| Tailwind CSS 4 and existing CSS variables | Shared spacing, typography, colors, light/dark themes, and responsive layout |
| shadcn/ui fallbacks and Radix/Base UI | Sidebar/Sheet, structural Field/Item/Separator/Spinner, and specialized role-confirmation AlertDialog; see the [fallback report](docs/ARC_MIGRATION.md) |
| [assistant-ui](https://www.assistant-ui.com/) (`@assistant-ui/react` 0.15.29) | LocalRuntime and primitives for the RAG conversation, composer, scrolling, messages, and cancellation |
| [Arc Free](https://uiarc.dev) copied MIT source | Active buttons, cards, alerts, fields, selects, search, avatars, badges, skeletons, tables, account menu, public FAQ and mobile navigation |
| Motion 14.3.0 | Animation required by the selected Arc Free source |
| `blobatar` and `@blobatar/react` 2.7.0 | Shared glass mascot gaze, expressions, and responsive SVG motion in website and workspace |
| Hugeicons | Existing icon family, also used by the adapted Arc components |
| `Typography` | Shared text rendering and semantic headings; also used inside adapted Arc source |
| TanStack Router, Query, and Form | Existing navigation, server-state queries/mutations, and application forms |
| Zod contracts | Shared request/response validation between the browser and Bun API |

Exact dependency versions and resolutions belong in the app manifests and `bun.lock`. Motion is pinned to 14.3.0 in both apps; the existing Radix aggregate is pinned to 1.6.7. Astro is no longer used. The active frontend uses React/Vite.

## Visual language

Spec Review is the approved hackathon product direction. The authenticated shell uses a floating translucent sidebar and capsule header above a lavender-white canvas, with a dark equivalent. The BCC Hub and BCC Hub × MNU references inform the `#8934F9` brand accent, `#EFF0FC` lavender, `#19171E` ink, editorial headings, open metric columns, rounded controls, and mobile navigation. The distinct Spec Review mascot and application composition remain original. The review screen uses progressive disclosure: document selection and optional reference settings share one form, with a compact onboarding guide and an instant sample report beside it. The guide stacks below the form on mobile. A separate waiting view shows elapsed time without fabricated stage completion; the report gives the source a wide paper-like canvas with violet line markings and a small page rail on the left. The selected finding reads as a margin note on the right. The searchable queue opens in that same margin on desktop. Switching report sections preserves document context. A small, still mascot identifies the findings margin. The public landing follows the approved BCC HUB layout with a lavender panel, a lightweight review loop and a pause control. It starts with a still illustration for reduced-motion and no-JavaScript sessions. Fullscreen retains the mounted document and uses the existing glass mascot to toggle the same review panes. Citation controls reveal the corresponding original text lines or PDF page. On mobile, the view switches between the report and original document. Selecting an issue opens a dedicated detail pane with a back action; the fullscreen controls have their own row rather than covering content. Technical provider metadata and internal identifiers are retained in the structured/exported report, not in the employee-facing interface. The sample report is a labeled local artifact from a previous synthetic core review, not a live result. Retain the existing Arc controls and semantic `Typography`. See the [approved ImageGen reference](docs/assets/spec-review-approved.png) and the [review workflow](docs/SPEC_REVIEW.md). Tactile branding uses the approved glass-and-titanium mascot in the sidebar, authentication, report guide, analysis, response identity, and website. The same component and optimized transparent shell live in `packages/mascot`, with a restrained six-colour palette. The wordmark and BCC Hub × MNU partner label remain compact. The geometric accent is a stylistic interpretation, not an official logo asset.

The review setup keeps the default mode visible in a collapsed settings row. Uploading a document is the first action. The report queue uses priority bands inspired by Linear Triage, with existing finding IDs preserved across search, citation jumps, and keyboard navigation. In the correction screen, accepting an available edit is the primary action. The actions for the revised document explain when they become available. The local Storybook workflow demonstrates upload and report navigation with the existing sample and no model calls.

Use Inter Variable for the public product site and workspace. Primary actions have a violet fill; action text on lavender uses a darker violet for contrast. Use shared spacing and semantic color tokens. Keep primary actions clear and make long messages, filenames, and citations wrap within narrow viewports. Glass surfaces use bounded translucency, specular inset highlights, depth shadows, and backdrop blur where supported. The document itself stays opaque. The mobile dock exposes Overview, Review, Chat, and New review while the full sidebar retains profile and settings. Hide the dock in document fullscreen. Motion is grounded in short state transitions and press feedback; honor reduced motion.

The original generated glass-and-titanium artwork is used in the entry guide, overview header, authentication, and public hero. The two independently built browser applications use the same optimized WebP (about 55 KB per app); see [asset prompt](docs/assets/review-glass.prompt.md).

The overview dashboard replaces the account-summary surface with persistent review metrics, a requirement-quality chart, severity distribution, an adaptive-width GitHub-style activity calendar (up to 52 weeks) with date filtering, the most recent completed review summaries, and the completed reviews available in chat. The activity calendar never includes demo data. Pointer hover highlights metrics, chart values and activity dates; reduced-motion users retain immediate feedback without transforms. It uses Arc Free cards, badges, empty states and the sortable table; a lightweight two-category CSS ring renders the exact ratio, with explicit counts for accessibility. With no completed reviews, charts show a clearly labeled synthetic example, while personal counters remain zero. Owner-scoped archive analytics restore the real dashboard on reload; only lightweight summaries live in the authenticated query cache, and sign-out clears it. File rows link to their saved report with direct access to the selected review chat.

The workspace shell owns navigation and the page frame. Features occupy its content region. The separate sidebar destination “Чат по проверкам” keeps chat and completed-review selection apart from the review workflow; the completed-review list stacks above chat on narrow screens. Memory notes remain in a collapsed disclosure. The chat selects one completed review and uses a dedicated review-chat module without a technical module selector; suggested questions appear in the empty state. The desktop chat fills the remaining workspace height with a scrollable conversation and a composer anchored at the bottom.

Use motion for state transitions, not decoration that delays a task. Retain reduced-motion behavior. Keyboard focus, accessible names, readable error messages, and non-color status text must remain usable.

Account fallbacks use the glass mascot with a deterministic colour and small eye-geometry variation derived locally from the stable account ID; uploaded photos take precedence. The assistant uses one identity with idle, thinking, speaking, success and error poses. The local Blobatar gaze and motion engine tracks a fine pointer, blinks, breathes, and interpolates expressions. Only the latest chat response follows the pointer; larger mascots can turn once on hover, lean and squash with interruptible springs, become bored and sleepy during inactivity, and wake on user activity. Analysis cycles decorative thinking expressions while the real review state stays unchanged. Reduced-motion preferences disable ambient motion and gaze. See [the mascot guide](docs/BLOBATAR.md).

## Library boundaries

assistant-ui owns the client chat lifecycle. The feature-local adapter uses the existing authenticated Bun SSE proxy. FastAPI/LangGraph owns model calls, retrieval, tools, document isolation, memory access, and checkpoints. Provider keys remain on the server. assistant-ui Cloud is not configured.

TanStack Query owns authenticated module, document, and memory requests. Their cache keys include the owner and start with the shared session namespace. Sign-out or a replacement login clears that cache and cancels pending reads. Changing accounts resets the local chat and drafts. The memory card saves only explicit user notes; conversations are not automatically converted into notes.

Arc Free is the default presentation library for the active public site and workspace. Official copied source lives in each app's `src/components/arc/`; compatibility adapters live in `src/components/interface/`. Features retain validation, data queries, permissions, and route ownership. Namespaced `--arc-*` tokens map to the existing theme and work inside portals. Foundation tokens are imported without the global reset. Keep the [workspace provenance](webapp/src/components/arc/README.md) and [website provenance](website/src/components/arc/PROVENANCE.md), including both MIT notices.

Use shadcn/ui when the exact required Arc Free component is unavailable. Arc's ready-made sidebar blocks are Pro; the existing accessible Sidebar/Sheet stays. A generic Arc Dialog does not replace the destructive AlertDialog contract. The [migration report](docs/ARC_MIGRATION.md) records every retained fallback. Optional Storybook catalogs remain separate from active product screens. No Arc Pro code is included.

assistant-ui continues to own the conversation, composer, messages, sources/tools, stop, and error recovery. Its existing chat controls are an explicit migration exception. Arc owns the surrounding module/document/memory panels. Public FAQ and navigation retain native details/summary in the initial HTML and enhance to Arc after hydration. Preserve real anchors, multiple open FAQ panels, keyboard focus, and reduced motion. Field focus outlines must remain visible even while the validation border is red. In the mobile sidebar, the first Escape closes the account menu and the next closes the sidebar.

Chat text stays React-escaped plain text. Sources show the document filename, page, and snippet. Tool events describe server activity; they do not execute tools in the browser. A recoverable tool error must not suppress the agent's later answer. Terminal provider/network failures remain visible, and Stop must cancel the request and reader.

## Adding or changing components

1. Find the existing owner in the feature or shared UI before adding another abstraction.
2. Check the official Arc Free catalog first. Reuse the existing interface adapter; use shadcn/ui for a documented missing equivalent. Keep chats on assistant-ui.
3. Preserve theme tokens, Typography, Hugeicons, keyboard behavior, and reduced motion. Do not overwrite all shared UI with a registry generator.
4. Keep library-specific integration in the responsible feature or Arc source folder. Keep authentication and provider secrets in their existing server boundaries.
5. Run focused tests, types, lint, architecture validation, and relevant builds. For interaction changes, exercise the real UI on desktop and a narrow viewport with labeled test-data/provider boundaries.
6. Update this document, the relevant README, CHECKLIST, and dated verification evidence when a lasting library or behavior decision changes.

The current [verification report](docs/VERIFICATION.md) distinguishes functional UI tests from real-provider quality checks.

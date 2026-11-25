# Public Spec Review website

The public site presents Spec Review, an AI-based requirements quality checker for the BCC Hub × MNU FinTech Hackathon. It is a concise Russian-language product introduction with an illustrative report, a four-stage workflow, quality criteria and authentication links. The report example is explicitly illustrative and does not claim real analysis results. Its violet and neutral palette matches the separate authenticated workspace in `webapp/`.

The website uses React 19, Vite 8, Tailwind CSS 4 and copied MIT **Arc Free** controls. Keep their [source provenance and MIT notices](src/components/arc/PROVENANCE.md). The public product surface uses `src/styles/spec-review.css`; the existing component catalog and its themes remain separate. There is no 3D scene in the active landing page.

## Rendering and behavior

The landing page is composed by `src/App.tsx`. `scripts/build.mjs` uses Vite's SSR build API and React DOM `renderToString` to generate complete static HTML. A client build supplies hashed assets. Temporary server-rendering output is removed after the build. The published output is `website/dist`, including pre-rendered `index.html` and immutable `assets/`; it needs no production Node server.

The development Vite middleware renders the same application and metadata into HTML. The client entry hydrates it when the browser is idle, with a 2500 ms deadline. Navigation anchors and authentication links work natively without JavaScript. The signed-in application performs document review and report generation; the public site does not upload documents or call AI providers.

## Commands

Run application commands from `vibe/`:

```powershell
bun run dev:website
bun run build:website
bun run typecheck:website
bun run test:website
bun run test:build-contracts
bun run storybook:website
```

The public dev server remains at `http://localhost:4321`. The root dev shortcut loads the shared local `.env`. The complete stack starts with `bun run dev`.

To preview production output, first build, then run `bun run --cwd website preview`. Storybook runs on port 6007 with its own Vite configuration so the website's HTML middleware cannot replace the component catalog.

## Public settings and metadata

- `PUBLIC_WEBAPP_URL`: optional absolute HTTP(S) origin for the authenticated workspace. When set, the public CTA links to `/signup?returnTo=%2Fapp%2Fassistant` and the login link to `/login?returnTo=%2Fapp%2Fassistant`. When unset, CTA links to the local process section and no login link is rendered.
- `PUBLIC_WEBSITE_URL`: optional absolute HTTP(S) site URL for canonical and Open Graph URL tags.
- Public settings are build-time values. Invalid configured URLs fail the build. Metadata is HTML-escaped before insertion.

Title, description, Russian language, light theme, favicon, Open Graph, and Twitter metadata are in the initial HTML. Vite exposes only its standard public variables and the existing `PUBLIC_*` prefix. Keep all private keys out of public-prefixed variables.

## Checks and architecture

The website tests cover authentication-link configuration, metadata escaping and URL validation, plus the legacy component catalog. The static build contract verifies pre-rendered content, native anchor targets, hashed assets, retired canvas exclusion and story-only CSS isolation.

Keep account routes, permissions, checkout and private data in `webapp/` and `backend/`. This product landing does not add payments, public API data or runtime server rendering. See [WEB_SURFACES.md](../docs/WEB_SURFACES.md) and [ARCHITECTURE.md](../docs/ARCHITECTURE.md).

Static release source retains the existing output directory and public environment names. Request-rendered pages or new routes require a separate recorded product need; this one-page static build does not imply a general SSR routing framework.

## References

- [Vite SSR API](https://vite.dev/guide/ssr.html)
- [React server rendering](https://react.dev/reference/react-dom/server/renderToString)
- [React hydration](https://react.dev/reference/react-dom/client/hydrateRoot)

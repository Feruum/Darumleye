# Glass mascot and Blobatar motion

The approved glass-and-titanium character is shared by the workspace and public website through `@web-app-demo/mascot` (`packages/mascot`). `blobatar` and `@blobatar/react` are pinned to **2.7.0**, MIT licensed. The original body artwork is a local optimized transparent WebP. Only the animated SVG eyes and mouth are rendered over it; no external avatar service is used.

## Ownership and behavior

- `packages/mascot/src/index.tsx` owns the glass shell, separate SVG face, five operational states, thirteen decorative moods, six muted colour palettes, a spring gesture layer, and the Blobatar gaze and expression integration. `performance.ts` owns deterministic mood timing and the paused-on-hidden-tab scheduler. The shell artwork was edited to remove baked facial features. The motion rig targets the pinned package's CSS and supplies a transparent geometric proxy for its gaze survey. Recheck the rig if Blobatar changes major version.
- The approved reference is `docs/assets/glass-mascot-concept-v4.png`; the clean source is `docs/assets/glass-mascot-body.png`. The runtime WebP is resized and encoded from that clean asset. The favicon uses the approved idle artwork.
- Assistant state maps to violet idle/speaking, glacier thinking, jade success, and rose error. Explicit palette overrides also support amber and graphite. Account fallbacks derive stable colours and slight eye variations from the account ID; names, email addresses and IDs are never sent to a hosted avatar endpoint. Uploaded photos take precedence.
- The sidebar, sign-in screen, guide, analysis screen, completed report, chat, account fallback and landing use the same identity. Large faces turn once on hover, lean toward the pointer, squeeze on press and return with a Motion spring. Inactive large faces progress from a wink and curiosity to boredom, yawn and sleep; pointer or keyboard activity wakes them. Thinking cycles pondering, inspection and a brief spark without changing the review state. Historical chat responses do not attach a pointer-tracking driver. State labels remain accessible outside the decorative artwork.
- Large assistant surfaces opt into expressive motion based on the [Blooby animation guide](https://github.com/divyanshu-patil/blooby/blob/a581ed9c8bdd65bcfacf930914e96bf11ae529b0/ANIMATION.md) and the timing of its Profile Hello and Tap to Start presets. The glass shell uses paired scale axes, a curved face shift with follow-through, and a delayed screen ripple. A clipped light sweep accompanies thinking and completion. These effects use the existing SVG and Motion rig.
- The native Blobatar gaze driver follows a fine pointer and respects reduced-motion preferences. The mood scheduler and spring gestures are disabled under reduced motion; `mascot.css` suppresses custom body performances, speech and hover turns. The component never controls chat submission, review status or backend operations.
- Local preview: `http://127.0.0.1:5173/mascot-preview.html` (Vite development only). Switch states, moods and palettes, move the pointer, press the character, or leave it idle; no provider request is needed. Production build entrypoints remain unchanged.

## Checks

```bash
cd vibe
bun test webapp/tests/blob-avatar.test.ts webapp/tests/agent-response-identity.test.ts
bun run --cwd webapp build
bun run --cwd website build
bun test website/build-contracts/static-build.test.ts
```

The tests cover local rendering, distinct stable accounts, photo precedence, eyes and state classes, color mapping, and the landing's local asset references. The live preview verifies pointer response, color and motion independently of model calls.

Primary references: [Blobatar documentation](https://blobatar.dev/docs), [React adapter source](https://github.com/Alain00/blobatar/tree/main/packages/react), and [upstream license](https://github.com/Alain00/blobatar/blob/main/LICENSE).

## Product scope (2026-11-27)

The character is presentation-only. Its waiting state reflects the local UI's active review/chat state, not an estimate of backend progress or a quality score. The durable job remains independent of animation and navigation. Historical pixel-art and glass concepts in `assets/` are design provenance rather than alternate active mascots. The current review and report behavior is documented in [Spec Review](SPEC_REVIEW.md).

# Architecture diagrams

Updated 27 November 2026. The two Graphviz overview diagrams are authoritative for the current review topology and component boundaries:

- [System overview (SVG)](spec-review-overview.svg) · [PNG](spec-review-overview.png) · [DOT source](spec-review-overview.dot)
- [AI pipeline (SVG)](spec-review-pipeline.svg) · [PNG](spec-review-pipeline.png) · [DOT source](spec-review-pipeline.dot)

They cover the three review modes, durable web queue, bounded fact index and Z3, adjudication, criterion reconciliation, evidence graph and optional reference/Drex branches. The [multi-agent description](../MULTI_AGENT_REVIEW.ru.md) and [API guide](../SPEC_REVIEW.md) describe exact behavior.

The six numbered PlantUML sources and their PNGs are archived diagrams of the original architecture (legacy core, synchronous review and early report schema), retained as a reproducible historical snapshot. The fix and review-chat sequences remain useful for exploring the basic interfaces, but the exact current recovery, repeat and archive contracts are in the API guide. They should not be used as an assertion that the default route is synchronous or lacks the arbiter and graph.

Regenerate the current diagrams from this directory using Graphviz:

```bash
for diagram in spec-review-overview spec-review-pipeline; do
  dot -Tsvg "$diagram.dot" -o "$diagram.svg"
  dot -Tpng "$diagram.dot" -o "$diagram.png"
done
```

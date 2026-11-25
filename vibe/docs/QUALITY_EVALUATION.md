# Requirements review quality evaluation

The organizer supplies a source document and review rubric, not labeled ground truth. Use three separate evidence tracks:

1. **Structural checks:** exact source identity, citations, rubric coverage, well-formed report and registration. The existing `evaluate_spec_report.py` checks these properties.
2. **Quality benchmark:** hand-annotated synthetic sources and a saved report. The offline `evaluate_quality_benchmark.py` measures annotation coverage, evidence validity and criterion coverage. It does not call a model.
3. **Business value:** measured manual and assisted review effort for the same task, with a common quality acceptance rubric. Do not infer saved analyst time from model latency.

For a clearly labeled hackathon planning hypothesis based on public Kazakhstan wage statistics and comparable API tariffs, see [the provisional business-value scenario](BUSINESS_VALUE.ru.md). It is not an observed savings measurement.

## Synthetic benchmark

All four sources are fictional and distinct from the organizer case. Their labels are pedagogical, not a representative test corpus.

| Source | Annotated explicit obligations | Injected defect labels | Purpose |
| --- | ---: | ---: | --- |
| `defective.md` | 6 | 4 | Vague language, conflicting schedule, duplicates |
| `revised.md` | 4 | 0 | Paired revised source with fictional approved decisions |
| `workflow.md` | 9 | 4 | Multiple obligations under one ID, unnamed requirement, conflicting periods, missing exception outcome, performance, duplicate |
| `workflow-revised.md` | 9 | 0 | Paired revised source; additional exception obligations |

The `*.gold.json` files in [examples/quality-benchmark](examples/quality-benchmark/) list the expected obligation occurrences and the deliberately injected defects. Duplicate obligations with distinct explicit IDs are separate extraction labels. The reviewer should detect the duplication. Document headings and metadata are not obligations. An empty defect label set documents removal of the injected defects, not certified readiness.

From the repository root, evaluate a **previously generated** JSON report with this command:

```bash
uv run --project vibe/services/agent --frozen python vibe/services/agent/scripts/evaluate_quality_benchmark.py \
  --gold vibe/docs/examples/quality-benchmark/defective.gold.json \
  --report .artifacts/benchmark-defective.json \
  --output .artifacts/benchmark-defective-score.json
```

If no report exists, generate it with the normal specification review CLI described in [SPEC_REVIEW.md](SPEC_REVIEW.md). That CLI calls the configured provider and may incur charges. Repeat for each of the four sources. Do not submit the gold annotation to the model. Record the exact source and report SHA-256, model, agent revision, settings and run ID. No model runs on the new synthetic examples are claimed here.

### What each result means

| Result field | Definition |
| --- | --- |
| `matched` | Maximum one-to-one pairing of gold defect IDs and report finding IDs. Accepted kinds and all annotated quotation anchors must match. |
| `annotation_recall` | Matched labels / annotated labels. Null if there are no labels. |
| `annotation_precision` | Matched items / report items. This legacy field name denotes annotation agreement, **not** validated precision. A report can contain legitimate observations outside the labels. Null if there are no report items. |
| `extraction` | One-to-one coverage of explicit obligation labels by report requirements, with original ID (including null for unnamed requirements) and quote anchors. The same report item cannot satisfy two labels on the same source line. |
| `coverage.annotated_individual` | Distinct valid individual criteria on matched obligations / nine times the number of annotated obligations. A missed extracted obligation therefore contributes zero reviewed criteria. |
| `coverage.report_individual` | Distinct valid individual criteria / nine times the number of extracted requirements. This alone does not measure extraction completeness. |
| `coverage.set` | Distinct valid set criteria / six. |
| `evidence_validity` | Exact citation text is checked against its 1-based source line range, ignoring whitespace differences. Invalid citations cannot score. A finding with no citation is reported separately. |
| `measurements` | Optional observed time and cost data. Null when no measurements are supplied. |

One-to-one matching uses kind, line range and distinctive literal quote anchors. It cannot verify the meaning of a paraphrased requirement or finding, its severity, the quality of its recommendation, or whether external context invalidates it. A correct finding can also miss an anchor, e.g., because the reviewer cites a different exact excerpt. Legacy gold files with line-only labels remain supported but are weaker. Unmatched items require expert adjudication. Do not report precision, recall or accuracy as semantic metrics without independently adjudicated, representative held-out data.

### Compare two saved reports

Use the same frozen gold file and the same source for a model or pipeline regression comparison. For a document revision, use two gold files with the same declared `comparison_group` and stable gold IDs. The report must identify its own source hash.

```bash
uv run --project vibe/services/agent --frozen python vibe/services/agent/scripts/evaluate_quality_benchmark.py \
  --before-gold vibe/docs/examples/quality-benchmark/defective.gold.json \
  --before-report .artifacts/benchmark-defective.json \
  --gold vibe/docs/examples/quality-benchmark/revised.gold.json \
  --report .artifacts/benchmark-revised.json \
  --output .artifacts/benchmark-comparison.json
```

The output contains both scores and differences (after minus before). Track annotated defect IDs removed from or added to the source, missed persistent labels, extracted requirement count, extraction and review coverage, and finding count. Fewer findings alone do not prove improvement. A model can simply miss existing problems. The expert should review the final source and observations before accepting a correction.

## Saved official report: observed results and limits

An actual completed organizer-case report from 2026-11-25 is stored locally in ignored `.artifacts/organizer-eval/official-report.json`. The existing `official-eval.json`, `official-eval-summary.md` and `manual-rubric.json` document an independent structural and manual review of the original input. They have not been replaced. Exact identifiers:

- Source SHA-256: `e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381`.
- Report SHA-256: `e3db2852367cb77e7ca69497c8dabbec4ec45cb8f2ce91dc8abfecab0dcb1bac`.
- Run ID: `1505f1d9-4882-4661-9494-3ebb94b56283`.

An additional **retrospective subset** annotation and audit of the saved report are in `.artifacts/organizer-eval/quality-anchor-gold.json` and `quality-anchor-audit.json`. The source is the complete organizer requirements document. The new labels cover 26 selected source obligations across both parts and the six core topics of the earlier independent reviewer checklist. They do not form comprehensive extraction gold or a blinded experiment.

Reproduce the additional audit locally without any provider calls:

```bash
python3 vibe/services/agent/scripts/evaluate_quality_benchmark.py \
  --gold .artifacts/organizer-eval/quality-anchor-gold.json \
  --report .artifacts/organizer-eval/official-report.json \
  --output .artifacts/organizer-eval/quality-anchor-audit.json
```

| Check | Observed result |
| --- | --- |
| Existing structural audit | 18/18 checks passed |
| Retrospective selected obligation anchors | 26/26 matched one-to-one |
| Criteria on selected obligations | 234/234 present |
| All reported requirements with exact citations | 56/56 |
| All reported individual criteria / set criteria | 504/504 / 6/6 |
| Findings with exact original citations | 44/44 |
| One-finding anchor match against six core topics | 4/6 (C03 and C04 not matched) |
| Earlier independent manual checklist, core topics | 6/6 fully found across one or more findings |
| Earlier manual checklist, additional topics | 2/8 fully found, 3/8 partly found, 3/8 missed |
| Drex assessment coverage | 44/44 findings assessed; 28 supported, 12 uncertain, 4 unsupported |
| Verdict | Not ready |

The stricter automatic matcher needs a single finding that cites all annotated anchors. The earlier reviewer accepted combinations of findings for C03 and C04. This difference demonstrates why automated matches are not equivalent to expert judgment. The Drex assessment is a second-model opinion, not the ground truth. A verdict and well-formed report do not prove exhaustive extraction or semantic accuracy.

The earlier run summary records an approximate 19:04–19:18 local interval, including rate-limit retries and checkpoints. This is not a controlled stopwatch measurement. It does not include a comparable manual review or verified post-review analyst effort. Provider charges and token usage were not recorded in the report. These missing measurements prevent a quantified speed or cost savings claim.

## Measure manual effort, assisted effort and cost

Freeze a common source, output format and acceptance rubric before a trial. Include completeness of obligation extraction, defect correctness, exact citations, severity and actionability. The assisted mode includes human verification, rejection of false observations and correction of the report to that same quality bar. Distinguish discovery of additional legitimate defects from false positives.

1. Assign a qualified analyst to review the source without the assistant. Record elapsed seconds and active analyst seconds from starting the review through producing an accepted report.
2. Run the assisted review on a comparable source. Record the end-to-end elapsed seconds, the generation duration separately, and all active analyst seconds spent uploading, verifying, editing and approving the final report.
3. Use analysts of comparable experience and counterbalance the order across matched cases. Reusing a learned source in both modes can bias the result. For paired comparisons, obtain both outcomes for the same source, preferably by different independently assigned analysts; repeat on multiple distinct sources and swap assignments.
4. Keep the actual evidence for elapsed time and charges (for example, stopwatch log and provider usage invoice). Direct cost includes observed paid model, infrastructure or tool costs allocated to that review, not labor. Use one currency and a consistent allocation rule. A zero means an observed zero; use null when unknown.
5. Have an independent reviewer adjudicate both reports against the same rubric before including the time observations in a savings comparison. A faster but less complete report must not count as equivalent work.

For generation-only latency, time the existing CLI process, for example with `/usr/bin/time -p -o .artifacts/benchmark-generation-time.txt` before the normal CLI command. The `real` duration records process elapsed seconds. It is not the assisted-review wall time. Include retries, failures and resumed checkpoints in the full assisted effort; note the exact conditions.

Copy [the measurement template](examples/quality-benchmark/measurements.template.json) to an ignored artifact path. Copy the source and report hashes from the score JSON into its two digest fields. Fill the measured numbers and record the measurement conditions in `notes`. Do not insert estimates into measured fields; omit private account or billing identifiers.

Run the scorer again with `--measurements .artifacts/measurement.json`. The manifest must identify the exact saved report and input. It accepts a single analyst per mode; active seconds cannot exceed elapsed seconds. If supplied separately, generation seconds cannot exceed assisted elapsed seconds.

For paired manual/assisted measurements the evaluator calculates:

- Wall time saved = manual wall seconds − assisted wall seconds.
- Active analyst time saved = manual active seconds − assisted active seconds.
- Wall speedup = manual wall seconds / assisted wall seconds, when the assisted duration is positive.
- Direct cost saved = manual direct cost − assisted direct cost, when both amounts are observed.

A negative saving means the assisted process took longer or cost more. Any missing input produces null for the affected calculation. Use `--before-measurements` and `--measurements` when comparing two measured assisted reports; costs are compared only in the same currency. To estimate labor value separately, multiply the measured active seconds saved by an explicitly supplied loaded hourly labor rate / 3600. Subtract any additional direct cost. This is a scenario estimate, not observed financial savings.

Report individual trials and the distribution over multiple cases (median, range, and sample count), plus cases that fail the acceptance rubric. Do not claim a general productivity or ROI improvement from the single saved official run.

## Offline checks

For the paired architecture experiment, `eligible_for_equal_budget_analysis` requires two independently generated, structurally valid reports with the same source hash, model and frozen implementation. Recorded total tokens must be within 10% of the legacy trial. Each underlying run remains available for inspection.

The selected product default for new reviews is `multi_agent` on GPT-6 Luna; `legacy` remains available through explicit mode selection. This decision does not establish universal improvements in semantic quality, time or cost. Keep both modes explicit in benchmark trials and preserve all historical measurements; re-review of an archived report without mode metadata remains a `legacy` run.

To measure expert-confirmed false findings, use independent analyst decisions tied to the SHA-256 of each report file. A reviewer assigns common defect IDs to confirmed findings, so repeated descriptions of a defect count once. An empty list marks a finding explicitly rejected by the analyst. Use `null` or omit an ID when the assessment is pending or uncertain. For example, the input format is

```json
[{
  "run_id": "<UUID from the passport>",
  "report_sha256": "<SHA-256 of the generated JSON file>",
  "findings": {"D-001": ["month-attribution"], "D-002": ["month-attribution"], "D-003": [], "D-004": null}
}]
```

Run `compare_review_modes.py --output-dir <experiment-directory> --adjudications <decisions.json>` to save `adjudicated-comparison.json` alongside the original experiment. This operation reads saved reports and makes no model calls.

For independent structural validation of a report with Reference Inspector, pass the original `--reference-manifest` to `evaluate_spec_report.py`. The audit verifies the selected document hashes and the quotations in both documents. Missing originals leave the reference checks incomplete and return a nonzero result.

From `vibe/services/agent`:

```bash
uv run --frozen pytest -q tests/test_quality_benchmark.py
```

Tests exercise quote-anchor matching, one-to-one extraction and duplicate prevention, missing-obligation criterion coverage, citation validation, report comparison, hash-bound timing/cost inputs and all committed synthetic annotations. Model outputs are not generated by the tests.

## GPT-6 Luna cold-start measurement

On 26 November 2026 the CLI ran both modes through WikiVibe on the same organizer document. The pair below is repetition 2 of the experiment. Both jobs started with empty checkpoints, the same review implementation, a 900-second limit, a 120-second request timeout and three main model slots. The document had no selected external references.

| Measurement | Legacy | Multi-agent |
| --- | ---: | ---: |
| Elapsed time | 4 min 47 s | 8 min 16 s |
| Extracted requirements | 57 | 62 |
| Findings with verified source quotations | 45 / 45 | 61 / 61 |
| Structural checks passed | 25 / 25 | 34 / 34 |
| Matched annotated obligations | 25 / 26 | 26 / 26 |
| Matched annotated defect themes | 3 / 6 | 6 / 6 |
| Input + output tokens | 158,359 | 463,121 |

Both reports produced the `not_ready` verdict. The multi-agent report contains 399 graph nodes, 980 edges and four unresolved disagreements. It records deterministic fallback during consolidation. Total token usage was 2.92 times the legacy usage, outside the experiment's equal-budget tolerance. The provider supplied no verified cost.

Reliability varied across cold starts. The first multi-agent trial on the same document stopped after 131 seconds when the logic expert returned an invalid enumeration after correction. Its failed attempt is included in the experiment. The first legacy trial completed in 286 seconds. Direct comparison against the source found repeated findings and unsupported claims in both successful reports inspected. Automated annotation matches and quotation checks measure different properties from human-adjudicated precision.

### Additional PDF stress test

A separate six-page PDF was submitted once through the multi-agent CLI on the same model. After 5 min 56 s, the document-level Logic Inspector request reached its 120-second provider timeout. The CLI exited with code 1 after 21 model calls and produced a failure metrics file. Coverage was incomplete, so the run produced no report or verdict. Token totals and cost are unavailable for this attempt.

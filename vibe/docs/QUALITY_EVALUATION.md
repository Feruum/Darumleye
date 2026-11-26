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

If no report exists, generate it with the normal specification review CLI described in [SPEC_REVIEW.md](SPEC_REVIEW.md). That CLI calls the configured provider and may incur charges. Repeat for each of the four sources. Do not submit the gold annotation to the model. Record the exact source and report SHA-256, model, agent revision, settings and run ID. The dated experiment sections below identify the sources and outputs used in live trials.

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

## Comparing the three review modes

The benchmark runs the original staged review (`legacy`), paired validation (`multi_agent`) and direct validation (`multi_agent_direct`). It rotates their order across cases and repetitions. For each pair, the recorded latency and annotation deltas are the candidate value minus the baseline value. Equal-budget comparisons require independent, structurally valid reports on the same source, model, implementation and settings, with total token usage within 10% of the baseline in that pair. Per-stage request counts include corrective calls. Future failed trials also retain allowlisted validation reasons observed across overlapping expert batches, even when the cause of the final exit is ambiguous. Archives retain their original mode labels.

To run nine fresh CLI trials, one for each mode on each input, use the annotated training fixture, the organizer document, and a local stress input. Run the following command from the repository root:

```bash
uv run --project vibe/services/agent --frozen python vibe/services/agent/scripts/compare_review_modes.py \
  --case defective --case official --case stress --stress-source /path/to/stress.pdf \
  --env-file .env --repetitions 1 --timeout-seconds 900 --output-dir .artifacts/review-three-mode-comparison
```

Each trial starts with its own empty checkpoint directory. Interrupted attempts retain their validated stages. A restart of a cold benchmark trial uses a fresh directory. The per-trial metrics include application requests, task-level retries, batch splits, and the tokens the provider reported for completed responses. A timeout can leave the total usage unknown even if the final report succeeds. Aggregate reported input and output token counters include failed trials.

The runner evaluates each saved report against its original document. With an unlabelled stress input, it records structural validity and timing. Defect recall requires an independently annotated source.

To exercise the Reference Inspector, add `--reference-manifest vibe/docs/examples/quality-benchmark/reference-rubric.json` to the comparison command and use a fresh output directory. This shared synthetic rubric checks measurable conditions and consistency of numerical acceptance criteria when they occur. The runner passes the same manifest to all three modes. Each trial starts with its own empty reference index. The organizer-source trial in this configuration includes the supplementary rubric. The official input SHA-256 check still applies.

The experiment records the SHA-256 of the manifest and the ordered reference originals. A change to either prevents resume. The independent audit checks the source register, reference status, requirement IDs, and both originals cited by each reference observation. Paired and direct reports must also show a completed Reference Inspector in the evidence graph. Zero reference observations can be valid. An unavailable comparison fails the benchmark audit even when the CLI produced a core report. The report and its usage remain available for inspection.

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

### OpenRouter GPT-6 Luna at low reasoning effort, 26 November 2026

All model stages requested `openai/gpt-6-luna` with `reasoning.effort=low`. The routes shared a 900-second deadline, a 120-second request timeout, three main model slots plus the separate reference branch, zero SDK retries and one bounded transient-task retry. Each trial started with its own empty checkpoints and reference index. The selected external source was a synthetic rubric on measurable requirements. The reference comparison used local retrieval. Drex was disabled.

An initial integration series stopped during extraction on all nine submissions. OpenRouter returned HTTP 404. A controlled pair of synthetic requests showed that strict parameter routing rejected Luna with `temperature=0` and accepted the same request with the temperature field omitted. The corrected client keeps strict parameter routing. The initial failures recorded 18 application-level calls and no reported usage or price.

On the corrected client, the CLI ran three cold trials per route and document, rotating the order across repetitions. **24 of 27 reports passed every structural check.** The source hashes, runtime fingerprint, model settings and reference identity matched across all trials. Each completed report covered nine criteria per extracted requirement, six set checks and all 15 readiness checks. All 24 verdicts were `not_ready`. The 17 completed multi-agent reports recorded all six experts. Every one of the 1,996 core findings in the completed reports had a verified source citation.

| Route | Audited reports | Training | Organizer | Stress PDF | Reported spend across all attempts |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legacy | 7/9 | 0:28 · 3/3 | 1:16 · 2/3 | 2:50 · 2/3 | ≥ $0.2988 |
| Paired | 9/9 | 0:43 · 3/3 | 2:48 · 3/3 | 5:24 · 3/3 | ≥ $0.6270 |
| Direct | 8/9 | 0:41 · 3/3 | 2:45 · 2/3 | 5:04 · 3/3 | $0.6036 |

Times are medians of completed, audited reports. Counts show successful reports per three submissions. Amounts are rounded to four decimal places. Across all 920 application-level calls, OpenRouter reported 5,585,951 input and 1,745,376 output tokens and USD 1.529379705 in charges. These are subtotals. The first Paired stress run retried three tasks and the second Legacy stress run stopped early. Both had incomplete accounting. The SDK supplied model metadata for 914 responses, all labeled `openai/gpt-6-luna`. The [per-trial measurements](examples/quality-benchmark/openrouter-luna-low.csv) include partial usage, failure reasons when available, coverage, matched themes, source-checked stress finding IDs and run IDs.

Three cold submissions ended without a report. On the organizer document, Legacy rejected a quotation absent from the cited original and Direct rejected invalid mandatory criteria in the Risk Inspector's set-level response. On the stress PDF, Legacy exited after 172 seconds. The saved diagnostic did not identify a terminal stage or cause.

**Detection and interpretation**

- All nine training trials matched all four seeded defects and all six annotated obligations. On the organizer document, the automated defect matcher found 4/6 in both completed Legacy runs, 5/6, 6/6 and 6/6 in Paired, and 5/6 in both Direct runs. Extraction varied independently. The Paired report with the fewest matched obligations covered 19/26 and still matched all six annotated defect themes.
- On the stress PDF all eight completed reports identified the three conflicts selected from the original before inspecting the model findings. Legacy produced two audited stress reports, and both Paired and Direct produced three. The stress input has no comprehensive defect annotation.

**Source checks from the first repetition**

- On the training input all three routes matched the four annotated themes. The conflict over access hours appeared in two Legacy findings, three Paired findings and six Direct findings. Direct's 19 core findings covered the same four themes.
- On the organizer input the automated matcher scored Legacy 4/6 and both multi-agent routes 5/6. Comparing findings to source lines showed where the rule missed a theme. Legacy D-004 describes the missing read contract and D-026 discusses HTTP 200 with an error payload. The matcher requires additional source lines. Paired D-033 and Direct D-026 identify the monthly push conflict but classify it as ambiguous. The retrospective label requires a conflict classification.
- The stress PDF has no complete gold labels. A source check of three conflicts found all three in each route. In order of FR-15/FR-16, NT-01/NT-02 and API-07/API-08, examples are Legacy D-015/D-024/D-047, Paired D-025/D-029/D-012 and Direct D-122/D-050/D-030. The first pair concerns repeated authentication in a single session, the second concerns two ordinary limit changes in one day, and the third concerns retries during outages longer than 500 ms.
- Paired D-019 on the organizer input claims the error example lacks the `result` field. Its own verified quotation from line 171 contains `"result":"ERROR"`. This is one confirmed example of an incorrect interpretation despite an exact quote. An independent banking expert has yet to adjudicate precision across the reports.

### Three-document CLI comparison, 26 November 2026

Each source was submitted once per route with a fresh checkpoint, through the normal CLI. Both routes used `gpt-6-luna` on WikiVibe, three primary model slots, a 120-second request timeout, a 900-second analysis limit, the same reasoning levels and Drex configuration. No external references were supplied, so five expert roles ran. Reference Inspector routing was covered separately by tests. The runner alternated which route went first across documents. The implementation fingerprint began with `4a10063aaa7b`.

| Source | Route | Elapsed | Structural checks | Extracted requirements | Findings | Matched defect themes | Model calls | Total tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Training | Paired | 1:59 | 32/32 | 6 | 17 | 4/4 | 17 | 64,899 |
| Training | Direct | 1:52 | 32/32 | 6 | 28 | 4/4 | 15 | 58,854 |
| Organizer | Paired | 8:12 | 34/34 | 52 | 67 | 6/6 | 46 | 425,196 |
| Organizer | Direct | 2:10 to failure | — | — | — | — | 10 | incomplete |
| Stress PDF | Paired | 7:47 to failure | — | — | — | — | 27 | incomplete |
| Stress PDF | Direct | 10:33 to failure | — | — | — | — | 40 | incomplete |

On the training case, both reports covered all six annotated obligations. Looking at source lines 5–6, the same conflict between business-hours access and round-the-clock access appeared in five paired findings and nine direct findings. This accounts for part of the difference in report length. The direct report also asked which time zone and interval boundaries to use. This spot check does not provide a false-positive rate.

On the organizer document, the paired report matched all six retrospectively annotated defect themes and 20 of 26 obligation anchors. The direct trial stopped before central validation. The CLI recorded two extraction requests and eight requests for individual Logic Inspector batches. Its failure record contains no confirmed exception class or failed stage. The elapsed time to failure cannot be used as generation latency for a completed review.

A separate cold diagnostic repeat of organizer/direct took 91.75 seconds and eight model calls. The first Logic Inspector batch response that needed correction cited evidence outside the scope of its requirement. After correction, the response failed the check that each supplied requirement ID occurred exactly once. The harness stopped before the common validator and produced no report. This repeat is recorded separately from the six planned trials.

The six-page stress PDF had no independent defect labels. Both CLI attempts stopped before consolidation and produced no report or verdict. The paired attempt recorded a provider timeout on the Logic Inspector's document-level review. The direct attempt recorded a generic failure during the expert stage. Its exact cause and complete token usage are unavailable.

Of the six planned cold starts, three produced reports that passed every structural check. Training was the only completed pair. Direct saved 7.05 seconds and 6,045 tokens on this input. All three completed reports recorded deterministic fallback during consolidation and verified quotations for every finding. No reliable currency tariff was available. These observations do not establish a general speed or accuracy advantage.

### Three-mode recovery experiment, 26 November 2026

Each series ran nine cold CLI trials, one for each combination of three documents and three modes. Both used the same inputs, annotations, trial order, `gpt-6-luna`, reasoning levels, three main generation slots, a 120-second API timeout, a 900-second overall limit, zero SDK retries and the same Drex settings. No external references were selected. The original generation implementation fingerprint began with `5583f0a37deb`, and the recovery implementation with `b3fbd7b6153b`. A metadata-only Langfuse exporter correction was tested separately. The recovery series enabled one bounded task-level transport retry with a one-second initial backoff and used separate fresh checkpoint directories. These limits applied to all three modes. The provider was shared across the two time windows.

**Before task recovery**

| Input | Route | Elapsed | Result | Model calls | Task retries / splits | Reported input / output tokens |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Training | Legacy | 1:10 | 23/23 checks | 3 | — | 5,902 / 6,031 |
| Training | Paired | 2:34 | API timeout · `multi_gap_hunter_individual` | 10 | — | 23,380 / 9,200 (partial) |
| Training | Direct | 2:32 | API timeout · `multi_scenario_builder_set` | 11 | — | 26,787 / 10,541 (partial) |
| Organizer | Legacy | 2:42 | API timeout · `set` | 10 | — | 31,730 / 13,284 (partial) |
| Organizer | Paired | 2:02 | API timeout · `extraction` | 2 | — | 3,267 / 1,660 (partial) |
| Organizer | Direct | 2:29 | validation error · `multi_logic_inspector_individual` | 12 | — | 59,438 / 18,403 (partial) |
| Stress PDF | Legacy | 2:02 | API timeout · `extraction` | 3 | — | 0 / 0 (partial) |
| Stress PDF | Paired | 2:02 | API timeout · `extraction` | 3 | — | 0 / 0 (partial) |
| Stress PDF | Direct | 2:02 | API timeout · `extraction` | 3 | — | 0 / 0 (partial) |

**With task recovery**

| Input | Route | Elapsed | Result | Model calls | Task retries / splits | Reported input / output tokens |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| Training | Legacy | 4:04 | API timeout · `extraction` | 2 | 1 / 0 | 0 / 0 (partial) |
| Training | Paired | 4:04 | API timeout · `extraction` | 2 | 1 / 0 | 0 / 0 (partial) |
| Training | Direct | 4:00 | 32/32 checks | 14 | 1 / 0 | 42,727 / 13,146 (partial) |
| Organizer | Legacy | 8:20 | 25/25 checks | 21 | 5 / 0 | 106,197 / 38,504 (partial) |
| Organizer | Paired | 11:17 | expert deadline · `multi_gap_hunter_set` | 26 | 12 / 0 | 75,575 / 18,445 (partial) |
| Organizer | Direct | 4:04 | API timeout · `extraction` | 4 | 2 / 0 | 0 / 0 (partial) |
| Stress PDF | Legacy | 10:24 | review failed | 24 | 9 / 0 | 55,868 / 29,347 (partial) |
| Stress PDF | Paired | 0:53 | connection error · `extraction` | 5 | 2 / 0 | 0 / 0 (partial) |
| Stress PDF | Direct | 11:17 | expert deadline · `multi_gap_hunter_individual` | 35 | 6 / 0 | 174,518 / 65,645 (partial) |

**Coverage and quality of completed reports**

| Series | Input | Route | Requirements | Individual / set / readiness cells | Findings with citations | Matched obligations | Matched defect themes | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Before task recovery | Training | Legacy | 6 | 54 / 6 / 15 | 10/10 | 6/6 | 4/4 | `not_ready` |
| With task recovery | Training | Direct | 6 | 54 / 6 / 15 | 19/19 | 6/6 | 4/4 | `not_ready` |
| With task recovery | Organizer | Legacy | 51 | 459 / 6 / 15 | 60/60 | 25/26 | 4/6 | `not_ready` |

Each completed report passed its independent audit against the original, including checks on the exact mode, citations, requirement coverage and the full readiness checklist. The training labels cover four injected defects. On source lines 5–6, the same access-hours conflict appeared twice in the original Legacy report and three times in the Direct report with recovery. The organizer annotations are retrospective. The stress PDF has no independent defect labels and produced no report. Precision and defect recall on that input remain unknown.

The first series produced one audited report in nine trials. With recovery, two of nine produced audited reports. Both successful recovery runs included a network retry. The paired route previously produced an audited organizer report in a separate two-mode trial. One cold attempt per cell does not establish comparative reliability or speed.

The provider reported at least 150,504 input and 59,119 output tokens across 57 calls before recovery, and at least 454,885 input and 165,087 output tokens across 133 calls after recovery. This includes failed trials. Missing usage from a failed call is unknown, including when the recorded subtotal is zero. Monetary cost is unavailable. The CLI recorded 39 task-level network retries and no live batch splits. Regression tests exercised splitting after repeated validation errors and reuse of validated child results. The two 11:17 outcomes reached the expert deadline with time reserved for finalization. The Direct stress run retained 22 validated stage responses. The Legacy stress run stopped with a generic failure after nine retries, so its terminal cause remains unattributed.

## Historical legacy versus paired measurement on GPT-6 Luna

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

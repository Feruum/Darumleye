# Review retention audit

The audit measures the preservation of expert observations through final review, separately from the correctness of the observations. It uses only an existing JSON report. It does not call a model, alter findings, or modify review state.

## Evidence-link retention

From the repository root:

```bash
python3 vibe/services/agent/scripts/audit_review_retention.py \
  --report .artifacts/review.json
```

The script counts severity-bearing observations from the five core experts. It excludes dissent records, reference observations, and deterministic code checks. A linked observation retains its original evidence only when its literal source anchors also appear in the linked final finding. An unlinked observation may be deliberately quarantined; it is not automatically a lost valid defect. A report without a graph does not have a graph-retention score.

## Semantic retention

An independent analyst can label the observation and final finding IDs using the `label_template` returned by an unlabelled audit. Save the template as a separate JSON file. It is bound to the exact report SHA-256. For each item, use a stable root-defect ID for a confirmed defect, `false_positive` for a rejected item, or `null` for an unfinished assessment. Equivalent observations receive the same root ID even when their wording differs.

```bash
python3 vibe/services/agent/scripts/audit_review_retention.py \
  --report .artifacts/review.json --labels .artifacts/retention-labels.json
```

Only complete labels yield semantic metrics. `root_retention` is the fraction of unique confirmed observation roots that appear in the final findings. `confirmed_observation_link_retention` additionally checks the graph link points to the same independently identified root. `confirmed_observation_evidence_retention` additionally requires original evidence preservation. The output lists omitted root IDs, incorrect merges, repeated final roots, and false-positive final findings.

The saved graph cannot show an observation lost before it was recorded. These metrics do not establish recall against every defect in the original document. Use an independent annotated source benchmark for that purpose.

## Source fact context and scenario witnesses

The multi-agent review derives a bounded index from verified requirement quotations once per run. It records explicit quantities, numeric bounds, calendar days and clock times. It normalizes compatible quantities for comparison, while retaining the exact original source location and text. Each expert receives the subset that refers to its supplied requirement IDs. The serialized context is limited to 1,600 characters for individual tasks and 4,200 characters for set tasks. The index does not call an additional model.

Comparison candidates and probe values are not confirmed defects. They identify places to check conditions and exceptions. Equivalent unit conversions and merely different example inputs are not inconsistent. The scenario expert uses the same existing witness schema to describe the initial state, concrete input, action, both rule outcomes, and any unresolved decision. The agent does not execute software tests or infer an absent requirement from an incomplete index.

The full profile adds literal subject, status, condition, outcome and interface-field mentions, with a separate bounded context. Z3 checks overlap and mutually incompatible outcomes only for explicitly recognized status and scalar rules. Unparsed conditions and exceptions prevent a formal conclusion. An unspecified range endpoint is not assumed inclusive. The solver uses typed expressions rather than executing model-generated code. Witnesses retain source citations and appear as non-severity-bearing observations in the Evidence Graph and Markdown report. They do not automatically change the verdict.

Configure `AGENT_SPEC_REVIEW_FACT_PROFILE` as `off`, `numeric`, or `full` (default). Configure `AGENT_SPEC_REVIEW_USE_Z3=false` to disable the solver while retaining the full clause index. The locked Z3 dependency is installed with the application; feature settings are included in checkpoint identity and benchmark manifests. Both profiles use the same existing expert structure and provider model.

## Root-defect consolidation

Merge-candidate retrieval normalizes numerical thousands separators and explicit requirement identifiers so presentation differences do not split a shared issue. It retains different business quantities, temporal direction and incompatible polarity. A source relationship alone does not authorize a semantic merge; the reducer must confirm that the observations require the same repair decision.

Candidate retrieval also accepts a narrower literal anchor set when the repair vocabulary overlaps substantially, including cross-kind and cross-ID candidates; repair similarity ranks pairs without authorizing a merge. Expert prompts require one independent repair decision per finding and the reducer sees the original recommendations. Multi-line anchor overlap is counted by source coordinates, and candidate clusters are reconsidered after a successful merge. A verified conflict links complete registry anchors on both sides without adding context-only citations from unrelated observations. Disputes preserve original requirement–criterion pairs instead of taking the Cartesian product of a merged group. Row-level recheck outcomes replace contradictory positive comments; a positive result can be retained when relevant literal counterevidence defeats the specific challenge. Original expert comments remain available in the graph. Invalid multi-item adjudication batches are bisected within the shared deadline.

Two source-local cases can be clustered before the semantic reducer: repeated descriptions of one statically proven inverted numeric range, and repeated missing-value claims tied to the identical literal blank field at the same source coordinates. Distinct fields, unrelated evidence, and independently actionable claims remain separate. Dense semantic candidate components use batches of at most six groups so all possible pair relations fit the reducer limit. After global reduction up to twelve residual candidate pairs receive an independent repair-decision check. Cliques require approval of every constituent pair; pair approval is never assumed transitive. The graph retains every original observation and exact source quote.

## Extended synthetic source cases

[Two additional cases](examples/quality-benchmark/extended/) are kept separate from the default comparison matrix so existing benchmark runs do not silently gain additional provider calls. Their labels are fixed before the first live run. They are pedagogical cases, not a blinded independent expert corpus.

| Case | Annotated obligations | Injected root defects | Negative controls |
|---|---:|---:|---:|
| Invoice routing | 7 | 3 | 2 |
| Equipment reservation | 9 | 3 | 1 |

Run the normal CLI with the source document only. Do not send the gold file to the reviewing model. Score the resulting JSON with the existing `evaluate_quality_benchmark.py` and the corresponding `*.gold.json`. Independently review severity and the negative controls; the anchor matcher does not score those additional fields automatically.

When comparing legacy, direct and paired validation, use the same frozen source and model settings, distinct run IDs, and record token use and elapsed time. Report the anchor agreement and semantic retention separately. A lower finding count alone is not evidence of better deduplication.

To run the extended cases in paired and direct modes only:

```bash
AGENT_SPEC_REVIEW_FACT_PROFILE=full AGENT_SPEC_REVIEW_USE_Z3=true \
  uv run --project vibe/services/agent --frozen python vibe/services/agent/scripts/compare_review_modes.py \
  --benchmark-dir vibe/docs/examples/quality-benchmark/extended \
  --case equipment-reservation --case invoice-routing \
  --mode multi_agent --mode multi_agent_direct --repetitions 1 \
  --output-dir .artifacts/full-profile-comparison
```

Repeat with `AGENT_SPEC_REVIEW_FACT_PROFILE=off`, `AGENT_SPEC_REVIEW_USE_Z3=false`, and a fresh output directory to measure a control profile. A single trial per configuration is a pilot, not an estimate of average model quality.

## Observed retention and current evidence

Historical offline replay of the deterministic root-consolidation step (2026-11-27) reduced the earlier invoice-routing paired groups from 15 to 7 and direct groups from 9 to 5, retaining all 28 original observations in each. An earlier official report changed from 18 to 17 groups, retaining 26 observations; another 23-group official report was unchanged. This isolates a code step and is not a new cold model evaluation. The subsequent final official paired run contains 17 findings, 218 nodes and 463 edges; 10 candidates went through adjudication (9 confirmed, 1 rejected). Three assessment rows remain uncertain. An independent labeled corpus is still needed to quantify root recall. See [the final evaluation](QUALITY_EVALUATION.md#final-organizer-run-27-november-2026) and [the extended live pilot](QUALITY_EVALUATION.md#extended-fact-and-formal-profile-pilot).

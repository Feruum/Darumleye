# Semantic holdout evaluation

This evaluation measures whether a finding identifies a real, independently actionable defect, not just whether it contains a valid quotation or matches a keyword. It supplements the structural audit and the original anchor-based benchmark. The organizer's source is not a labeled holdout.

## Protocol

1. An independent AI corpus author writes natural synthetic requirements, predeclares distinct defect roots and negative controls, and records exact source anchors and SHA-256 hashes. The authors do not inspect the analyzer's prompts or outputs.
2. The analyzer receives the source document only. The benchmark executes the real `multi_agent` CLI with GPT-6 Luna on OpenRouter, the full fact profile, Z3, low primary reasoning, auto adjudication reasoning, concurrency 3 per document, and no Drex or external references. All three documents run in parallel with a 600-second deadline per document. Each run manifest captures the repository commit and implementation file hashes, including uncommitted code changes.
3. A separate AI reviewer evaluates each final finding against the full source and frozen labels. It records a rationale, supported root ID or unsupported classification, negative-control hits where applicable, and hashes of the source, gold and report. The analyzer does not receive the reviewer decisions.
4. The offline scorer validates the hashes and source evidence. It counts unique supported roots (predeclared and independently confirmed additional defects) divided by all reported findings as semantic precision. Duplicates, false positives and uncertain findings count against precision. Gold recall is the fraction of predeclared roots found, with no extra credit for additional defects. Severity agreement is reported separately.
5. The target is at least 95% semantic precision **and** 95% gold recall. An empty report cannot pass. A structurally correct report is not necessarily semantically correct.

The original six labeled documents remain in the parent benchmark: 14 known defect roots, 44 annotated requirements, and three negative controls. The three new holdouts contain nine specifications, 51 predeclared roots, 144 annotated requirements and 46 negative controls. This historical benchmark contains 15 specifications, 65 labeled roots, 188 annotated requirements and 49 negative controls. The new sets have independent labels; once analyzed, a set becomes regression data, not an unseen validation set for subsequent changes. These results are synthetic and AI-reviewed, not a human expert certification or a guarantee on arbitrary real specifications.

## Saved evaluations

| Run | Documents | Gold roots | Reported | Unique supported | Precision | Gold recall | Duplicates | False positives | Uncertain | Negative controls hit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [A — initial pipeline](results/baseline-a/semantic-score.json) | 3 | 15 | 24 | 14 | 58.33% | 93.33% | 6 | 3 | 1 | 1/15 |
| [B — revised pipeline](results/improved-b/semantic-score.json) | 3 | 18 | 28 | 22 | 78.57% | 100% | 3 | 3 | 0 | 0/14 |
| [C — second revision, unseen reserve](results/final-c/semantic-score.json) | 3 | 18 | 23 | 19 | 82.61% | 100% | 1 | 3 | 0 | 1/17 |

These are different independently written sets, so the differences are observations, not a controlled same-input comparison. All nine reports passed the 31-point structural audit. Set B contains four supported additional defects beyond its 18 predeclared roots; C contains one. Exact severity agreement was 7/14 for A and 4/18 for B and C; the latter includes differences between the corpus authors' severity labels and the pipeline's implementation/acceptance-blocker rubric. For the version recorded in C, the scorer found all 18 predeclared roots and measured **82.61% semantic precision**, with one duplicate and three unsupported findings. The nine real reports used 358 model calls and USD 0.43582767 in provider-reported cost, with complete reported usage; C took 165–177 seconds per document (parallel execution).

The later regression run on C and the partial evaluation on 30 additional specifications have separate implementation fingerprints. [Subsequent evaluation](../../../SEMANTIC_EVALUATION_30.ru.md)

## Reproduce scoring

From the repository root, run the offline scorer with the saved source, gold, report and review files. Example for one case:

```bash
uv run --project vibe/services/agent --frozen python vibe/services/agent/scripts/evaluate_semantic_holdout.py \
  --case vibe/docs/examples/quality-benchmark/semantic-holdout/holdout-b/equipment-loans.md \
    vibe/docs/examples/quality-benchmark/semantic-holdout/holdout-b/equipment-loans.gold.json \
    vibe/docs/examples/quality-benchmark/semantic-holdout/results/improved-b/equipment-loans.json \
    vibe/docs/examples/quality-benchmark/semantic-holdout/results/improved-b/equipment-loans.review.json
```

Repeat `--case` for each document to reproduce the micro-aggregate. The raw reports, independent reviews, metrics, structural audits, completion status and implementation fingerprints are saved under `results/`. Corpus manifests record original source and label hashes. To generate a new report, run `python -m agent.spec_review_cli --input <source.md> --output <new-report.json> --review-mode multi_agent --env-file <configured.env>` with the profile recorded in its run manifest. The reviewer must reassess and bind each new report; old review files cannot be reused against a different report hash.

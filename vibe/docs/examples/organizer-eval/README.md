# Organizer case evaluation

The evaluation uses the original Markdown specification and a frozen SHA-256. [gold.json](gold.json) records the expected status, rationale and verbatim line anchors for each case. The two negative controls were added for this audit. The labels cover a subset of possible defects. [Organizer case](https://github.com/BCC-HUB/ai-requirements-validator-task)

| Expected status | IDs and source lines |
| --- | --- |
| Confirmed | GT-001 (39–40), GT-002 (38, 64), GT-003 (52, 120), GT-006 (151, 171), GT-008 (151, 165) |
| Disputed | GT-004 (104, 120), GT-005 (108), GT-007 (175, 189) |
| Reject | NEG-001 (46), NEG-002 (40, 63, 126) |

## Run the standard model

The new GPT‑6 Luna run is blocked in this checkout because `AGENT_LLM_API_KEY` is unavailable. Copy the OpenRouter endpoint and `openai/gpt-6-luna` model from `vibe/.env.example` into `vibe/.env` and set the matching API key. The generation call uses the configured provider. Run the commands from the repository root and send only the source Markdown to the model.

```bash
mkdir -p .artifacts/organizer-eval
curl -fsSL https://raw.githubusercontent.com/BCC-HUB/ai-requirements-validator-task/HEAD/docs/requirements.md \
  -o .artifacts/organizer-eval/requirements.md
shasum -a 256 .artifacts/organizer-eval/requirements.md
```

The digest must be `e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381`.

```bash
uv run --project vibe/services/agent --frozen python -m agent.spec_review_cli \
  --input .artifacts/organizer-eval/requirements.md \
  --output .artifacts/organizer-eval/luna-report.json \
  --env-file vibe/.env --review-mode multi_agent --timeout-seconds 900 \
  --metrics-output .artifacts/organizer-eval/luna-metrics.json
```

## Export, adjudicate and score

The scorer runs offline with Python 3. It verifies the original source and report passport hashes. Its exact-anchor suggestions help locate evidence. An analyst must still decide whether the finding follows from its citations and whether an unmatched finding is valid.

```bash
python3 vibe/services/agent/scripts/evaluate_organizer_report.py \
  --source .artifacts/organizer-eval/requirements.md \
  --report .artifacts/organizer-eval/luna-report.json \
  > .artifacts/organizer-eval/luna-adjudications.json
```

Edit only the `decisions` map. For each report finding, enter one matching GT or NEG ID, `valid_extra` for a supported defect outside this set, `false_positive` for another unsupported finding, or `unresolved` when there is insufficient information. Classify a finding as NEG only if it makes the specific false claim. A confirmed match needs verifiable source citations and a substantively correct description. Reusing the same GT ID records a duplicate.

```bash
python3 vibe/services/agent/scripts/evaluate_organizer_report.py \
  --source .artifacts/organizer-eval/requirements.md \
  --report .artifacts/organizer-eval/luna-report.json \
  --adjudications .artifacts/organizer-eval/luna-adjudications.json \
  > .artifacts/organizer-eval/luna-score.json
```

- **Confirmed recall** is the number of unique confirmed labels found divided by five.
- **Confirmed precision** is unique confirmed labels found divided by that count plus the number of false-positive findings. Disputed items, confirmed duplicates and valid extra defects are shown separately.
- **Negative-control hit rate** is the fraction of the two false claims that appeared in the report. A hit is a false positive. The score also lists all adjudicated false-positive finding IDs.

Metrics remain null until all findings have a resolved decision. Empty denominators also produce null. The scores describe only this source and reference subset.

## Synthetic smoke check

The small fixture is hand-written and marked `synthetic_fixture`. It exercises scoring branches against the same source digest and contains no output from GPT‑6 Luna.

```bash
python3 vibe/services/agent/scripts/evaluate_organizer_report.py \
  --source .artifacts/organizer-eval/requirements.md \
  --report vibe/docs/examples/organizer-eval/synthetic-report.json \
  --adjudications vibe/docs/examples/organizer-eval/synthetic-adjudications.json
```

Expected fixture results are confirmed precision **2/3**, confirmed recall **2/5**, negative-control hit rate **1/2**, one disputed finding and one valid extra.

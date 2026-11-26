"""Score a saved organizer-case report using explicit analyst adjudications."""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from evaluate_quality_benchmark import covers, ratio, read_json, valid_evidence, validate_gold


DEFAULT_GOLD = Path(__file__).resolve().parents[3] / "docs/examples/organizer-eval/gold.json"
OTHER_DECISIONS = ("valid_extra", "false_positive", "unresolved")


def evaluate(source_path, report_path, gold_path, adjudications_path=None):
    gold = read_json(gold_path)
    source = source_path.read_bytes()
    source_hash = sha256(source).hexdigest()
    if source_hash != gold["source_sha256"]:
        raise ValueError("Source does not match the frozen Markdown SHA-256")
    lines = source.decode("utf-8-sig").splitlines()
    validate_gold(gold["labels"], lines)
    labels = {item["id"]: item for item in gold["labels"]}
    if any(item["expected"] not in {"confirmed", "review", "reject"} for item in labels.values()):
        raise ValueError("Unknown expected label status")

    raw_report = report_path.read_bytes()
    report_hash = sha256(raw_report).hexdigest()
    report = json.loads(raw_report)
    if report["passport"]["document_sha256"] != source_hash:
        raise ValueError("Report passport does not identify the annotated source")
    findings = {
        f"D-{index:03d}": item for index, item in enumerate(report["findings"], 1)
    }
    decisions = {}
    if adjudications_path:
        adjudications = read_json(adjudications_path)
        if adjudications["report_sha256"] != report_hash:
            raise ValueError("Adjudications belong to a different report")
        decisions = adjudications["decisions"]
        if not isinstance(decisions, dict) or set(decisions) - findings.keys():
            raise ValueError("Adjudications must map known report IDs to decisions")

    rows = {}
    for finding_id, finding in findings.items():
        evidence = finding["evidence"]
        valid = bool(evidence) and all(valid_evidence(item, lines) for item in evidence)
        decision = decisions.get(finding_id)
        if decision is not None and decision not in (*labels, *OTHER_DECISIONS):
            raise ValueError(f"Unknown decision for {finding_id}: {decision}")
        if decision in labels and labels[decision]["expected"] == "confirmed" and not valid:
            raise ValueError(f"{finding_id} needs verifiable source evidence for a confirmed match")
        rows[finding_id] = {
            "issue": finding["issue"],
            "kind": finding["kind"],
            "evidence": evidence,
            "verified_citations": valid,
            "full_anchor_candidates": [
                key for key, label in labels.items() if valid and covers(evidence, label)
            ],
        }

    by_decision = {key: [] for key in (*labels, *OTHER_DECISIONS)}
    pending = []
    for finding_id in findings:
        decision = decisions.get(finding_id)
        if decision is None:
            pending.append(finding_id)
        else:
            by_decision[decision].append(finding_id)

    confirmed = {key: by_decision[key] for key, item in labels.items()
                 if item["expected"] == "confirmed"}
    disputed = {key: by_decision[key] for key, item in labels.items()
                if item["expected"] == "review"}
    controls = {key: by_decision[key] for key, item in labels.items()
                if item["expected"] == "reject"}
    true_positives = sum(bool(ids) for ids in confirmed.values())
    false_positives = [*by_decision["false_positive"],
                       *(finding_id for ids in controls.values() for finding_id in ids)]
    complete = not pending and not by_decision["unresolved"]
    return {
        "source_sha256": source_hash,
        "report_sha256": report_hash,
        "model": report["passport"].get("model"),
        "synthetic_fixture": report.get("synthetic_fixture", False),
        "counts_final": complete,
        "metrics": {
            "confirmed_precision": ratio(true_positives, true_positives + len(false_positives)),
            "confirmed_recall": ratio(true_positives, len(confirmed)),
            "negative_control_hit_rate": ratio(sum(bool(ids) for ids in controls.values()), len(controls)),
        } if complete else None,
        "confirmed": confirmed,
        "confirmed_duplicates": [finding_id for ids in confirmed.values() for finding_id in ids[1:]],
        "disputed": disputed,
        "negative_controls": controls,
        "false_positives": false_positives,
        "valid_extra": by_decision["valid_extra"],
        "unresolved": by_decision["unresolved"],
        "pending": pending,
        "findings": rows,
        "decisions": {finding_id: decisions.get(finding_id) for finding_id in findings},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--adjudications", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate(args.source, args.report, args.gold, args.adjudications)
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

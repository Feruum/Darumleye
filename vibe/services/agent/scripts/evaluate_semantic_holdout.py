"""Score frozen holdout reports with independent, source-bound semantic decisions.

This scorer never invokes a model. It counts each supported root once. A repeated
finding, unsupported finding or uncertain assessment remains in the precision
denominator; valid additional findings do not expand the frozen recall denominator.
"""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from evaluate_quality_benchmark import ratio, valid_evidence, validate_gold

STATUSES = {"gold", "valid_extra", "duplicate", "false_positive", "uncertain"}


def evaluate_semantic_case(source: Path, gold: Path, report: Path, review: Path) -> dict:
    source_bytes, gold_bytes, report_bytes = (p.read_bytes() for p in (source, gold, report))
    digests = dict(zip(("source_sha256", "gold_sha256", "report_sha256"),
                       (sha256(data).hexdigest() for data in (source_bytes, gold_bytes, report_bytes)), strict=True))
    gold_data, report_data, decisions = (json.loads(data) for data in (gold_bytes, report_bytes, review.read_bytes()))
    if any(decisions.get(key) != value for key, value in digests.items()):
        raise ValueError("Review SHA-256 does not match the frozen input, labels and report")
    if Path(gold_data.get("source", "")).name != source.name or report_data["passport"].get("document_sha256") != digests["source_sha256"]:
        raise ValueError("Report and annotation must identify the same source")
    lines = source_bytes.decode("utf-8-sig").splitlines()
    labels = gold_data["defects"]
    controls = gold_data.get("negative_controls", [])
    validate_gold([*labels, *controls], lines)
    gold_by_id = {entry["id"]: entry for entry in labels}
    control_ids = {entry["id"] for entry in controls}
    findings = {f"D-{i:03d}": f for i, f in enumerate(report_data["findings"], 1)}
    reviewed = decisions["findings"]
    if not isinstance(reviewed, dict) or set(reviewed) != set(findings):
        raise ValueError("Independent review must assess every finding exactly once")

    roots = {}
    duplicate_roots = []
    matches = {}
    controls_hit = set()
    counts = {status: 0 for status in STATUSES}
    severity_total = severity_correct = 0
    for fid, row in reviewed.items():
        status, root = row.get("status"), row.get("root_id")
        if status not in STATUSES or not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
            raise ValueError("Each finding needs an identified decision and rationale")
        counts[status] += 1
        if status in {"gold", "valid_extra", "duplicate"}:
            evidence = findings[fid]["evidence"]
            if not evidence or not all(valid_evidence(item, lines) for item in evidence):
                raise ValueError("A supported finding must contain valid source evidence")
        if status in {"gold", "valid_extra"}:
            if not isinstance(root, str) or not root or root in roots:
                raise ValueError("Use a unique supported root; repeated roots must be marked duplicate")
            if status == "gold":
                if root not in gold_by_id:
                    raise ValueError("Unknown gold root")
                matches[root] = fid
                expected = gold_by_id[root].get("expected_severity")
                if expected:
                    severity_total += 1
                    severity_correct += findings[fid].get("severity") == expected
            elif not root.startswith("EXTRA-") or root in gold_by_id or root in control_ids:
                raise ValueError("A valid additional defect needs a separate EXTRA root")
            roots[root] = fid
        elif status == "duplicate":
            if not isinstance(root, str) or not root:
                raise ValueError("Duplicate must identify its supported root")
            duplicate_roots.append(root)
        elif root is not None:
            raise ValueError("Unsupported and uncertain findings cannot declare a supported root")
        hit = row.get("negative_control_ids", [])
        if not isinstance(hit, list) or len(set(hit)) != len(hit) or not set(hit) <= control_ids:
            raise ValueError("Unknown or repeated negative control")
        if hit and status != "false_positive":
            raise ValueError("Only an unsupported finding can hit a negative control")
        controls_hit.update(hit)
    if any(root not in roots for root in duplicate_roots):
        raise ValueError("A duplicate must refer to an adjudicated supported root")

    return {
        "case": source.name,
        **digests,
        "model": report_data["passport"].get("model"),
        "findings": len(findings),
        "supported_findings": counts["gold"] + counts["valid_extra"],
        "matched_gold": len(matches), "gold_count": len(labels),
        "semantic_precision": ratio(counts["gold"] + counts["valid_extra"], len(findings)),
        "gold_recall": ratio(len(matches), len(labels)),
        "duplicate_count": counts["duplicate"], "false_positive_count": counts["false_positive"],
        "uncertain_count": counts["uncertain"], "valid_extra_count": counts["valid_extra"],
        "negative_control_count": len(control_ids), "negative_control_hit_count": len(controls_hit),
        "negative_control_hits": sorted(controls_hit),
        "severity_correct": severity_correct, "severity_total": severity_total,
        "severity_accuracy": ratio(severity_correct, severity_total),
        "matches": matches, "missed_gold_ids": sorted(set(gold_by_id) - matches.keys()),
    }


def aggregate_cases(cases: list[dict], target: float = .95) -> dict:
    if not cases:
        raise ValueError("At least one independently adjudicated case is required")
    if not 0 < target <= 1:
        raise ValueError("Target must be between 0 and 1")
    if len({c["source_sha256"] for c in cases}) != len(cases):
        raise ValueError("Do not count the same source twice in one holdout aggregate")
    keys = ("findings", "supported_findings", "matched_gold", "gold_count", "duplicate_count",
            "false_positive_count", "uncertain_count", "valid_extra_count", "negative_control_count",
            "negative_control_hit_count", "severity_correct", "severity_total")
    total = {k: sum(c[k] for c in cases) for k in keys}
    precision = ratio(total["supported_findings"], total["findings"])
    recall = ratio(total["matched_gold"], total["gold_count"])
    return {
        "documents": len(cases), **total,
        "semantic_precision": precision, "gold_recall": recall,
        "severity_accuracy": ratio(total["severity_correct"], total["severity_total"]),
        "negative_control_hit_rate": ratio(total["negative_control_hit_count"], total["negative_control_count"]),
        "target": target,
        "target_met": precision is not None and recall is not None and precision >= target and recall >= target,
        "cases": cases,
        "method": "Independent semantic adjudication of frozen reports. Unique supported roots / all findings; "
                  "matched predeclared gold roots / all predeclared gold roots. Duplicates, false positives and uncertain findings "
                  "are counted against precision. Extra valid roots do not change recall. Synthetic holdout and reviewer limitations remain.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", nargs=4, type=Path, action="append", required=True,
                        metavar=("SOURCE", "GOLD", "REPORT", "REVIEW"))
    parser.add_argument("--target", type=float, default=.95)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = aggregate_cases([evaluate_semantic_case(*case) for case in args.case], args.target)
    except (ValueError, TypeError, KeyError, OSError) as error:
        parser.error(str(error))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

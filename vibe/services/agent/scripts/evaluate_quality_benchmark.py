"""Audit a hand-annotated text benchmark against saved reports (no model calls)."""

import argparse
import json
import math
from hashlib import sha256
from pathlib import Path

# Fixed evaluation rubric, independent of the model's output.
INDIVIDUAL_CRITERIA = {
    "Необходимость", "Однозначность", "Полнота", "Атомарность", "Реализуемость",
    "Проверяемость", "Корректность", "Независимость от реализации", "Прослеживаемость",
}
SET_CRITERIA = {
    "Полнота набора", "Согласованность", "Связность", "Достижимость",
    "Проверяемость набора", "Отсутствие дублирования",
}
VALID_STATUSES = {"pass", "fail", "uncertain", "not_applicable"}


def normalize(text: str) -> str:
    return " ".join(text.split())


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def covers(evidence: list[dict], label: dict) -> bool:
    return all(
        any(item["start"] <= line <= item["end"] for item in evidence)
        for line in label.get("lines", [])
    ) and all(
        any(
            item["start"] <= anchor["line"] <= item["end"]
            and normalize(anchor["quote"]) in normalize(item.get("quote", ""))
            for item in evidence
        )
        for anchor in label.get("anchors", [])
    )


def match_one_to_one(candidates: list[list[int]]) -> dict[int, int]:
    """Maximum bipartite matching: reported item index -> gold item index."""
    matched: dict[int, int] = {}

    def match(gold_index: int, seen: set[int]) -> bool:
        for candidate in candidates[gold_index]:
            if candidate in seen:
                continue
            seen.add(candidate)
            previous = matched.get(candidate)
            if previous is None or match(previous, seen):
                matched[candidate] = gold_index
                return True
        return False

    for index in range(len(candidates)):
        match(index, set())
    return matched


def score_findings(
    gold: list[dict], findings: list[dict], *, excluded_indices: set[int] | None = None,
) -> dict:
    """Match each annotation and each finding at most once by kind and evidence."""
    matched_findings = match_one_to_one([
        [
            i for i, finding in enumerate(findings)
            if i not in (excluded_indices or ())
            and finding["kind"] in defect["kinds"]
            and covers(finding["evidence"], defect)
        ]
        for defect in gold
    ])
    matched_gold = set(matched_findings.values())
    count = len(matched_gold)
    return {
        "annotated_defects": len(gold),
        "report_findings": len(findings),
        "matched": count,
        "annotation_recall": ratio(count, len(gold)),
        "annotation_precision": ratio(count, len(findings)),
        "matches": [
            {"gold_id": gold[gold_index]["id"], "finding_id": f"D-{index + 1:03d}"}
            for index, gold_index in sorted(matched_findings.items())
        ],
        "missed_gold_ids": [defect["id"] for i, defect in enumerate(gold) if i not in matched_gold],
        "unmatched_finding_count": len(findings) - count,
        "unmatched_finding_ids": [
            f"D-{i + 1:03d}" for i in range(len(findings)) if i not in matched_findings
        ],
        "method": "One-to-one matching by allowed kind, cited line coverage and exact "
                  "quote anchors (when annotated). This is an annotation-coverage proxy, "
                  "not semantic accuracy; unmatched findings require expert review.",
    }


def score_requirements(
    gold: list[dict], requirements: list[dict], *, excluded_indices: set[int] | None = None,
) -> dict:
    matched_requirements = match_one_to_one([
        [
            i for i, requirement in enumerate(requirements)
            if i not in (excluded_indices or ())
            and ("source_id" not in label or requirement["source_id"] == label["source_id"])
            and covers([requirement["evidence"]], label)
        ]
        for label in gold
    ])
    matched_gold = set(matched_requirements.values())
    count = len(matched_gold)
    return {
        "annotated_requirements": len(gold),
        "report_requirements": len(requirements),
        "matched": count,
        "annotation_recall": ratio(count, len(gold)),
        "annotation_precision": ratio(count, len(requirements)),
        "matches": [
            {"gold_id": gold[gold_index]["id"], "requirement_id": requirements[i]["id"]}
            for i, gold_index in sorted(matched_requirements.items())
        ],
        "missed_gold_ids": [label["id"] for i, label in enumerate(gold) if i not in matched_gold],
        "unmatched_requirement_ids": [
            req["id"] for i, req in enumerate(requirements) if i not in matched_requirements
        ],
        "method": "One-to-one matching of annotated atomic obligations by source ID "
                  "(when annotated) and evidence anchors. Does not validate paraphrase semantics.",
    }


def checked_criteria(checks: list[dict], expected: set[str]) -> set[str]:
    return {
        check["criterion"] for check in checks
        if check["criterion"] in expected and check["status"] in VALID_STATUSES
    }


def score_review_coverage(
    gold: list[dict] | None, requirements: list[dict], set_checks: list[dict],
    extraction: dict | None,
) -> dict:
    by_id = {
        req["id"]: len(checked_criteria(req.get("checks", []), INDIVIDUAL_CRITERIA))
        for req in requirements
    }
    if len(by_id) != len(requirements):
        raise ValueError("Duplicate report requirement ID")

    def coverage(reviewed: int, expected: int) -> dict:
        return {"reviewed": reviewed, "expected": expected, "fraction": ratio(reviewed, expected)}

    annotated = None
    if extraction is not None and gold is not None:
        annotated = coverage(
            sum(by_id[item["requirement_id"]] for item in extraction["matches"]),
            len(gold) * len(INDIVIDUAL_CRITERIA),
        )
    return {
        "annotated_individual": annotated,
        "report_individual": coverage(sum(by_id.values()), len(requirements) * len(INDIVIDUAL_CRITERIA)),
        "set": coverage(len(checked_criteria(set_checks, SET_CRITERIA)), len(SET_CRITERIA)),
        "method": "Count distinct expected criteria with a recognized status, not necessarily passed. "
                  "Missed annotated obligations count against annotated individual coverage.",
    }


def valid_evidence(evidence: dict, lines: list[str]) -> bool:
    start, end, quote = evidence.get("start"), evidence.get("end"), evidence.get("quote")
    return (
        type(start) is int and type(end) is int and isinstance(quote, str)
        and 1 <= start <= end <= len(lines) and bool(normalize(quote))
        and normalize(quote) in normalize(" ".join(lines[start - 1:end]))
    )


def validate_gold(labels: list[dict], lines: list[str]) -> None:
    ids = set()
    for label in labels:
        if label["id"] in ids:
            raise ValueError("Duplicate gold ID")
        ids.add(label["id"])
        if not (label.get("lines") or label.get("anchors")):
            raise ValueError("Gold label requires a source anchor or line")
        if any(type(line) is not int or not 1 <= line <= len(lines) for line in label.get("lines", [])):
            raise ValueError("Invalid gold line")
        if any(not valid_evidence({"start": anchor["line"], "end": anchor["line"],
                                   "quote": anchor["quote"]}, lines)
               for anchor in label.get("anchors", [])):
            raise ValueError("Invalid gold anchor: quote must occur on its annotated line")


def delta(after: float | None, before: float | None) -> float | None:
    return None if after is None or before is None else after - before


def score_measurements(manifest: dict, source_hash: str, report_hash: str) -> dict:
    """Do not infer missing timing or cost from the report or its timestamp."""
    if manifest.get("source_sha256") != source_hash or manifest.get("report_sha256") != report_hash:
        raise ValueError("Measurement manifest does not identify this source and report")
    result = {"cost_currency": manifest.get("cost_currency"), "notes": manifest.get("notes")}
    fields = ("wall_seconds", "analyst_active_seconds", "direct_cost")
    for mode in ("manual", "assisted"):
        values = {}
        for field in (*fields, "generation_seconds") if mode == "assisted" else fields:
            value = manifest.get(mode, {}).get(field)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"Invalid {mode}.{field}: expected a finite nonnegative number or null")
            values[field] = value
        if values["wall_seconds"] is not None and values["analyst_active_seconds"] is not None:
            if values["analyst_active_seconds"] > values["wall_seconds"]:
                raise ValueError(f"{mode}.analyst_active_seconds exceeds wall_seconds for one analyst")
        if mode == "assisted" and values["wall_seconds"] is not None and values["generation_seconds"] is not None:
            if values["generation_seconds"] > values["wall_seconds"]:
                raise ValueError("assisted.generation_seconds exceeds wall_seconds")
        if values["direct_cost"] is not None and not (
            isinstance(result["cost_currency"], str) and result["cost_currency"].strip()
        ):
            raise ValueError("Measured direct costs require a currency")
        result[mode] = values
    manual, assisted = result["manual"], result["assisted"]
    result.update({
        "wall_seconds_saved": delta(manual["wall_seconds"], assisted["wall_seconds"]),
        "analyst_seconds_saved": delta(manual["analyst_active_seconds"], assisted["analyst_active_seconds"]),
        "direct_cost_saved": delta(manual["direct_cost"], assisted["direct_cost"]),
        "wall_speedup": (
            manual["wall_seconds"] / assisted["wall_seconds"]
            if manual["wall_seconds"] is not None and assisted["wall_seconds"] else None
        ),
        "method": "User-supplied observations for the same review task; assisted time includes "
                  "human verification and correction. Direct costs exclude analyst labor. "
                  "Null means not measured or not computable.",
    })
    return result


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_case(gold_path: Path, report_path: Path, measurements_path: Path | None = None) -> dict:
    gold = read_json(gold_path)
    source = gold_path.parent / gold["source"]
    if source.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("This line-annotation benchmark accepts only Markdown and text sources")
    source_bytes = source.read_bytes()
    lines = source_bytes.decode("utf-8-sig").splitlines()
    actual_hash = sha256(source_bytes).hexdigest()
    report_bytes = report_path.read_bytes()
    report_hash = sha256(report_bytes).hexdigest()
    report = json.loads(report_bytes)
    if report["passport"].get("document_sha256") != actual_hash:
        raise ValueError("Report does not identify the annotated source by SHA-256")
    validate_gold(gold["defects"], lines)
    requirements = report.get("requirements", [])
    findings = report["findings"]
    invalid_findings = {
        i for i, finding in enumerate(findings)
        if not all(valid_evidence(ev, lines) for ev in finding["evidence"])
    }
    unanchored_findings = {i for i, finding in enumerate(findings) if not finding["evidence"]}
    invalid_requirements = {
        i for i, req in enumerate(requirements)
        if not valid_evidence(req["evidence"], lines)
        or (req.get("source_id") is not None and not valid_evidence({
            **req["evidence"], "quote": req["source_id"],
        }, lines))
    }
    labels = gold.get("requirements")
    extraction = None
    if labels is not None:
        validate_gold(labels, lines)
        extraction = score_requirements(labels, requirements, excluded_indices=invalid_requirements)
    return {
        "case": source.name,
        "comparison_group": gold.get("comparison_group"),
        "annotation_scope": {
            "defects": gold.get("defect_annotation_scope", "non-exhaustive"),
            "requirements": gold.get("requirement_annotation_scope", "non-exhaustive") if labels is not None else None,
        },
        "source_sha256": actual_hash,
        "report_sha256": report_hash,
        "model": report["passport"]["model"],
        "run_id": report["passport"].get("run_id"),
        "gold_defect_ids": [label["id"] for label in gold["defects"]],
        **score_findings(gold["defects"], findings, excluded_indices=invalid_findings | unanchored_findings),
        "extraction": extraction,
        "coverage": score_review_coverage(labels, requirements, report.get("set_checks", []), extraction),
        "evidence_validity": {
            "requirements_with_valid_citations": len(requirements) - len(invalid_requirements),
            "findings_with_valid_citations": len(findings) - len(invalid_findings | unanchored_findings),
            "requirements_with_invalid_citations": [requirements[i]["id"] for i in sorted(invalid_requirements)],
            "findings_with_invalid_citations": [f"D-{i + 1:03d}" for i in sorted(invalid_findings)],
            "findings_without_citations": [f"D-{i + 1:03d}" for i in sorted(unanchored_findings)],
        },
        "measurements": score_measurements(read_json(measurements_path), actual_hash, report_hash)
        if measurements_path else None,
        "limitation": "Evidence anchors locate an issue or obligation; they cannot prove the report's "
                      "interpretation, severity, recommendation or absence of other defects. "
                      "Expert adjudication on unseen labeled sources is required to establish accuracy.",
    }


def compare_results(before: dict, after: dict) -> dict:
    if before["source_sha256"] != after["source_sha256"] and not (
        before.get("comparison_group") and before["comparison_group"] == after.get("comparison_group")
    ):
        raise ValueError("Different sources require the same declared comparison_group")
    old_labels, new_labels = set(before["gold_defect_ids"]), set(after["gold_defect_ids"])
    new_matches = {item["gold_id"] for item in after["matches"]}

    def metric(record: dict, *keys: str) -> float | None:
        for key in keys:
            if record is None:
                return None
            record = record.get(key)
        return record

    def metric_delta(*keys: str) -> float | None:
        return delta(metric(after, *keys), metric(before, *keys))

    before_currency = (before.get("measurements") or {}).get("cost_currency")
    after_currency = (after.get("measurements") or {}).get("cost_currency")
    return {
        "before_case": before["case"],
        "after_case": after["case"],
        "finding_count_delta": after["report_findings"] - before["report_findings"],
        "matched_defect_count_delta": after["matched"] - before["matched"],
        "annotation_recall_delta": delta(after["annotation_recall"], before["annotation_recall"]),
        "annotation_precision_delta": delta(after["annotation_precision"], before["annotation_precision"]),
        "requirement_count_delta": metric_delta("extraction", "report_requirements"),
        "extraction_recall_delta": metric_delta("extraction", "annotation_recall"),
        "annotated_review_coverage_delta": metric_delta("coverage", "annotated_individual", "fraction"),
        "assisted_wall_seconds_delta": metric_delta("measurements", "assisted", "wall_seconds"),
        "assisted_generation_seconds_delta": metric_delta("measurements", "assisted", "generation_seconds"),
        "assisted_analyst_seconds_delta": metric_delta("measurements", "assisted", "analyst_active_seconds"),
        "assisted_direct_cost_delta": metric_delta("measurements", "assisted", "direct_cost")
        if before_currency and before_currency == after_currency else None,
        "removed_gold_defect_ids": sorted(old_labels - new_labels),
        "added_gold_defect_ids": sorted(new_labels - old_labels),
        "shared_gold_missed_after": sorted((old_labels & new_labels) - new_matches),
        "method": "After minus before. Gold label changes represent annotated edits to the source, "
                  "not fixes verified by the model. Fewer findings alone do not prove an improvement.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--measurements", type=Path)
    parser.add_argument("--before-gold", type=Path)
    parser.add_argument("--before-report", type=Path)
    parser.add_argument("--before-measurements", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.before_gold) != bool(args.before_report) or (args.before_measurements and not args.before_gold):
        parser.error("--before-gold and --before-report are required together")
    try:
        result = evaluate_case(args.gold, args.report, args.measurements)
        if args.before_gold:
            before = evaluate_case(args.before_gold, args.before_report, args.before_measurements)
            result = {"before": before, "after": result, "comparison": compare_results(before, result)}
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.error(str(error))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

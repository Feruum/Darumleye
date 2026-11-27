import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_semantic_holdout import evaluate_semantic_case, aggregate_cases


def fixture(tmp_path):
    source = tmp_path / "example.md"
    source.write_text("R-1. Any ready request is accepted.\nR-2. Any ready request is declined.\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    gold = tmp_path / "example.gold.json"
    gold.write_text(json.dumps({"source": f"sources/{source.name}", "defects": [{"id": "G1", "kinds": ["conflict"],
        "anchors": [{"line": 1, "quote": "accepted"}, {"line": 2, "quote": "declined"}],
        "expected_severity": "critical"}], "negative_controls": []}))
    report = tmp_path / "report.json"
    evidence = [{"start": 1, "end": 1, "quote": "Any ready request is accepted."},
                {"start": 2, "end": 2, "quote": "Any ready request is declined."}]
    report.write_text(json.dumps({"passport": {"document_sha256": digest},
        "findings": [{"severity": "critical", "evidence": evidence, "issue": "Conflict"},
                     {"severity": "high", "evidence": evidence, "issue": "Restatement"},
                     {"severity": "medium", "evidence": evidence[:1], "issue": "Speculation"},
                     {"severity": "medium", "evidence": evidence[:1], "issue": "Additional valid issue"}]}))
    review = tmp_path / "adjudication.json"
    review.write_text(json.dumps({
        "source_sha256": digest,
        "gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
        "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "findings": {
            "D-001": {"status": "gold", "root_id": "G1", "rationale": "Matches independent defect."},
            "D-002": {"status": "duplicate", "root_id": "G1", "rationale": "Same repair."},
            "D-003": {"status": "uncertain", "rationale": "Not established."},
            "D-004": {"status": "valid_extra", "root_id": "EXTRA-1", "rationale": "Separate valid issue."},
        }}))
    return source, gold, report, review


def test_semantic_metrics_penalize_duplicates_and_uncertain_without_counting_extra_as_gold(tmp_path):
    result = evaluate_semantic_case(*fixture(tmp_path))
    assert result["gold_recall"] == 1
    assert result["semantic_precision"] == .5
    assert result["duplicate_count"] == 1
    assert result["uncertain_count"] == 1
    assert result["supported_findings"] == 2
    assert result["severity_accuracy"] == 1
    summary = aggregate_cases([result])
    assert summary["semantic_precision"] == .5
    assert summary["gold_recall"] == 1
    assert summary["target_met"] is False


def test_missing_decision_or_wrong_fingerprint_fails_closed(tmp_path):
    source, gold, report, review = fixture(tmp_path)
    data = json.loads(review.read_text())
    data["findings"].pop("D-004")
    review.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="every finding"):
        evaluate_semantic_case(source, gold, report, review)
    data["report_sha256"] = "wrong"
    review.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_semantic_case(source, gold, report, review)


def test_invalid_evidence_cannot_be_promoted_to_true_positive(tmp_path):
    source, gold, report, review = fixture(tmp_path)
    data = json.loads(report.read_text())
    data["findings"][0]["evidence"][0]["quote"] = "invented"
    report.write_text(json.dumps(data))
    decisions = json.loads(review.read_text())
    decisions["report_sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
    review.write_text(json.dumps(decisions))
    with pytest.raises(ValueError, match="evidence"):
        evaluate_semantic_case(source, gold, report, review)

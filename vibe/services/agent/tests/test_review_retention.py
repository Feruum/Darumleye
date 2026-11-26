import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_review_retention import audit_report


def make_report(tmp_path):
    evidence = {"start": 1, "end": 1, "quote": "One source requirement."}
    findings = [{"issue": "Missing limit", "evidence": [evidence]},
                {"issue": "Duplicate limit issue", "evidence": [evidence]}]
    observations = [{"id": f"O-{i:04d}", "kind": "observation", "role": "logic_inspector",
                     "severity": "high", "title": f"Observation {i}"} for i in range(1, 5)]
    nodes = [*observations, {"id": "E-0001", "kind": "evidence", "evidence": evidence},
             {"id": "D-001", "kind": "finding"}, {"id": "D-002", "kind": "finding"},
             {"id": "O-dissent-0001", "kind": "observation", "role": "gap_hunter"}]
    edges = [{"source": observation["id"], "target": "E-0001", "kind": "cites"} for observation in observations]
    edges.extend([{"source": "O-0001", "target": "D-001", "kind": "contributes_to"},
                  {"source": "O-0002", "target": "D-002", "kind": "contributes_to"},
                  {"source": "O-0003", "target": "D-001", "kind": "contributes_to"}])
    report = {"passport": {"run_id": "fixture", "review_mode": "multi_agent"}, "findings": findings,
              "evidence_graph": {"nodes": nodes, "edges": edges}}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_graph_retention_is_not_claimed_as_semantic_quality(tmp_path):
    path, _ = make_report(tmp_path)
    result = audit_report(path)
    assert result["graph"]["observations"] == 4
    assert result["graph"]["linked_with_original_evidence"] == 3
    assert result["graph"]["unlinked_observation_ids"] == ["O-0004"]
    assert result["semantic"] is None


def test_independent_labels_measure_unique_defects_and_mismerged_observations(tmp_path):
    path, digest = make_report(tmp_path)
    labels = {"report_sha256": digest,
              "observations": {"O-0001": "G-A", "O-0002": "G-A", "O-0003": "G-B", "O-0004": "G-C"},
              "findings": {"D-001": "G-A", "D-002": "G-A"}}
    result = audit_report(path, labels)
    assert result["semantic"]["unique_observation_defects"] == 3
    assert result["semantic"]["root_retention"] == pytest.approx(1 / 3)
    assert result["semantic"]["lost_defect_ids"] == ["G-B", "G-C"]
    assert result["semantic"]["mismerged_observation_ids"] == ["O-0003"]
    assert result["semantic"]["duplicate_finding_ids"] == ["D-002"]


def test_incomplete_or_wrong_report_labels_cannot_claim_semantic_retention(tmp_path):
    path, digest = make_report(tmp_path)
    incomplete = {"report_sha256": digest, "observations": {"O-0001": "G-A"}, "findings": {}}
    result = audit_report(path, incomplete)
    assert result["semantic"] is None
    assert result["pending"]["observations"] == ["O-0002", "O-0003", "O-0004"]
    with pytest.raises(ValueError, match="report"):
        audit_report(path, {**incomplete, "report_sha256": "wrong"})


def test_legacy_report_does_not_claim_graph_retention(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"passport": {"review_mode": "legacy"}, "findings": []}))
    result = audit_report(path)
    assert result["graph"] is None
    assert result["semantic"] is None


def test_extended_benchmark_annotations_point_to_the_original_sources():
    from scripts.evaluate_quality_benchmark import validate_gold

    directory = Path(__file__).resolve().parents[3] / "docs/examples/quality-benchmark/extended"
    cases = sorted(directory.glob("*.gold.json"))
    assert len(cases) == 2
    for case in cases:
        gold = json.loads(case.read_text())
        lines = (directory / gold["source"]).read_text().splitlines()
        for field in ("defects", "requirements", "negative_controls"):
            validate_gold(gold[field], lines)
        assert any(row["expected_severity"] == "critical" for row in gold["defects"])

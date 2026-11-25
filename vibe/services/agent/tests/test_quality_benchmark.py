from scripts.evaluate_quality_benchmark import score_findings


def test_gold_matches_each_annotated_defect_at_most_once():
    expected = [
        {"id": "G-01", "kinds": ["ambiguous"], "lines": [3]},
        {"id": "G-02", "kinds": ["conflict"], "lines": [5, 6]},
    ]
    findings = [
        {"kind": "ambiguous", "evidence": [{"start": 3, "end": 3}]},
        {"kind": "ambiguous", "evidence": [{"start": 3, "end": 3}]},
        {"kind": "conflict", "evidence": [{"start": 5, "end": 5}]},
    ]
    result = score_findings(expected, findings)
    assert result["matched"] == 1
    assert result["missed_gold_ids"] == ["G-02"]
    assert result["unmatched_finding_count"] == 2
    assert result["annotation_recall"] == 0.5


def test_conflict_needs_both_locations_and_can_match_after_single_anchor():
    result = score_findings(
        [{"id": "G-01", "kinds": ["conflict"], "lines": [3, 5]}],
        [{"kind": "conflict", "evidence": [{"start": 3, "end": 5}]}],
    )
    assert result["matched"] == 1


def test_empty_gold_set_does_not_claim_perfect_recall():
    result = score_findings([], [{"kind": "missing", "evidence": []}])
    assert result["annotation_recall"] is None
    assert result["unmatched_finding_count"] == 1


def test_same_line_different_excerpt_cannot_match_an_anchored_defect():
    gold = [{"id": "G-01", "kinds": ["ambiguous"], "lines": [3],
             "anchors": [{"line": 3, "quote": "своевременно"}]}]
    findings = [{"kind": "ambiguous", "evidence": [{"start": 3, "end": 3, "quote": "Система"}]}]
    assert score_findings(gold, findings)["matched"] == 0


def test_atomic_extraction_preserves_same_id_but_separate_obligations():
    from scripts.evaluate_quality_benchmark import score_requirements

    labels = [
        {"id": "E-01", "source_id": "REQ-01", "anchors": [{"line": 2, "quote": "показать имя"}]},
        {"id": "E-02", "source_id": "REQ-01", "anchors": [{"line": 2, "quote": "показать статус"}]},
    ]
    report = [
        {"id": "R-001", "source_id": "REQ-01", "evidence": {"start": 2, "end": 2, "quote": "REQ-01: показать имя и показать статус"}},
        {"id": "R-002", "source_id": "WRONG-ID", "evidence": {"start": 2, "end": 2, "quote": "показать статус"}},
    ]
    scored = score_requirements(labels, report)
    assert scored["matched"] == 1
    assert scored["annotation_recall"] == 0.5
    assert scored["unmatched_requirement_ids"] == ["R-002"]


def test_missed_requirement_counts_against_gold_review_coverage():
    from scripts.evaluate_quality_benchmark import score_review_coverage

    labels = [{"id": "E-01"}, {"id": "E-02"}]
    requirements = [{"id": "R-001", "checks": [
        {"criterion": "Необходимость", "status": "pass"},
        {"criterion": "Необходимость", "status": "pass"},
        {"criterion": "Полнота", "status": "invalid"},
    ]}]
    extraction = {"matches": [{"gold_id": "E-01", "requirement_id": "R-001"}]}
    result = score_review_coverage(labels, requirements, [], extraction)
    assert result["annotated_individual"] == {"reviewed": 1, "expected": 18, "fraction": 1 / 18}
    assert result["report_individual"] == {"reviewed": 1, "expected": 9, "fraction": 1 / 9}
    assert result["set"] == {"reviewed": 0, "expected": 6, "fraction": 0.0}


def test_measurements_are_bound_to_report_and_compute_only_supplied_deltas():
    import pytest
    from scripts.evaluate_quality_benchmark import score_measurements

    manifest = {
        "source_sha256": "source", "report_sha256": "report", "cost_currency": "KZT",
        "manual": {"wall_seconds": 500, "analyst_active_seconds": 400},
        "assisted": {"wall_seconds": 100, "analyst_active_seconds": 30, "direct_cost": 5},
    }
    result = score_measurements(manifest, "source", "report")
    assert result["wall_seconds_saved"] == 400
    assert result["analyst_seconds_saved"] == 370
    assert result["direct_cost_saved"] is None
    assert result["wall_speedup"] == 5
    assert result["assisted"]["direct_cost"] == 5
    with pytest.raises(ValueError, match="report"):
        score_measurements(manifest, "source", "other")
    manifest["manual"]["wall_seconds"] = -1
    with pytest.raises(ValueError, match="wall_seconds"):
        score_measurements(manifest, "source", "report")


def test_compare_reports_separates_removed_gold_from_report_observations():
    import pytest
    from scripts.evaluate_quality_benchmark import compare_results

    before = {
        "case": "before.md", "source_sha256": "one", "comparison_group": "example",
        "gold_defect_ids": ["G-01", "G-02"], "report_findings": 3, "matched": 2,
        "annotation_recall": 1.0, "annotation_precision": 2 / 3,
        "matches": [{"gold_id": "G-01"}, {"gold_id": "G-02"}],
    }
    after = {
        "case": "after.md", "source_sha256": "two", "comparison_group": "example",
        "gold_defect_ids": ["G-02", "G-03"], "report_findings": 1, "matched": 1,
        "annotation_recall": 0.5, "annotation_precision": 1.0,
        "matches": [{"gold_id": "G-03"}],
    }
    result = compare_results(before, after)
    assert result["finding_count_delta"] == -2
    assert result["removed_gold_defect_ids"] == ["G-01"]
    assert result["added_gold_defect_ids"] == ["G-03"]
    assert result["shared_gold_missed_after"] == ["G-02"]
    with pytest.raises(ValueError, match="comparison"):
        compare_results(before, {**after, "comparison_group": "different"})


def test_case_rejects_forged_gold_anchor_and_invalid_report_citation(tmp_path):
    import json
    from hashlib import sha256

    import pytest
    from scripts.evaluate_quality_benchmark import evaluate_case

    source = tmp_path / "case.md"
    source.write_text("# ТЗ\nСервис должен быть удобным.\n", encoding="utf-8")
    gold_path = tmp_path / "gold.json"
    label = {"id": "G-01", "kinds": ["ambiguous"], "lines": [2], "anchors": [{"line": 2, "quote": "удобным"}]}
    gold = {"source": source.name, "defects": [label], "requirements": []}
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    report_path = tmp_path / "report.json"
    report = {
        "passport": {"document_sha256": sha256(source.read_bytes()).hexdigest(), "model": "test"},
        "findings": [{"kind": "ambiguous", "evidence": [{"start": 1, "end": 2, "quote": "неудобным"}]}],
        "requirements": [], "set_checks": [],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    score = evaluate_case(gold_path, report_path)
    assert score["matched"] == 0
    assert score["evidence_validity"]["findings_with_invalid_citations"] == ["D-001"]
    label["anchors"][0]["quote"] = "выдуманное"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    with pytest.raises(ValueError, match="anchor"):
        evaluate_case(gold_path, report_path)


def test_all_synthetic_gold_cases_have_exact_source_anchors():
    import json
    from pathlib import Path

    from scripts.evaluate_quality_benchmark import validate_gold

    examples = Path(__file__).parents[3] / "docs" / "examples" / "quality-benchmark"
    paths = sorted(examples.glob("*.gold.json"))
    assert len(paths) >= 4
    for path in paths:
        gold = json.loads(path.read_text(encoding="utf-8"))
        lines = (path.parent / gold["source"]).read_text(encoding="utf-8").splitlines()
        validate_gold(gold["defects"], lines)
        validate_gold(gold["requirements"], lines)
        assert gold["requirements"]
        assert all(item["anchors"] for item in gold["defects"])
        assert all(item["anchors"] for item in gold["requirements"])


def test_fixed_benchmark_rubric_agrees_with_review_contract():
    from agent.services.spec_review import INDIVIDUAL_CRITERIA, SET_CRITERIA
    from scripts.evaluate_quality_benchmark import INDIVIDUAL_CRITERIA as benchmark_individual
    from scripts.evaluate_quality_benchmark import SET_CRITERIA as benchmark_set

    assert benchmark_individual == set(INDIVIDUAL_CRITERIA)
    assert benchmark_set == set(SET_CRITERIA)


def test_compare_reports_includes_extraction_and_review_coverage_deltas():
    from scripts.evaluate_quality_benchmark import compare_results

    def result(match, count):
        return {
            "case": "case.md", "source_sha256": "one",
            "gold_defect_ids": [], "report_findings": 0, "matched": 0,
            "annotation_recall": None, "annotation_precision": None, "matches": [],
            "extraction": {"annotation_recall": match, "report_requirements": count},
            "coverage": {"annotated_individual": {"fraction": match}},
        }

    scored = compare_results(result(0.5, 2), result(1.0, 4))
    assert scored["extraction_recall_delta"] == 0.5
    assert scored["requirement_count_delta"] == 2
    assert scored["annotated_review_coverage_delta"] == 0.5


def test_separate_generation_duration_is_not_assisted_review_duration():
    import pytest
    from scripts.evaluate_quality_benchmark import score_measurements

    manifest = {
        "source_sha256": "source", "report_sha256": "report",
        "assisted": {"generation_seconds": 30},
    }
    result = score_measurements(manifest, "source", "report")
    assert result["assisted"]["generation_seconds"] == 30
    assert result["assisted"]["wall_seconds"] is None
    assert result["wall_seconds_saved"] is None
    manifest["assisted"]["wall_seconds"] = 20
    with pytest.raises(ValueError, match="generation_seconds"):
        score_measurements(manifest, "source", "report")


def test_compare_costs_only_when_both_measurements_share_currency():
    from scripts.evaluate_quality_benchmark import compare_results

    common = {
        "case": "case.md", "source_sha256": "same",
        "gold_defect_ids": [], "report_findings": 0, "matched": 0,
        "annotation_recall": None, "annotation_precision": None, "matches": [],
    }
    before = {**common, "measurements": {"cost_currency": "KZT", "assisted": {"direct_cost": 12}}}
    after = {**common, "measurements": {"cost_currency": "KZT", "assisted": {"direct_cost": 10}}}
    assert compare_results(before, after)["assisted_direct_cost_delta"] == -2
    after["measurements"]["cost_currency"] = "USD"
    assert compare_results(before, after)["assisted_direct_cost_delta"] is None


def test_cli_compares_saved_reports_and_preserves_hashes(tmp_path):
    import json
    import subprocess
    import sys
    from hashlib import sha256
    from pathlib import Path

    source = tmp_path / "source.md"
    source.write_text("Требование.\n", encoding="utf-8")
    source_hash = sha256(source.read_bytes()).hexdigest()
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"source": source.name, "defects": []}), encoding="utf-8")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "passport": {"document_sha256": source_hash, "model": "test"},
        "findings": [],
    }), encoding="utf-8")
    report_hash = sha256(report.read_bytes()).hexdigest()
    measurement = tmp_path / "measurement.json"
    measurement.write_text(json.dumps({
        "source_sha256": source_hash, "report_sha256": report_hash,
        "manual": {"wall_seconds": 10},
        "assisted": {"wall_seconds": 5},
    }), encoding="utf-8")
    output = tmp_path / "comparison.json"
    script = Path(__file__).parents[1] / "scripts" / "evaluate_quality_benchmark.py"
    subprocess.run([
        sys.executable, str(script), "--gold", str(gold), "--report", str(report),
        "--before-gold", str(gold), "--before-report", str(report),
        "--measurements", str(measurement), "--output", str(output),
    ], check=True)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["before"]["measurements"] is None
    assert result["after"]["measurements"]["wall_speedup"] == 2
    assert result["after"]["report_sha256"] == report_hash
    assert result["comparison"]["annotation_recall_delta"] is None

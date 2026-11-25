"""Independent structural/evidence audit of a generated specification report.

The organizer publishes an input and rubric, not a labeled gold dataset. This
script evaluates reproducible schema, evidence, coverage and report checks. It
does not equate structural validity or Drex probabilities with semantic accuracy.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

from agent.services.spec_document import extract_specification, normalize_quote
from agent.services.spec_reference_review import ReferenceDocument, load_reference_files
from agent.services.spec_review import (
    INDIVIDUAL_CRITERIA,
    SET_CRITERIA,
    ReviewReport,
    READINESS_CRITERIA,
    checked_readiness,
    clarification_questions,
    link_failed_criteria,
    ordered_checks,
    summarize,
    uncovered_requirement_candidates,
    validate_findings,
)


def evaluate(source_path: Path, report_path: Path, *, official: bool = False,
             references: list[ReferenceDocument] | None = None) -> dict:
    specification = extract_specification(
        source_path.name, "application/octet-stream", source_path.read_bytes()
    )
    raw_report = json.loads(report_path.read_text(encoding="utf-8"))
    report = ReviewReport.model_validate(raw_report)
    results = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append({"criterion": name, "passed": bool(condition), "detail": detail})

    def checked(name, operation):
        try:
            operation()
        except (ValueError, TypeError) as error:
            check(name, False, type(error).__name__)
        else:
            check(name, True)

    ids = {item.id for item in report.requirements}
    check(
        "Exact source identity",
        report.passport.document_sha256 == specification.sha256
        and report.filename == source_path.name
        and report.source == specification.source,
    )
    check(
        "Registration passport",
        bool(
            report.passport.run_at
            and report.passport.model
            and report.passport.agent_version
            and report.passport.document_title == source_path.name
            and report.passport.run_id
        ),
    )
    if report.passport.run_id:
        checked("Run ID is a UUID", lambda: UUID(report.passport.run_id))
    check(
        "Unique sequential requirement IDs",
        [item.id for item in report.requirements]
        == [f"R-{i:03d}" for i in range(1, len(report.requirements) + 1)],
    )
    check(
        "Nonempty requirements register",
        bool(report.requirements),
        str(len(report.requirements)),
    )

    def check_register():
        for requirement in report.requirements:
            evidence = requirement.evidence
            specification.verify_quote(evidence.start, evidence.end, evidence.quote)
            if requirement.source_id is not None:
                specification.verify_quote(
                    evidence.start, evidence.end, requirement.source_id
                )

    checked(
        "Every extracted requirement and explicit ID traces to the original",
        check_register,
    )

    def check_individual():
        for item in report.requirements:
            ordered_checks(item.checks, INDIVIDUAL_CRITERIA)

    checked("All nine individual criteria for every requirement", check_individual)

    def check_set():
        ordered_checks(report.set_checks, SET_CRITERIA)
        for item in report.set_checks:
            for evidence in item.evidence:
                specification.verify_quote(evidence.start, evidence.end, evidence.quote)

    checked("All six set criteria and exact set citations", check_set)
    checked(
        "All finding IDs and exact evidence",
        lambda: validate_findings(specification, report.findings, ids, allow_set=True),
    )
    linked = {req for finding in report.findings for req in finding.requirement_ids}
    check(
        "Every failed individual check has a finding",
        all(
            item.id in linked or all(c.status != "fail" for c in item.checks)
            for item in report.requirements
        ),
    )
    check(
        "Every finding has a concrete source citation",
        all(finding.evidence for finding in report.findings),
        f"{sum(bool(f.evidence) for f in report.findings)}/{len(report.findings)}",
    )
    expected_statistics, expected_verdict = summarize(
        report.requirements,
        report.findings,
        report.set_checks,
        specification.unreadable_pages,
        specification.unreadable_units,
        report.extraction_gaps,
        report.readiness_checklist,
    )
    check(
        "Statistics derived from actual records",
        expected_statistics == report.statistics,
    )
    check("Verdict derived from validated records", expected_verdict == report.verdict)
    check(
        "Unreadable page register matches input",
        report.unreadable_pages == specification.unreadable_pages and report.unreadable_units == specification.unreadable_units,
    )
    sections = (
        "Паспорт прогона",
        "Сводка",
        "Выделенные требования",
        "Проверка каждого требования",
        "Проверка набора требований",
        "Выявленные нарушения",
        "Критические дефекты",
        "Итог",
    )
    check(
        "Required Russian report sections",
        all(f"## {section}" in report.report_markdown for section in sections),
    )
    check(
        "Actionable recommendations",
        all(bool(finding.recommendation.strip()) for finding in report.findings),
    )
    if official:
        check("Organizer document-only mode", report.reference_review is None)
        check("Original organizer input SHA-256", specification.sha256 == "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381")
    decision = report.decision_review
    if decision and decision.status == "completed":
        check(
            "Drex assessed every core finding",
            {item.id for item in decision.findings}
            == {f"D-{i:03d}" for i in range(1, len(report.findings) + 1)}
            and len(decision.findings) == len(report.findings),
        )
    multi_agent = report.passport.review_mode in {"multi_agent", "multi_agent_direct"}
    if multi_agent or "readiness_checklist" in raw_report:
        check("All readiness criteria exactly once and in order", [item.id for item in report.readiness_checklist] == list(READINESS_CRITERIA))
        def check_readiness():
            if checked_readiness(specification, report.readiness_checklist) != report.readiness_checklist:
                raise ValueError("Readiness rows do not match verified source status")
        checked("Readiness citations and confirmed status", check_readiness)
    if multi_agent or "clarification_questions" in raw_report:
        check("Questions link to verified findings or checklist rows", report.clarification_questions == clarification_questions(report.findings, report.readiness_checklist))
    if multi_agent or "extraction_gaps" in raw_report:
        deterministic_gaps = uncovered_requirement_candidates(specification, report.requirements)
        if multi_agent:
            check("Explicit extraction candidate audit", all(item in report.extraction_gaps for item in deterministic_gaps))
            def check_gap_evidence():
                for item in report.extraction_gaps:
                    specification.verify_quote(item.start, item.end, item.quote)
            checked("Independent gap candidates cite the original", check_gap_evidence)
        else:
            check("Explicit extraction candidate audit", report.extraction_gaps == deterministic_gaps)
        def check_criterion_links():
            for requirement in report.requirements:
                linked = [f.model_copy(update={"criteria": [c for c in f.criteria if c in INDIVIDUAL_CRITERIA]}, deep=True) for f in report.findings if any(c in INDIVIDUAL_CRITERIA for c in f.criteria)]
                link_failed_criteria(requirement.checks, linked, requirement_id=requirement.id)
        checked("Every failed individual criterion has a linked finding", check_criterion_links)
        def check_set_links():
            linked = [f.model_copy(update={"criteria": [c for c in f.criteria if c in SET_CRITERIA]}, deep=True) for f in report.findings if any(c in SET_CRITERIA for c in f.criteria)]
            link_failed_criteria(report.set_checks, linked)
        checked("Every failed set criterion has a linked finding", check_set_links)
    documents = {ref.source.id: ref.document for ref in references or []}
    reference = report.reference_review
    if reference or documents:
        expected = {source.id: source.sha256 for source in reference.sources} if reference else {}
        check("Selected reference originals match reported hashes",
              bool(references) and expected == {k: doc.sha256 for k, doc in documents.items()})
        def check_reference_evidence():
            for item in reference.observations if reference else []:
                doc = documents.get(item.source_id)
                if doc is None:
                    raise ValueError("Reference original unavailable")
                for source, anchors in ((doc, item.reference_evidence), (specification, item.specification_evidence)):
                    for anchor in anchors:
                        source.verify_quote(anchor.start, anchor.end, anchor.quote)
        checked("Reference findings cite both original documents", check_reference_evidence)
    graph = report.evidence_graph
    if multi_agent:
        check("Multi-agent graph exists", graph is not None)
        if graph is not None:
            complete = {item.role for item in graph.experts if item.status == "completed"}
            check("All five independent experts completed", {"logic_inspector", "scenario_builder", "gap_hunter", "contract_inspector", "risk_inspector"} <= complete)
            check("Graph covers the requirement register", {node.requirement_id for node in graph.nodes if node.kind == "requirement"} == ids)
            expected_finding_ids = {f"D-{index:03d}" for index in range(1, len(report.findings) + 1)}
            core_nodes = {node.id: node.finding_id for node in graph.nodes if node.kind == "finding" and node.finding_id in expected_finding_ids}
            check("Graph covers all primary findings", set(core_nodes.values()) == expected_finding_ids)
            nodes = {node.id: node for node in graph.nodes}
            targets = defaultdict(set)
            incoming = set()
            for edge in graph.edges:
                targets[edge.source, edge.kind].add(edge.target)
                if edge.kind == "contributes_to" and nodes[edge.source].kind == "observation" and nodes[edge.source].finding_id == edge.target:
                    incoming.add(edge.target)
            check("Every primary finding retains observation provenance", set(core_nodes) <= incoming)
            counts = Counter(node.role for node in graph.nodes if node.kind == "observation")
            check("Expert observation counts match graph provenance", counts == Counter({expert.role: expert.observations for expert in graph.experts}))

            def citation_key(source_id, anchor):
                return source_id, anchor.start, anchor.end, normalize_quote(anchor.quote)

            def linked_to(node_id, requirements, citations):
                actual = set()
                for target in targets[node_id, "cites"]:
                    node = nodes[target]
                    if node.kind != "evidence":
                        return False
                    actual.add(citation_key(node.source_id, node.evidence))
                return actual == set(citations) and targets[node_id, "about"] == set(requirements)

            check("Graph links each finding to its reported requirements and citations", all(
                linked_to(f"D-{i:03d}", f.requirement_ids, [citation_key(None, e) for e in f.evidence])
                for i, f in enumerate(report.findings, 1)
            ))
            if reference:
                check("Graph links normative findings to both sets of citations", all(
                    linked_to(item.id, item.requirement_ids, [citation_key(None, e) for e in item.specification_evidence] +
                              [citation_key(item.source_id, e) for e in item.reference_evidence])
                    and targets[item.id, "from_reference"] == {item.source_id}
                    for item in reference.observations
                ))
            def check_graph_evidence():
                for node in graph.nodes:
                    if node.kind == "evidence" and node.evidence is not None:
                        source = documents.get(node.source_id) if node.source_id else specification
                        if source is None:
                            raise ValueError("Reference original unavailable")
                        source.verify_quote(node.evidence.start, node.evidence.end, node.evidence.quote)
            checked("Graph source citations are exact", check_graph_evidence)
    metrics = {
        "requirements": len(report.requirements),
        "individual_checks": sum(len(item.checks) for item in report.requirements),
        "set_checks": len(report.set_checks),
        "findings": len(report.findings),
        "findings_with_citations": sum(bool(item.evidence) for item in report.findings),
        "critical": report.statistics.critical,
        "verdict": report.verdict.status,
        "decision_status": decision.status if decision else "disabled",
        "decision_findings": len(decision.findings) if decision else 0,
        "review_mode": report.passport.review_mode,
        "graph_nodes": len(graph.nodes) if graph else 0,
        "graph_edges": len(graph.edges) if graph else 0,
    }
    return {
        "source": source_path.name,
        "source_sha256": specification.sha256,
        "model": report.passport.model,
        "run_id": report.passport.run_id,
        "checks": results,
        "passed": sum(item["passed"] for item in results),
        "total": len(results),
        "metrics": metrics,
        "limitation": "Structural/evidence evaluation; no organizer-labeled expected findings were published. Semantic coverage must be independently reviewed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--reference-manifest", type=Path, help="Original manifest used for a review with external sources")
    args = parser.parse_args()
    for original in (args.input, args.report):
        if args.output.resolve() == original.resolve() or (args.output.exists() and args.output.samefile(original)):
            parser.error("The audit output must be a separate file from the source and generated report")
    references = load_reference_files(args.reference_manifest, args.output) if args.reference_manifest else None
    result = evaluate(args.input, args.report, official=args.official, references=references)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Structural checks: {result['passed']}/{result['total']}")
    print(json.dumps(result["metrics"], ensure_ascii=False))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

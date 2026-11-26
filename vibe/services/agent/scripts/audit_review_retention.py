"""Audit observation retention in a saved evidence graph without calling a model.

Semantic metrics require independently reviewed observations and findings bound
to the exact report digest. Graph links alone cannot establish semantic quality.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path


EXPERT_ROLES = frozenset({"logic_inspector", "scenario_builder", "gap_hunter", "contract_inspector", "risk_inspector"})
REJECTED = "false_positive"


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _anchor(evidence: dict) -> tuple:
    return (evidence["start"], evidence["end"], evidence["quote"])


def audit_report(report_path: Path, labels: dict | None = None) -> dict:
    raw = report_path.read_bytes()
    report = json.loads(raw)
    digest = sha256(raw).hexdigest()
    passport = report["passport"]
    result = {"report_sha256": digest, "run_id": passport.get("run_id"),
              "source_sha256": passport.get("document_sha256"), "mode": passport.get("review_mode"),
              "graph": None, "semantic": None,
              "limitation": "Only observations recorded in the saved graph can be audited. Earlier omissions are not measurable here."}
    if labels is not None and labels.get("report_sha256") != digest:
        raise ValueError("Labels belong to a different report")
    graph = report.get("evidence_graph")
    if not graph:
        if labels is not None:
            raise ValueError("Report has no evidence graph to assess")
        return result

    nodes = {node["id"]: node for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("Duplicate graph node ID")
    observations = {id_: node for id_, node in nodes.items()
                    if node["kind"] == "observation" and node.get("severity")
                    and node.get("role") in EXPERT_ROLES}
    findings = {f"D-{index:03d}": finding for index, finding in enumerate(report["findings"], 1)}
    observation_targets = defaultdict(set)
    observation_anchors = defaultdict(set)
    for edge in graph["edges"]:
        source, target = edge["source"], edge["target"]
        if source not in nodes or target not in nodes:
            raise ValueError("Graph has a dangling edge")
        if source not in observations:
            continue
        if edge["kind"] == "contributes_to" and target in findings and nodes[target]["kind"] == "finding":
            observation_targets[source].add(target)
        if edge["kind"] == "cites" and nodes[target]["kind"] == "evidence" and not nodes[target].get("source_id"):
            observation_anchors[source].add(_anchor(nodes[target]["evidence"]))

    finding_anchors = {id_: {_anchor(evidence) for evidence in finding["evidence"]}
                       for id_, finding in findings.items()}
    linked = {id_ for id_ in observations if observation_targets[id_]}
    retained = {id_ for id_ in linked if observation_anchors[id_] and any(
        observation_anchors[id_] <= finding_anchors[target] for target in observation_targets[id_])}
    result["graph"] = {
        "observations": len(observations), "linked_observations": len(linked),
        "linked_with_original_evidence": len(retained),
        "evidence_link_retention": _fraction(len(retained), len(observations)),
        "unlinked_observation_ids": sorted(set(observations) - linked),
        "linked_without_all_original_evidence_ids": sorted(linked - retained),
        "by_role": {role: {"observations": len(ids), "linked": len(ids & linked),
                           "linked_with_original_evidence": len(ids & retained)}
                    for role in sorted(EXPERT_ROLES)
                    if (ids := {id_ for id_, node in observations.items() if node["role"] == role})},
    }
    if labels is None:
        result["label_template"] = {"report_sha256": digest,
                                    "observations": dict.fromkeys(sorted(observations)),
                                    "findings": dict.fromkeys(sorted(findings))}
        return result

    observation_labels = labels.get("observations", {})
    finding_labels = labels.get("findings", {})
    for mapping, known in ((observation_labels, observations), (finding_labels, findings)):
        if not isinstance(mapping, dict) or set(mapping) - known.keys():
            raise ValueError("Labels must refer to known report items")
        if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in mapping.values()):
            raise ValueError("Each label must be a defect ID, false_positive, or null")
    pending = {"observations": sorted(id_ for id_ in observations if observation_labels.get(id_) is None),
               "findings": sorted(id_ for id_ in findings if finding_labels.get(id_) is None)}
    result["pending"] = pending
    if any(pending.values()):
        return result

    observed_roots = {value for value in observation_labels.values() if value != REJECTED}
    final_roots = {value for value in finding_labels.values() if value != REJECTED}
    final_by_root = defaultdict(list)
    for id_, root in sorted(finding_labels.items()):
        if root != REJECTED:
            final_by_root[root].append(id_)
    confirmed_observations = {id_ for id_, root in observation_labels.items() if root != REJECTED}
    same_root_links = {id_ for id_ in confirmed_observations if any(
        finding_labels[target] == observation_labels[id_] for target in observation_targets[id_])}
    result["semantic"] = {
        "unique_observation_defects": len(observed_roots),
        "unique_final_defects": len(final_roots),
        "root_retention": _fraction(len(observed_roots & final_roots), len(observed_roots)),
        "confirmed_observation_link_retention": _fraction(len(same_root_links), len(confirmed_observations)),
        "confirmed_observation_evidence_retention": _fraction(len(same_root_links & retained), len(confirmed_observations)),
        "lost_defect_ids": sorted(observed_roots - final_roots),
        "new_final_defect_ids": sorted(final_roots - observed_roots),
        "mismerged_observation_ids": sorted(confirmed_observations & linked - same_root_links),
        "duplicate_finding_ids": sorted(id_ for ids in final_by_root.values() for id_ in ids[1:]),
        "false_positive_finding_ids": sorted(id_ for id_, label in finding_labels.items() if label == REJECTED),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--labels", type=Path, help="Independent report-bound labels; see label_template in an unlabelled audit")
    args = parser.parse_args()
    try:
        labels = json.loads(args.labels.read_text()) if args.labels else None
        print(json.dumps(audit_report(args.report, labels), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

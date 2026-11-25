"""Serializable, validated provenance graph for a single specification review."""
from typing import Literal
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal[
    "logic_inspector", "scenario_builder", "gap_hunter", "contract_inspector",
    "risk_inspector", "reference_inspector", "code_checks",
]
Severity = Literal["critical", "high", "medium", "low"]
NodeKind = Literal["requirement", "evidence", "observation", "counterexample", "finding", "question", "reference"]
EdgeKind = Literal["cites", "about", "contributes_to", "disagrees_with", "illustrates", "asks", "from_reference"]


class GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class GraphEvidence(GraphModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start:
            raise ValueError("Invalid graph evidence range")
        return self


class GraphNode(GraphModel):
    id: str = Field(min_length=1)
    kind: NodeKind
    title: str = Field(min_length=1)
    role: Role | None = None
    severity: Severity | None = None
    requirement_id: str | None = None
    finding_id: str | None = None
    question_id: str | None = None
    source_id: str | None = None
    evidence: GraphEvidence | None = None
    description: str | None = None


class GraphEdge(GraphModel):
    source: str
    target: str
    kind: EdgeKind


class ExpertSummary(GraphModel):
    role: Role
    status: Literal["completed", "unavailable", "skipped"]
    observations: int = Field(ge=0)


class EvidenceGraph(GraphModel):
    schema_version: Literal[1] = 1
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    experts: list[ExpertSummary]
    finalization_status: Literal["completed", "deterministic_fallback"]
    unresolved_count: int = Field(ge=0)

    @model_validator(mode="after")
    def integrity(self):
        by_id = {node.id: node for node in self.nodes}
        ids = set(by_id)
        if len(ids) != len(self.nodes):
            raise ValueError("Duplicate graph node")
        for node in self.nodes:
            if node.kind == "requirement" and node.requirement_id != node.id:
                raise ValueError("Missing graph requirement identity")
            if node.kind == "finding" and node.finding_id != node.id:
                raise ValueError("Missing graph finding identity")
            if node.kind == "question" and node.question_id != node.id:
                raise ValueError("Missing graph question identity")
            if node.kind == "reference" and node.source_id != node.id:
                raise ValueError("Missing graph source identity")
            if (node.kind == "evidence") != (node.evidence is not None):
                raise ValueError("Missing or unexpected graph evidence")
            if node.kind in {"observation", "counterexample"} and node.role is None:
                raise ValueError("Missing graph observation role")
            for related, kind in (
                (node.requirement_id, "requirement"), (node.finding_id, "finding"),
                (node.question_id, "question"), (node.source_id, "reference"),
            ):
                if related is not None and (related not in by_id or by_id[related].kind != kind):
                    raise ValueError("Unknown graph node reference")
        links = {(edge.source, edge.target, edge.kind) for edge in self.edges}
        if len(links) != len(self.edges) or any(
            edge.source not in ids or edge.target not in ids or edge.source == edge.target
            for edge in self.edges
        ):
            raise ValueError("Invalid graph edge")
        endpoint_types = {
            "cites": ({"requirement", "observation", "finding", "question"}, {"evidence"}),
            "about": ({"observation", "finding", "question"}, {"requirement", "observation"}),
            "contributes_to": ({"observation"}, {"finding"}),
            "disagrees_with": ({"observation", "finding"}, {"finding"}),
            "illustrates": ({"counterexample"}, {"observation", "finding"}),
            "asks": ({"finding"}, {"question"}),
            "from_reference": ({"evidence", "finding"}, {"reference"}),
        }
        for edge in self.edges:
            source, target = by_id[edge.source], by_id[edge.target]
            allowed_sources, allowed_targets = endpoint_types[edge.kind]
            if source.kind not in allowed_sources or target.kind not in allowed_targets:
                raise ValueError("Invalid graph edge endpoint types")
            if edge.kind == "from_reference" and source.source_id != target.id:
                raise ValueError("Graph edge contradicts reference identity")
            if edge.kind == "cites" and target.source_id is not None and source.source_id != target.source_id:
                raise ValueError("Graph citation crosses unrelated reference sources")
            if edge.kind == "contributes_to" and source.finding_id != target.id:
                raise ValueError("Graph edge contradicts observation identity")
            if edge.kind == "asks" and target.finding_id != source.id:
                raise ValueError("Graph edge contradicts question identity")
            if edge.kind == "illustrates" and source.finding_id != target.finding_id:
                raise ValueError("Graph edge contradicts counterexample identity")
        if len({expert.role for expert in self.experts}) != len(self.experts):
            raise ValueError("Duplicate graph expert")
        counts = Counter(node.role for node in self.nodes if node.kind == "observation")
        declared = {expert.role:expert.observations for expert in self.experts}
        if set(counts) - declared.keys() or any(declared[role] != counts[role] for role in declared):
            raise ValueError("Graph expert count differs from observation nodes")
        return self

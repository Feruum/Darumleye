"""Metadata-only specification review traces and the Langfuse OTLP sink.

The application owns the OTel provider. The sink exports only this instrumentation
scope and rebuilds each span from an explicit allowlist before HTTP transmission.
No prompt, source text, provider error body, owner ID or tool payload is recorded.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Status, StatusCode
import structlog

from agent.services.spec_review_mode import REVIEW_MODES
from agent.build_info import GIT_COMMIT, SERVICE_VERSION

SCOPE = "hackathon.spec_review"
_LOG = structlog.get_logger(__name__)
_META = "langfuse.observation.metadata."
_TRACE_META = "langfuse.trace.metadata."
_COUNTERS = frozenset(
    {
        "observations",
        "rejected_observations",
        "requirements",
        "findings",
        "disagreements",
        "citations",
        "validated_citations",
        "criteria_covered",
        "criteria_expected",
        "set_criteria_covered",
        "set_criteria_expected",
        "checklist_covered",
        "checklist_expected",
        "cache_hits",
        "correction_attempts",
        "validation_errors",
        "input_tokens",
        "output_tokens",
        "groups",
        "nodes",
        "edges",
        "unresolved_count",
        "checkpoint_reuses",
        "reference_count",
        "model_calls",
        "drex_supported",
        "drex_uncertain",
        "drex_unsupported",
    }
)
# Names are authored by the application, not by an uploaded document. Even a
# future call site supplying arbitrary text cannot turn it into a span name.
_STAGES = frozenset(
    {
        "spec-review",
        "document-parsing",
        "requirement-extraction",
        "extract",
        "extraction",
        "individual",
        "set",
        "acceptance_alignment",
        "references",
        "reference",
        "reference-review",
        "reference-rag",
        "expert-review",
        "logic-inspector",
        "scenario-builder",
        "gap-hunter",
        "contract-inspector",
        "risk-inspector",
        "reference-inspector",
        "evidence-validator",
        "evidence-validation",
        "behavioral-validator",
        "structural-validator",
        "risk-validator",
        "global-evidence-validator",
        "evidence-graph",
        "finalizer",
        "targeted-re-review",
        "reconciliation",
        "drex-verification",
        "verdict",
        "archive",
        "cli-output",
        "review-analysis",
        "stage-correction",
        "stage-validation",
        "checkpoint",
        "pair-logic-scenario",
        "pair-gap-contract",
        "pair-risk-reference",
        "gap-audit",
        "gap-hunter-audit",
        "observation-validation",
        "sub-evidence-validator-1",
        "sub-evidence-validator-2",
        "sub-evidence-validator-3",
        "multi_pair_1",
        "multi_pair_2",
        "multi_pair_3",
        "multi_finalizer",
        "multi_recheck",
        *(
            f"multi_{role}_{phase}"
            for role in (
                "logic_inspector",
                "scenario_builder",
                "gap_hunter",
                "contract_inspector",
                "risk_inspector",
                "reference_inspector",
            )
            for phase in (
                "individual",
                "set",
                "readiness",
                "gaps",
                "observations",
                "reference",
            )
        ),
    }
)
_ROLE_RE = re.compile(
    r"^(expert[-_:])?(logic|scenario|gap|contract|risk|reference)([-_:](inspector|builder|hunter|rag))?([-_:](individual|set|readiness|gaps|observations))?$"
)
_MODEL_RE = re.compile(r"^[A-Za-z0-9./:_-]{1,120}$")
_ERROR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_OBSERVATION_ID_RE = re.compile(r"^O-(?:code-)?[0-9]{1,8}$")
_SOURCES = {"cli", "api", "worker", "pipeline"}
_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "not_committed",
    "unavailable",
    "not_applicable",
    "not_ready",
    "needs_revision",
    "ready",
    "deterministic_fallback",
}


def _uuid(value: str | UUID | None) -> str:
    try:
        return str(UUID(str(value))) if value else str(uuid4())
    except (ValueError, AttributeError, TypeError):
        return str(uuid4())


def _stage_name(value: str) -> str:
    return (
        value
        if isinstance(value, str) and (value in _STAGES or _ROLE_RE.fullmatch(value))
        else "review-stage"
    )


def _tracer():
    return trace.get_tracer(SCOPE)


@dataclass
class UsageTotals:
    calls: int = 0
    hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    measured_input_calls: int = 0
    measured_output_calls: int = 0
    cost_usd: Decimal = Decimal(0)
    measured_cost_calls: int = 0
    response_models: Counter[str] = field(default_factory=Counter)

    def summary(self) -> dict[str, int | float | bool | None]:
        input_complete = self.calls == self.measured_input_calls
        output_complete = self.calls == self.measured_output_calls
        reported_cost = float(self.cost_usd) if self.measured_cost_calls else None
        return {
            "model_calls": self.calls,
            "cache_hits": self.hits,
            "input_tokens": self.input_tokens if input_complete else None,
            "output_tokens": self.output_tokens if output_complete else None,
            "reported_input_tokens": self.input_tokens,
            "reported_output_tokens": self.output_tokens,
            "usage_complete": input_complete and output_complete,
            "cost_usd": reported_cost if self.calls == self.measured_cost_calls else None,
            "reported_cost_usd": reported_cost,
        }


@dataclass
class ReviewSpan:
    span: trace.Span
    stage: str = field(default="review-stage", kw_only=True)
    model: str | None = field(default=None, kw_only=True)
    _effort: str | None = field(default=None, init=False)
    _usage: UsageTotals | None = field(default=None, init=False)
    _called: bool = field(default=False, init=False)
    _measured: set[str] = field(default_factory=set, init=False)
    _failed: bool = field(default=False, init=False)

    def _stage_usage(self) -> UsageTotals | None:
        run = _active_run.get()
        if run is None:
            return None
        key = (self.stage, self.model, self._effort)
        if key not in run.by_stage:
            run.by_stage[key] = UsageTotals()
        return run.by_stage[key]

    def called(self) -> None:
        """Count the application-level provider invocation (SDK hidden retries are unknown)."""
        if not self._called:
            self._called = True
            self._usage = self._stage_usage()
            if self._usage is not None:
                self._usage.calls += 1

    def http_status(self, status: int) -> None:
        if type(status) is int and 100 <= status <= 599:
            self.span.set_attribute(f"{_META}http_status", status)

    def reasoning_effort(self, value: str | None) -> None:
        """Configured effort only, never the model's reasoning or response."""
        if isinstance(value, str) and value in {"low", "medium"}:
            self._effort = value
            self.span.set_attribute(f"{_META}reasoning_effort", value)

    def count(self, name: str, value: int) -> None:
        if name in _COUNTERS and type(value) is int and 0 <= value <= 2**53:
            self.span.set_attribute(f"{_META}{name}", value)

    def ids(self, field: str, values: Sequence[str]) -> None:
        if field == "observation_ids":
            identifiers = [
                value
                for value in values
                if isinstance(value, str) and _OBSERVATION_ID_RE.fullmatch(value)
            ][:256]
            self.span.set_attribute(f"{_META}observation_ids", identifiers)

    def cached(self) -> None:
        usage = self._stage_usage()
        if usage is not None:
            usage.hits += 1
        self.span.set_attribute(f"{_META}cache_hit", True)
        # Cached observations aren't a billed generation and have no token usage.
        self.span.set_attribute("langfuse.observation.type", "span")

    def usage(self, response: Any, *, openrouter: bool = False) -> None:
        """Read provider usage only, never response text or arbitrary metadata."""
        metadata = getattr(response, "response_metadata", None)
        if isinstance(metadata, Mapping) and self._usage is not None:
            model = metadata.get("model_name")
            if isinstance(model, str) and _MODEL_RE.fullmatch(model) and "model" not in self._measured:
                self._usage.response_models[model] += 1
                self._measured.add("model")
            token_usage = metadata.get("token_usage")
            if openrouter and isinstance(token_usage, Mapping) and "cost" not in self._measured:
                cost = token_usage.get("cost")
                if type(cost) in (int, float) and 0 <= cost <= 2**53:
                    self._usage.cost_usd += Decimal(str(cost))
                    self._usage.measured_cost_calls += 1
                    self._measured.add("cost")
        usage = (
            response
            if isinstance(response, Mapping)
            else getattr(response, "usage_metadata", None)
        )
        if not isinstance(usage, Mapping) or not usage:
            usage = (
                metadata.get("token_usage") if isinstance(metadata, Mapping) else None
            )
        if not isinstance(usage, Mapping):
            return
        for direction, alternatives in (
            ("input", ("input_tokens", "prompt_tokens")),
            ("output", ("output_tokens", "completion_tokens")),
        ):
            value = next((usage.get(key) for key in alternatives if key in usage), None)
            if type(value) is int and 0 <= value <= 2**53:
                self.span.set_attribute(f"gen_ai.usage.{direction}_tokens", value)
                if (
                    self._usage is not None
                    and direction not in self._measured
                ):
                    self._measured.add(direction)
                    if direction == "input":
                        self._usage.input_tokens += value
                        self._usage.measured_input_calls += 1
                    else:
                        self._usage.output_tokens += value
                        self._usage.measured_output_calls += 1

    def outcome(self, status: str) -> None:
        if status in _STATUSES:
            self.span.set_attribute(f"{_META}outcome", status)
            if status == "failed":
                self.span.set_status(StatusCode.ERROR)
                self.span.set_attribute("langfuse.observation.level", "ERROR")

    def failure(self, code: str) -> None:
        if not self._failed and _ERROR_RE.fullmatch(code):
            self.span.set_attribute(f"{_META}error_type", code)
        self._failed = True
        self.outcome("failed")

    def report(self, report: Any) -> None:
        """Record aggregate coverage and verdict, not any title or text."""
        # Imported at runtime to avoid a cycle with spec_review's stage tracing.
        from agent.services.spec_review import (
            INDIVIDUAL_CRITERIA,
            READINESS_CRITERIA,
            SET_CRITERIA,
        )

        requirements = list(getattr(report, "requirements", ()) or ())
        set_checks = list(getattr(report, "set_checks", ()) or ())
        checklist = list(getattr(report, "readiness_checklist", ()) or ())
        findings = list(getattr(report, "findings", ()) or ())
        individual_ids = set(INDIVIDUAL_CRITERIA)
        set_ids = set(SET_CRITERIA)
        readiness_ids = set(READINESS_CRITERIA)

        def covered(rows, *, attribute, expected):
            return {getattr(row, attribute, None) for row in rows} & expected

        req_rows = [list(getattr(item, "checks", ()) or ()) for item in requirements]
        covered_req = [
            covered(rows, attribute="criterion", expected=individual_ids)
            for rows in req_rows
        ]
        covered_set = covered(set_checks, attribute="criterion", expected=set_ids)
        # checked_readiness() fills absent rows with this deterministic fallback
        # in legacy and scoped reviews. It is not an actual expert assessment.
        assessed_checklist = [
            row
            for row in checklist
            if getattr(row, "comment", None)
            != "Пункт не подтверждён отдельной оценкой полного документа; требуется ручная проверка."
        ]
        covered_checklist = covered(
            assessed_checklist, attribute="id", expected=readiness_ids
        )
        self.count("requirements", len(requirements))
        self.count("findings", len(findings))
        self.count("criteria_expected", len(requirements) * len(individual_ids))
        self.count(
            "criteria_covered",
            sum(map(len, covered_req)),
        )
        self.count("set_criteria_expected", len(set_ids))
        self.count("set_criteria_covered", len(covered_set))
        self.count("checklist_expected", len(readiness_ids))
        self.count("checklist_covered", len(covered_checklist))
        coverage = (
            bool(requirements)
            and all(
                ids == individual_ids and len(rows) == len(individual_ids)
                for rows, ids in zip(req_rows, covered_req, strict=True)
            )
            and covered_set == set_ids
            and len(set_checks) == len(set_ids)
            and covered_checklist == readiness_ids
            and len(checklist) == len(readiness_ids)
        )
        self.span.set_attribute(f"{_META}coverage_complete", coverage)
        citation_count = sum(
            len(getattr(item, "evidence", ()) or ()) for item in findings
        )
        self.count("validated_citations", citation_count)
        review = getattr(report, "decision_review", None)
        decisions = getattr(review, "findings", ()) or ()
        for status in ("supported", "uncertain", "unsupported"):
            self.count(
                f"drex_{status}",
                sum(getattr(item, "status", None) == status for item in decisions),
            )
        run = _active_run.get()
        if run is not None:
            self.count("model_calls", sum(row.calls for row in run.by_stage.values()))
            self.count("cache_hits", sum(row.hits for row in run.by_stage.values()))
        verdict = getattr(getattr(report, "verdict", None), "status", None)
        if verdict in _STATUSES:
            self.span.set_attribute(f"{_META}verdict", verdict)
        graph = getattr(report, "evidence_graph", None)
        if graph is not None:
            self.count("nodes", len(getattr(graph, "nodes", ()) or ()))
            self.count("edges", len(getattr(graph, "edges", ()) or ()))
            self.count("unresolved_count", getattr(graph, "unresolved_count", None))


@dataclass
class ReviewRun(ReviewSpan):
    run_id: str
    review_mode: str
    source: str
    by_stage: dict[tuple[str, str | None, str | None], UsageTotals] = field(default_factory=dict)

    def usage_summary(self) -> dict[str, Any]:
        total = UsageTotals()
        rows = []
        for (stage, model, effort), usage in self.by_stage.items():
            total.calls += usage.calls
            total.hits += usage.hits
            total.input_tokens += usage.input_tokens
            total.output_tokens += usage.output_tokens
            total.measured_input_calls += usage.measured_input_calls
            total.measured_output_calls += usage.measured_output_calls
            total.cost_usd += usage.cost_usd
            total.measured_cost_calls += usage.measured_cost_calls
            rows.append({
                "stage": stage, "model": model, "reasoning_effort": effort,
                **usage.summary(), "response_models": dict(usage.response_models),
            })
        return {**total.summary(), "by_stage": rows}


_active_run: ContextVar[ReviewRun | None] = ContextVar("spec_review_run", default=None)


def _run_attributes(run_id: str, mode: str, source: str) -> dict[str, str]:
    return {
        "langfuse.trace.name": "spec-review",
        "langfuse.session.id": run_id,
        "langfuse.release": GIT_COMMIT,
        "langfuse.version": SERVICE_VERSION,
        f"{_TRACE_META}run_id": run_id,
        f"{_TRACE_META}review_mode": mode,
        f"{_TRACE_META}source": source,
        # V4 observation-level dashboard filtering must not depend on a join
        # against trace-only metadata.
        f"{_META}review_mode": mode,
        f"{_META}source": source,
        "langfuse.observation.type": "span",
    }


@contextmanager
def review_run(
    run_id: str | UUID | None = None,
    *,
    review_mode: str = "legacy",
    source: str = "pipeline",
) -> Iterator[ReviewRun]:
    """Fresh trace for each attempt; a durable run UUID is its Langfuse session."""
    existing = _active_run.get()
    if existing is not None and (run_id is None or existing.run_id == str(run_id)):
        yield existing
        return
    identifier = _uuid(run_id)
    mode = review_mode if review_mode in REVIEW_MODES else "legacy"
    source = source if source in _SOURCES else "pipeline"
    # Explicitly do not attach to an HTTP auto-instrumented parent, since that
    # parent is filtered out by the Langfuse exporter. Root traces stay complete.
    with _tracer().start_as_current_span(
        "spec-review",
        context=Context(),
        attributes=_run_attributes(identifier, mode, source),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        run = ReviewRun(span, identifier, mode, source)
        token = _active_run.set(run)
        try:
            yield run
        except BaseException as error:
            if type(error).__name__ == "CancelledError":
                run.outcome("cancelled")
            else:
                run.failure(type(error).__name__)
            raise
        finally:
            _active_run.reset(token)


@contextmanager
def review_stage(
    stage: str, *, model: str | None = None, prompt: str | None = None
) -> Iterator[ReviewSpan]:
    """Span for validated pipeline stages. No raw exception descriptions/events."""
    run = _active_run.get()
    if run is None:
        yield ReviewSpan(trace.INVALID_SPAN)
        return
    attributes = _run_attributes(run.run_id, run.review_mode, run.source)
    if stage in {
        "logic-inspector",
        "scenario-builder",
        "gap-hunter",
        "contract-inspector",
        "risk-inspector",
        "reference-inspector",
        "finalizer",
    }:
        attributes["langfuse.observation.type"] = "agent"
    elif stage == "reference-rag":
        attributes["langfuse.observation.type"] = "retriever"
    elif stage.startswith(("pair-", "sub-evidence-validator-")) or stage in {
        "global-evidence-validator",
        "stage-validation",
        "evidence-validation",
    }:
        attributes["langfuse.observation.type"] = "guardrail"
    elif stage == "drex-verification":
        attributes["langfuse.observation.type"] = "evaluator"
    elif stage == "review-analysis":
        attributes["langfuse.observation.type"] = "chain"
    if isinstance(model, str) and _MODEL_RE.fullmatch(model):
        attributes["gen_ai.request.model"] = model
        attributes["langfuse.observation.type"] = "generation"
        attributes["langfuse.observation.model.name"] = model
    if isinstance(prompt, str):
        attributes[f"{_META}prompt_version"] = hashlib.sha256(
            prompt.encode()
        ).hexdigest()
    with _tracer().start_as_current_span(
        _stage_name(stage),
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        wrapper = ReviewSpan(span, stage=_stage_name(stage), model=attributes.get("gen_ai.request.model"))
        try:
            yield wrapper
        except BaseException as error:
            if type(error).__name__ == "CancelledError":
                wrapper.outcome("cancelled")
            else:
                wrapper.failure(type(error).__name__)
            raise


def _safe_attributes(source: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce both allowed keys and value shapes at the export boundary."""
    result = {}
    for key, value in source.items():
        if key in {"langfuse.session.id", f"{_TRACE_META}run_id"}:
            try:
                result[key] = str(UUID(str(value)))
            except (ValueError, TypeError, AttributeError):
                pass
        elif key == "langfuse.trace.name" and value == "spec-review":
            result[key] = value
        elif (
            key in {f"{_TRACE_META}review_mode", f"{_META}review_mode"}
            and isinstance(value, str)
            and value in REVIEW_MODES
        ):
            result[key] = value
        elif (
            key in {f"{_TRACE_META}source", f"{_META}source"}
            and isinstance(value, str)
            and value in _SOURCES
        ):
            result[key] = value
        elif key == "langfuse.release" and value == GIT_COMMIT:
            result[key] = value
        elif key == "langfuse.version" and value == SERVICE_VERSION:
            result[key] = value
        elif (
            key == "langfuse.observation.type"
            and isinstance(value, str)
            and value
            in {
                "span",
                "generation",
                "agent",
                "retriever",
                "guardrail",
                "evaluator",
                "chain",
            }
        ):
            result[key] = value
        elif key == "langfuse.observation.level" and value == "ERROR":
            result[key] = value
        elif key == "langfuse.internal.is_app_root" and value is True:
            result[key] = value
        elif (
            key == f"{_META}reasoning_effort"
            and isinstance(value, str)
            and value in {"low", "medium"}
        ):
            result[key] = value
        elif (
            key in {"gen_ai.request.model", "langfuse.observation.model.name"}
            and isinstance(value, str)
            and _MODEL_RE.fullmatch(value)
        ):
            result[key] = value
        elif key in {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"} or (
            key.startswith(_META) and key[len(_META) :] in _COUNTERS
        ):
            if type(value) is int and 0 <= value <= 2**53:
                result[key] = value
        elif (
            key == f"{_META}http_status" and type(value) is int and 100 <= value <= 599
        ):
            result[key] = value
        elif (
            key in {f"{_META}cache_hit", f"{_META}coverage_complete"}
            and type(value) is bool
        ):
            result[key] = value
        elif key == f"{_META}observation_ids" and isinstance(value, (tuple, list)):
            result[key] = [
                item
                for item in value
                if isinstance(item, str) and _OBSERVATION_ID_RE.fullmatch(item)
            ][:256]
        elif (
            key == f"{_META}prompt_version"
            and isinstance(value, str)
            and _HASH_RE.fullmatch(value)
        ):
            result[key] = value
        elif (
            key == f"{_META}error_type"
            and isinstance(value, str)
            and _ERROR_RE.fullmatch(value)
        ):
            result[key] = value
        elif (
            key in {f"{_META}outcome", f"{_META}verdict"}
            and isinstance(value, str)
            and value in _STATUSES
        ):
            result[key] = value
    return result


def should_export_to_langfuse(span: ReadableSpan) -> bool:
    """Reject unexpected content before SDK media handling or batch export.

    The custom exporter sanitizes again, including events/resources/status.
    Langfuse SDK can process embedded media before export-time masking, so a
    span with any unapproved attribute must never enter its export queue.
    """
    if (
        span.instrumentation_scope is None
        or span.instrumentation_scope.name != SCOPE
        or _stage_name(span.name) != span.name
    ):
        return False
    attributes = dict(span.attributes or {})
    validated = _safe_attributes(attributes)
    if validated.keys() != attributes.keys():
        return False
    return all(
        tuple(value) == tuple(attributes[key])
        if isinstance(value, (tuple, list))
        else value == attributes[key]
        for key, value in validated.items()
    )


class LangfuseReviewExporter(SpanExporter):
    """Langfuse's native OTLP endpoint with a fail-closed metadata boundary.

    The Langfuse SDK's BatchSpanProcessor sends exports off the event loop. Failures
    never retry synchronously or propagate to the review or existing exporter.
    """

    def __init__(
        self,
        base_url: str,
        public_key: str,
        secret_key: str,
        *,
        client: httpx.Client | None = None,
    ):
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid Langfuse service URL")
        self.endpoint = base_url.rstrip("/") + "/api/public/otel/v1/traces"
        self.scores_endpoint = base_url.rstrip("/") + "/api/public/scores"
        self.auth = httpx.BasicAuth(public_key, secret_key)
        self.client = client or httpx.Client(
            timeout=3.0, follow_redirects=False, trust_env=False
        )
        # Do not inherit arbitrary OTEL_RESOURCE_ATTRIBUTES from the global provider.
        self.resource = Resource(
            {"service.name": "agent", "service.version": SERVICE_VERSION}
        )

    def _export_scores(self, spans: Sequence[ReadableSpan]) -> None:
        """Publish only typed root counters, after a terminal outcome is recorded."""
        scores = []
        for span in spans:
            if (
                span.name != "spec-review"
                or span.context is None
                or span.end_time is None
            ):
                continue
            attrs = span.attributes or {}
            outcome = attrs.get(f"{_META}outcome")
            if outcome not in {"completed", "failed"}:
                continue
            trace_id = format(span.context.trace_id, "032x")
            timestamp = datetime.fromtimestamp(span.end_time / 1e9, UTC).isoformat()
            counters: list[tuple[str, str, Any]] = [
                ("terminal_success", "BOOLEAN", float(outcome == "completed"))
            ]
            if outcome == "completed":
                complete = attrs.get(f"{_META}coverage_complete")
                if type(complete) is bool:
                    counters.append(("coverage_complete", "BOOLEAN", float(complete)))
                for name in (
                    "findings",
                    "criteria_covered",
                    "criteria_expected",
                    "set_criteria_covered",
                    "set_criteria_expected",
                    "checklist_covered",
                    "checklist_expected",
                    "validated_citations",
                    "drex_supported",
                    "drex_uncertain",
                    "drex_unsupported",
                    "unresolved_count",
                ):
                    value = attrs.get(f"{_META}{name}")
                    if type(value) is int:
                        counters.append((name, "NUMERIC", float(value)))
                verdict = attrs.get(f"{_META}verdict")
                if verdict in {"ready", "needs_revision", "not_ready"}:
                    counters.append(("verdict", "CATEGORICAL", verdict))
            for name, data_type, value in counters:
                score_name = f"spec_review_{name}"
                scores.append(
                    {
                        "id": f"{trace_id}-{score_name}",
                        "name": score_name,
                        "traceId": trace_id,
                        "timestamp": timestamp,
                        "dataType": data_type,
                        "value": value,
                    }
                )
        if scores:
            try:
                response = self.client.post(
                    self.scores_endpoint, json=scores, auth=self.auth
                )
                if not response.is_success:
                    _LOG.warning(
                        "langfuse_scores_export_failed",
                        http_status=response.status_code,
                    )
            except Exception as error:
                # The trace has already been exported. Scores are also best-effort.
                _LOG.warning(
                    "langfuse_scores_export_failed", error_type=type(error).__name__
                )

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            safe = []
            for span in spans:
                scope = span.instrumentation_scope
                if scope is None or scope.name != SCOPE:
                    continue
                safe.append(
                    ReadableSpan(
                        name=_stage_name(span.name),
                        context=span.context,
                        parent=span.parent,
                        resource=self.resource,
                        attributes=_safe_attributes(span.attributes or {}),
                        kind=span.kind,
                        status=Status(span.status.status_code),
                        start_time=span.start_time,
                        end_time=span.end_time,
                        instrumentation_scope=InstrumentationScope(SCOPE),
                        # No events, links, raw resource attributes or status descriptions.
                    )
                )
            if not safe:
                return SpanExportResult.SUCCESS
            response = self.client.post(
                self.endpoint,
                content=encode_spans(safe).SerializeToString(),
                auth=self.auth,
                headers={
                    "Content-Type": "application/x-protobuf",
                    "x-langfuse-ingestion-version": "4",
                },
            )
            if not response.is_success:
                _LOG.warning("langfuse_export_failed", http_status=response.status_code)
                return SpanExportResult.FAILURE
            self._export_scores(safe)
            return SpanExportResult.SUCCESS
        except Exception as error:
            _LOG.warning("langfuse_export_failed", error_type=type(error).__name__)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

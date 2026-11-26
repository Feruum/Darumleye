"""Review telemetry exports only technical metadata, even on failures."""

from uuid import uuid4

import httpx
import pytest
from opentelemetry import trace
from types import SimpleNamespace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent.utils import review_telemetry as telemetry
from agent.utils.observability import configure_langfuse
from agent.config.settings import Settings
from agent.services.spec_review import (
    INDIVIDUAL_CRITERIA,
    SET_CRITERIA,
    READINESS_CRITERIA,
)


@pytest.fixture(autouse=True)
def enable_local_traces(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)


@pytest.mark.parametrize("mode", ["legacy", "multi_agent", "multi_agent_direct"])
def test_langfuse_sdk_uses_shared_provider_and_blocks_media_before_export(monkeypatch, mode):
    """Use the real SDK processor, not a fake Langfuse implementation."""
    from langfuse import Langfuse
    from langfuse._task_manager.media_manager import MediaManager

    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200)

    def unexpected_media(*args, **kwargs):
        pytest.fail("Private span content reached the SDK media pipeline")

    monkeypatch.setattr(MediaManager, "_find_and_process_media", unexpected_media)
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    provider = TracerProvider()
    transport = httpx.MockTransport(receive)
    public_key = f"pk-lf-{uuid4()}"
    exporter = telemetry.LangfuseReviewExporter(
        "http://langfuse.test",
        public_key,
        "sk-lf-test",
        client=httpx.Client(transport=transport),
    )
    config = Settings(
        langfuse_base_url="http://langfuse.test",
        langfuse_public_key=public_key,
        langfuse_secret_key="sk-lf-test",
    )
    sdk = configure_langfuse(provider, config, exporter=exporter)
    assert isinstance(sdk, Langfuse)
    monkeypatch.setattr(
        telemetry, "_tracer", lambda: provider.get_tracer(telemetry.SCOPE)
    )
    try:
        with provider.get_tracer("other").start_as_current_span(
            "private-http",
            attributes={"gen_ai.prompt": "data:text/plain;base64,UFJJVkFURQ=="},
        ):
            pass
        with telemetry.review_run(str(uuid4()), review_mode=mode):
            with telemetry.review_stage("extraction", model="model-a") as stage:
                stage.usage({"input_tokens": 17, "output_tokens": 5})
                stage.reasoning_effort("low")
                stage.reasoning_effort("PRIVATE REASONING")
            with telemetry.review_stage("finalizer"):
                trace.get_current_span().set_attribute(
                    "gen_ai.prompt", "data:text/plain;base64,UFJJVkFURQ=="
                )
        assert provider.force_flush(timeout_millis=3000)
        names = []
        attributes = {}
        for request in requests:
            assert request.url.path == "/api/public/otel/v1/traces"
            assert b"UFJJVkFURQ" not in request.content
            spans = ExportTraceServiceRequest.FromString(request.content)
            for resource in spans.resource_spans:
                for scope in resource.scope_spans:
                    for span in scope.spans:
                        names.append(span.name)
                        attributes[span.name] = {
                            attr.key: attr.value for attr in span.attributes
                        }
        assert sorted(names) == ["extraction", "spec-review"]
        assert (
            attributes["extraction"][
                "langfuse.observation.metadata.reasoning_effort"
            ].string_value
            == "low"
        )
        assert (
            attributes["extraction"][
                "langfuse.observation.metadata.review_mode"
            ].string_value
            == mode
        )
    finally:
        sdk.shutdown()
        provider.shutdown()


def test_relates_nested_stages_and_resume_by_run_id(monkeypatch):
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(
        telemetry, "_tracer", lambda: provider.get_tracer(telemetry.SCOPE)
    )
    identifier = str(uuid4())
    with telemetry.review_run(
        identifier, review_mode="multi_agent", source="worker"
    ) as run:
        assert run.run_id == identifier
        with telemetry.review_run(identifier, review_mode="multi_agent") as nested:
            assert nested is run
            with telemetry.review_stage(
                "individual", model="model-a", prompt="secret document"
            ) as stage:
                stage.usage({"input_tokens": 17, "output_tokens": 5})
                stage.count("observations", 2)
                stage.reasoning_effort("medium")
            with telemetry.review_stage("individual", model="model-a") as stage:
                stage.cached()
        with pytest.raises(RuntimeError):
            with telemetry.review_stage("finalizer"):
                raise RuntimeError("secret document")
    with telemetry.review_run(identifier, review_mode="multi_agent", source="worker"):
        with telemetry.review_stage("archive"):
            pass
    spans = memory.get_finished_spans()
    roots = [s for s in spans if s.name == "spec-review"]
    assert len(roots) == 2
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert all(s.attributes["langfuse.session.id"] == identifier for s in spans)
    individual = [s for s in spans if s.name == "individual"]
    assert individual[0].attributes["gen_ai.usage.input_tokens"] == 17
    assert individual[0].attributes["gen_ai.usage.output_tokens"] == 5
    assert individual[0].attributes["langfuse.observation.type"] == "generation"
    assert (
        individual[0].attributes["langfuse.observation.metadata.reasoning_effort"]
        == "medium"
    )
    assert individual[1].attributes["langfuse.observation.type"] == "span"
    assert individual[1].attributes["langfuse.observation.metadata.cache_hit"] is True
    error = next(s for s in spans if s.name == "finalizer")
    assert error.status.status_code is trace.StatusCode.ERROR
    assert error.status.description is None
    assert not error.events
    assert "secret document" not in repr([s.attributes for s in spans])


def test_exporter_strips_non_review_spans_and_sensitive_fields(monkeypatch):
    payloads = []

    def receive(request):
        payloads.append((str(request.url), request.content, request.headers))
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(receive))
    exporter = telemetry.LangfuseReviewExporter(
        "http://langfuse:3000", "public", "secret", client=client
    )
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(
        telemetry, "_tracer", lambda: provider.get_tracer(telemetry.SCOPE)
    )
    with provider.get_tracer("other").start_as_current_span(
        "private-http", attributes={"secret": "LEAK-CANARY"}
    ):
        with telemetry.review_run(str(uuid4()), review_mode="legacy"):
            with telemetry.review_stage(
                "extraction", model="model-a", prompt="LEAK-CANARY"
            ):
                span = trace.get_current_span()
                span.set_attribute("gen_ai.prompt", "LEAK-CANARY")
                span.add_event("LEAK-CANARY")
    assert exporter.export(memory.get_finished_spans()) is SpanExportResult.SUCCESS
    assert len(payloads) == 1
    url, payload, headers = payloads[0]
    assert url.endswith("/api/public/otel/v1/traces")
    assert headers["x-langfuse-ingestion-version"] == "4"
    assert headers["authorization"].startswith("Basic ")
    assert b"LEAK-CANARY" not in payload
    parsed = ExportTraceServiceRequest.FromString(payload)
    names = [
        span.name
        for resource in parsed.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    assert sorted(names) == ["extraction", "spec-review"]
    exporter.shutdown()


def test_exporter_failure_does_not_raise():
    def receive(request):
        raise httpx.ConnectError("private provider details", request=request)

    exporter = telemetry.LangfuseReviewExporter(
        "http://langfuse:3000",
        "public",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(receive)),
    )
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer(telemetry.SCOPE).start_as_current_span("spec-review"):
        pass
    assert exporter.export(memory.get_finished_spans()) is SpanExportResult.FAILURE
    exporter.shutdown()


def test_usage_summary_not_dependent_on_otlp(monkeypatch):
    monkeypatch.setattr(telemetry, "_tracer", lambda: trace.NoOpTracer())
    with telemetry.review_run(str(uuid4())) as run:
        with telemetry.review_stage("extract", model="model-a") as stage:
            stage.called()
            stage.usage(
                SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 3})
            )
        with telemetry.review_stage("extract", model="model-a") as stage:
            stage.cached()
        assert run.usage_summary() == {
            "model_calls": 1,
            "cache_hits": 1,
            "input_tokens": 10,
            "output_tokens": 3,
            "reported_input_tokens": 10,
            "reported_output_tokens": 3,
            "usage_complete": True,
            "cost_usd": None,
        }
        with telemetry.review_stage("set", model="model-b") as stage:
            stage.called()
        assert run.usage_summary()["input_tokens"] is None
        assert run.usage_summary()["reported_input_tokens"] == 10
        assert not run.usage_summary()["usage_complete"]


def test_coverage_summary_is_metadata_only(monkeypatch):
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(
        telemetry, "_tracer", lambda: provider.get_tracer(telemetry.SCOPE)
    )
    with telemetry.review_run(str(uuid4())) as run:
        run.report(
            SimpleNamespace(
                requirements=[
                    SimpleNamespace(
                        checks=[
                            SimpleNamespace(criterion=c) for c in INDIVIDUAL_CRITERIA
                        ],
                        text="PRIVATE",
                    )
                ],
                findings=[SimpleNamespace(evidence=["PRIVATE CITATION"])],
                set_checks=[SimpleNamespace(criterion=c) for c in SET_CRITERIA],
                readiness_checklist=[
                    SimpleNamespace(id=id_) for id_ in list(READINESS_CRITERIA)[:-1]
                ],
                verdict=SimpleNamespace(status="needs_revision"),
            )
        )
        run.outcome("completed")
    attributes = memory.get_finished_spans()[0].attributes
    assert attributes["langfuse.observation.metadata.coverage_complete"] is False
    assert attributes["langfuse.observation.metadata.criteria_covered"] == 9
    assert attributes["langfuse.observation.metadata.checklist_expected"] == 15
    assert attributes["langfuse.observation.metadata.outcome"] == "completed"
    assert "PRIVATE" not in repr(attributes)


def test_coverage_detects_duplicate_criteria_and_unassessed_placeholders(monkeypatch):
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(
        telemetry, "_tracer", lambda: provider.get_tracer(telemetry.SCOPE)
    )
    readiness = [SimpleNamespace(id=id_) for id_ in READINESS_CRITERIA]
    readiness[
        0
    ].comment = "Пункт не подтверждён отдельной оценкой полного документа; требуется ручная проверка."
    with telemetry.review_run(str(uuid4())) as run:
        run.report(
            SimpleNamespace(
                requirements=[
                    SimpleNamespace(
                        checks=[
                            SimpleNamespace(criterion=c)
                            for c in [*INDIVIDUAL_CRITERIA[:-1], INDIVIDUAL_CRITERIA[0]]
                        ]
                    )
                ],
                set_checks=[SimpleNamespace(criterion=c) for c in SET_CRITERIA],
                readiness_checklist=readiness,
            )
        )
    attributes = memory.get_finished_spans()[0].attributes
    assert attributes["langfuse.observation.metadata.coverage_complete"] is False
    assert attributes["langfuse.observation.metadata.criteria_covered"] == 8
    assert attributes["langfuse.observation.metadata.checklist_covered"] == 14


def test_completed_run_exports_dashboard_scores(monkeypatch):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(receive))
    exporter = telemetry.LangfuseReviewExporter(
        "http://langfuse:3000", "public", "secret", client=client
    )
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(
        telemetry, "_tracer", lambda: provider.get_tracer(telemetry.SCOPE)
    )
    with telemetry.review_run(str(uuid4()), review_mode="multi_agent") as run:
        run.report(
            SimpleNamespace(
                requirements=[
                    SimpleNamespace(
                        checks=[
                            SimpleNamespace(criterion=c) for c in INDIVIDUAL_CRITERIA
                        ],
                        text="PRIVATE",
                    )
                ],
                findings=[],
                set_checks=[SimpleNamespace(criterion=c) for c in SET_CRITERIA],
                readiness_checklist=[
                    SimpleNamespace(id=id_) for id_ in READINESS_CRITERIA
                ],
                verdict=SimpleNamespace(status="ready"),
            )
        )
        run.outcome("completed")
    assert exporter.export(memory.get_finished_spans()) is SpanExportResult.SUCCESS
    assert len(requests) == 2
    assert str(requests[1].url).endswith("/api/public/scores")
    import json

    scores = {score["name"]: score for score in json.loads(requests[1].content)}
    assert scores["spec_review_coverage_complete"]["value"] == 1
    assert scores["spec_review_coverage_complete"]["dataType"] == "BOOLEAN"
    assert scores["spec_review_findings"]["value"] == 0
    assert scores["spec_review_verdict"]["value"] == "ready"
    assert scores["spec_review_set_criteria_expected"]["value"] == 6
    assert scores["spec_review_checklist_expected"]["value"] == 15
    assert b"PRIVATE" not in requests[1].content
    exporter.shutdown()

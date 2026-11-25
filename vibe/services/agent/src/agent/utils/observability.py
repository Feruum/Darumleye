"""OpenTelemetry export and agent service instruments."""

from __future__ import annotations

import os
import logging
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from agent.build_info import GIT_COMMIT, SERVICE_VERSION
from agent.config.settings import Settings, settings
from agent.utils.review_telemetry import (
    LangfuseReviewExporter,
    should_export_to_langfuse,
)
import structlog

_configured = False
_trace_provider: TracerProvider | None = None
_langfuse_client: Any | None = None
logger = structlog.get_logger(__name__)

meter = metrics.get_meter("hackathon.agent")
http_requests = meter.create_counter(
    "agent.http.requests",
    unit="{request}",
    description="HTTP requests handled by the agent service",
)
http_duration = meter.create_histogram(
    "agent.http.server.duration",
    unit="s",
    description="HTTP server request duration",
)
stream_active = meter.create_up_down_counter(
    "agent.streams.active",
    unit="{stream}",
    description="Currently admitted agent streams",
)
stream_rejections = meter.create_counter(
    "agent.streams.rejected",
    unit="{request}",
    description="Stream admission rejections",
)
stream_ttft = meter.create_histogram(
    "agent.streams.time_to_first_token",
    unit="s",
    description="Time from stream generator start to first model token",
)
stream_duration = meter.create_histogram(
    "agent.streams.duration",
    unit="s",
    description="Accepted stream duration",
)
stream_output_chunks = meter.create_counter(
    "agent.streams.output_chunks",
    unit="{chunk}",
    description="Model output chunks emitted to clients",
)
stream_errors = meter.create_counter(
    "agent.streams.errors",
    unit="{error}",
    description="Accepted streams ending in an error",
)


def configure_langfuse(
    provider: TracerProvider,
    config: Settings,
    *,
    exporter: LangfuseReviewExporter | None = None,
):
    """Attach the official SDK to the shared provider, without automatic LLM capture."""
    from langfuse import Langfuse

    sink = exporter or LangfuseReviewExporter(
        config.langfuse_base_url,
        config.langfuse_public_key,
        config.langfuse_secret_key,
    )
    try:
        return Langfuse(
            public_key=config.langfuse_public_key,
            secret_key=config.langfuse_secret_key,
            base_url=config.langfuse_base_url,
            tracer_provider=provider,
            should_export_span=should_export_to_langfuse,
            span_exporter=sink,
            timeout=3,
            flush_at=512,
            flush_interval=1.0,
            sample_rate=1.0,
            media_upload_thread_count=1,
        )
    except Exception:
        sink.shutdown()
        raise
    finally:
        # SDK debug logs can print full internal spans; this service's
        # diagnostic contract is strictly technical metadata.
        logging.getLogger("langfuse").setLevel(logging.WARNING)


def configure_observability(configuration: Settings | None = None) -> None:
    """One OTel provider, with independent optional infrastructure and LLM sinks."""
    global _configured, _trace_provider, _langfuse_client
    config = configuration or settings
    if _configured or os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return
    send_to_langfuse = all(
        (
            config.langfuse_base_url,
            config.langfuse_public_key,
            config.langfuse_secret_key,
        )
    )
    if not config.otlp_endpoint and not send_to_langfuse:
        return

    resource = Resource.create(
        attributes={
            "service.name": "agent",
            "service.version": SERVICE_VERSION,
            "service.instance.id": os.getenv("HOSTNAME", "unknown"),
            "deployment.environment.name": config.environment,
            "vcs.ref.head.revision": GIT_COMMIT,
        }
    )
    current = trace.get_tracer_provider()
    provider = (
        current
        if isinstance(current, TracerProvider)
        else TracerProvider(resource=resource, sampler=ALWAYS_ON)
    )
    if config.otlp_endpoint:
        try:
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=config.otlp_endpoint,
                        insecure=config.otlp_insecure,
                    )
                )
            )
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=config.otlp_endpoint, insecure=config.otlp_insecure
                ),
                export_interval_millis=30_000,
            )
            metrics.set_meter_provider(
                MeterProvider(resource=resource, metric_readers=[metric_reader])
            )
        except Exception as error:
            logger.warning("otel_configuration_failed", error_type=type(error).__name__)
    if send_to_langfuse:
        try:
            _langfuse_client = configure_langfuse(provider, config)
        except Exception as error:
            # Optional telemetry must not prevent application startup or CLI review.
            logger.warning(
                "langfuse_configuration_failed", error_type=type(error).__name__
            )
    if provider is not current:
        trace.set_tracer_provider(provider)
    _trace_provider = provider
    _configured = True


def flush_observability() -> None:
    """Best-effort CLI export before the process exits; no server request wait."""
    if _trace_provider is not None:
        try:
            _trace_provider.force_flush(timeout_millis=3500)
        except Exception as error:
            logger.warning("otel_flush_failed", error_type=type(error).__name__)


def instrument_fastapi(app: Any) -> None:
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return
    FastAPIInstrumentor.instrument_app(app)


def get_tracer(name: str):
    return trace.get_tracer(name)


def record_http_request(
    method: str, route: str, status_code: int, duration: float
) -> None:
    attributes = {
        "http.request.method": method,
        "http.route": route,
        "http.response.status_code": status_code,
    }
    http_requests.add(1, attributes)
    http_duration.record(duration, attributes)


def record_stream_admitted() -> None:
    stream_active.add(1)


def record_stream_finished(duration: float, *, model: str, error: bool) -> None:
    attributes = {"gen_ai.request.model": model}
    stream_active.add(-1)
    stream_duration.record(duration, attributes)
    if error:
        stream_errors.add(1, attributes)


def record_stream_rejected(reason: str) -> None:
    stream_rejections.add(1, {"reason": reason})


def record_first_token(duration: float, model: str) -> None:
    stream_ttft.record(duration, {"gen_ai.request.model": model})


def record_output_chunk(model: str) -> None:
    stream_output_chunks.add(1, {"gen_ai.request.model": model})

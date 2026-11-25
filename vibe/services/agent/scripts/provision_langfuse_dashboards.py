#!/usr/bin/env python3
"""Opt-in provisioning of Spec Review monitoring boards on Langfuse v4.

Default invocation prints a local manifest. --apply creates missing project
widgets/boards/placements using the documented unstable public API. It never
updates or removes existing items. Existing names are the stable identities.

Contract checked against upstream `unstable-dashboard-widgets.ts`,
`unstable-dashboards.ts`, `interfaces/filters.ts`, and query/dataModel.ts.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from collections.abc import Sequence
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from dotenv import dotenv_values
import httpx


# These are the canonical Langfuse observations and score query dimensions.
# Dashboard widgets use `agg` (unlike the public metrics API, `aggregation`).
# Filter values are only code-authored stage names and typed score names.
def eq(column: str, value: str) -> dict:
    return {"type": "string", "column": column, "operator": "=", "value": value}


def any_of(column: str, values: list[str]) -> dict:
    return {
        "type": "stringOptions",
        "column": column,
        "operator": "any of",
        "value": values,
    }


def metadata_filter(key: str, value: str) -> dict:
    return {
        "type": "stringObject",
        "column": "metadata",
        "key": key,
        "operator": "=",
        "value": value,
    }


def mode_filter(mode: str) -> dict:
    return metadata_filter("review_mode", mode)


def positive_counter(key: str) -> dict:
    return {
        "type": "numberObject",
        "column": "metadata",
        "key": key,
        "operator": ">",
        "value": 0,
    }


def widget(
    board,
    name,
    *,
    measure="count",
    agg="count",
    dimension=None,
    filters=(),
    view="observations",
    chart="LINE_TIME_SERIES",
    description="",
):
    return {
        "name": f"Spec Review / {board} / {name}",
        "description": description,
        "view": view,
        "dimensions": [{"field": dimension}] if dimension else [],
        "metrics": [{"measure": measure, "agg": agg}],
        "filters": [eq("traceName", "spec-review"), *filters],
        "chartType": chart,
    }


def _prompt_hash(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError("Prompt version must be a 64-character SHA-256 hash")
    return value.lower()


def evaluation_model_widgets(prompt_versions: Sequence[str] = ()) -> list[dict]:
    """Compare observed model generations by mode and exact prompt hash.

    Metrics v2 can group by providedModelName, not an arbitrary metadata key.
    A requested hash is therefore an exact stringObject filter rather than
    an unsupported grouping dimension or a Langfuse prompt-registry version.
    """
    versions = list(dict.fromkeys(_prompt_hash(value) for value in prompt_versions))
    widgets = []
    for mode, label in (("multi_agent_direct", "Direct"), ("multi_agent", "Multi-agent")):
        base = [eq("type", "GENERATION"), mode_filter(mode)]
        for version in (None, *versions):
            prefix = label if version is None else f"{label} prompt {version}"
            filters = (
                base
                if version is None
                else [*base, metadata_filter("prompt_version", version)]
            )
            description = (
                "Observed generations; compare workload, not semantic quality."
                if version is None
                else "Exact SHA-256 of transmitted system instructions. Compare a shared stage/template; absent data is not a zero-cost success."
            )
            for title, measure, agg in (
                ("model calls", "count", "count"),
                ("model latency p95 (ms)", "latency", "p95"),
                ("model tokens", "totalTokens", "sum"),
            ):
                widgets.append(
                    widget(
                        "Model Evaluation",
                        f"{prefix} · {title}",
                        measure=measure,
                        agg=agg,
                        dimension="providedModelName",
                        filters=filters,
                        chart="HORIZONTAL_BAR",
                        description=description,
                    )
                )
    return widgets


def dashboard_manifest(prompt_versions: Sequence[str] = ()) -> list[dict]:
    root = [eq("name", "spec-review")]
    analysis = [eq("name", "review-analysis")]
    model_calls = [eq("type", "GENERATION")]
    experts = [
        any_of(
            "name",
            [
                "logic-inspector",
                "scenario-builder",
                "gap-hunter",
                "contract-inspector",
                "risk-inspector",
                "reference-inspector",
                "reference-rag",
            ],
        )
    ]
    pairs = [
        any_of(
            "name", ["pair-logic-scenario", "pair-gap-contract", "pair-risk-reference"]
        )
    ]
    operations = "Review Operations"
    performance = "Agent Performance"
    quality = "Evidence Quality"
    cost = "Cost Intelligence"
    evaluation = "Model Evaluation"
    return [
        {
            "name": f"Spec Review — {operations}",
            "description": "Terminal outcomes, latency and durable review attempts.",
            "widgets": [
                widget(
                    operations,
                    "Review attempts by level",
                    dimension="level",
                    filters=root,
                    chart="BAR_TIME_SERIES",
                ),
                widget(
                    operations,
                    "Failed attempts",
                    filters=[*root, eq("level", "ERROR")],
                    chart="NUMBER",
                ),
                widget(
                    operations,
                    "Analysis p95 (ms)",
                    measure="latency",
                    agg="p95",
                    filters=analysis,
                ),
                widget(
                    operations,
                    "Terminal success rate (0–1)",
                    view="scores-boolean",
                    measure="value",
                    agg="avg",
                    filters=[eq("name", "spec_review_terminal_success")],
                ),
                widget(
                    operations,
                    "Non-committed attempts",
                    filters=[*root, metadata_filter("outcome", "not_committed")],
                ),
                widget(
                    operations,
                    "Checkpoint reuse count",
                    filters=[metadata_filter("cache_hit", "true")],
                ),
                widget(
                    operations,
                    "Resumed worker attempts",
                    filters=[
                        eq("name", "checkpoint"),
                        metadata_filter("source", "worker"),
                        metadata_filter("outcome", "completed"),
                    ],
                    description="One marker per re-entry into a claimed durable review after a prior charged execution attempt; independent of model checkpoint reuse. Counts attempts, not distinct job UUIDs.",
                ),
            ],
        },
        {
            "name": f"Spec Review — {performance}",
            "description": "Independent specialists, paired validators and model requests.",
            "widgets": [
                widget(
                    performance,
                    "Expert latency p50 (ms)",
                    measure="latency",
                    agg="p50",
                    dimension="name",
                    filters=experts,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Expert latency p95 (ms)",
                    measure="latency",
                    agg="p95",
                    dimension="name",
                    filters=experts,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Pair validator latency p95 (ms)",
                    measure="latency",
                    agg="p95",
                    dimension="name",
                    filters=pairs,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Model call count by stage",
                    dimension="name",
                    filters=model_calls,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Input tokens by stage",
                    measure="inputTokens",
                    agg="sum",
                    dimension="name",
                    filters=model_calls,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Output tokens by stage",
                    measure="outputTokens",
                    agg="sum",
                    dimension="name",
                    filters=model_calls,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Stages with rejected observations",
                    dimension="name",
                    filters=[positive_counter("rejected_observations")],
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    performance,
                    "Errors by stage",
                    dimension="name",
                    filters=[eq("level", "ERROR")],
                    chart="HORIZONTAL_BAR",
                ),
            ],
        },
        {
            "name": f"Spec Review — {quality}",
            "description": "Structural completeness and findings; not a ground-truth accuracy assessment.",
            "widgets": [
                widget(
                    quality,
                    "Complete coverage rate (0–1)",
                    view="scores-boolean",
                    measure="value",
                    agg="avg",
                    filters=[eq("name", "spec_review_coverage_complete")],
                ),
                widget(
                    quality,
                    "Covered and expected criterion counts",
                    view="scores-numeric",
                    measure="value",
                    agg="sum",
                    dimension="name",
                    filters=[
                        any_of(
                            "name",
                            [
                                "spec_review_criteria_covered",
                                "spec_review_criteria_expected",
                                "spec_review_set_criteria_covered",
                                "spec_review_set_criteria_expected",
                                "spec_review_checklist_covered",
                                "spec_review_checklist_expected",
                            ],
                        )
                    ],
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    quality,
                    "Average findings and citations",
                    view="scores-numeric",
                    measure="value",
                    agg="avg",
                    dimension="name",
                    filters=[
                        any_of(
                            "name",
                            ["spec_review_findings", "spec_review_validated_citations"],
                        )
                    ],
                    chart="BAR_TIME_SERIES",
                ),
                widget(
                    quality,
                    "Unresolved disagreements",
                    view="scores-numeric",
                    measure="value",
                    agg="sum",
                    filters=[eq("name", "spec_review_unresolved_count")],
                ),
                widget(
                    quality,
                    "Drex assessment totals",
                    view="scores-numeric",
                    measure="value",
                    agg="sum",
                    dimension="name",
                    filters=[
                        any_of(
                            "name",
                            [
                                "spec_review_drex_supported",
                                "spec_review_drex_uncertain",
                                "spec_review_drex_unsupported",
                            ],
                        )
                    ],
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    quality,
                    "Failed validation attempts",
                    filters=[eq("name", "stage-validation"), eq("level", "ERROR")],
                    chart="BAR_TIME_SERIES",
                ),
            ],
        },
        {
            "name": f"Spec Review — {cost}",
            "description": "Observed tokens and Langfuse pricing estimates, not provider invoices.",
            "widgets": [
                widget(
                    cost,
                    "Input tokens by model",
                    measure="inputTokens",
                    agg="sum",
                    dimension="providedModelName",
                    filters=model_calls,
                    chart="BAR_TIME_SERIES",
                ),
                widget(
                    cost,
                    "Output tokens by model",
                    measure="outputTokens",
                    agg="sum",
                    dimension="providedModelName",
                    filters=model_calls,
                    chart="BAR_TIME_SERIES",
                ),
                widget(
                    cost,
                    "Estimated model cost (USD)",
                    measure="totalCost",
                    agg="sum",
                    dimension="providedModelName",
                    filters=model_calls,
                    chart="HORIZONTAL_BAR",
                    description="Estimate only when model pricing/usage is registered; zero does not establish zero invoiced cost.",
                ),
                widget(
                    cost,
                    "Tokens by stage",
                    measure="totalTokens",
                    agg="sum",
                    dimension="name",
                    filters=model_calls,
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    cost,
                    "Direct tokens",
                    measure="totalTokens",
                    agg="sum",
                    filters=[*model_calls, mode_filter("multi_agent_direct")],
                ),
                widget(
                    cost,
                    "Multi-agent tokens",
                    measure="totalTokens",
                    agg="sum",
                    filters=[*model_calls, mode_filter("multi_agent")],
                ),
            ],
        },
        {
            "name": f"Spec Review — {evaluation}",
            "description": "Configuration and mode comparisons; use offline benchmark for empirical accuracy.",
            "widgets": [
                widget(
                    evaluation,
                    "Direct analysis p95 (ms)",
                    measure="latency",
                    agg="p95",
                    filters=[*analysis, mode_filter("multi_agent_direct")],
                ),
                widget(
                    evaluation,
                    "Multi-agent analysis p95 (ms)",
                    measure="latency",
                    agg="p95",
                    filters=[*analysis, mode_filter("multi_agent")],
                ),
                widget(
                    evaluation,
                    "Coverage rate by release",
                    view="scores-boolean",
                    measure="value",
                    agg="avg",
                    dimension="traceRelease",
                    filters=[eq("name", "spec_review_coverage_complete")],
                    chart="HORIZONTAL_BAR",
                ),
                widget(
                    evaluation,
                    "Verdict distribution",
                    view="scores-categorical",
                    dimension="stringValue",
                    filters=[eq("name", "spec_review_verdict")],
                    chart="PIE",
                ),
                widget(
                    evaluation,
                    "Direct attempts",
                    filters=[*root, mode_filter("multi_agent_direct")],
                    chart="BAR_TIME_SERIES",
                ),
                widget(
                    evaluation,
                    "Multi-agent attempts",
                    filters=[*root, mode_filter("multi_agent")],
                    chart="BAR_TIME_SERIES",
                ),
                *evaluation_model_widgets(prompt_versions),
            ],
        },
    ]


def _list_all(client: httpx.Client, path: str) -> list[dict]:
    items = []
    page = 1
    while True:
        response = client.get(path, params={"page": page, "limit": 100})
        response.raise_for_status()
        result = response.json()
        items.extend(result["data"])
        if page >= result["meta"]["totalPages"]:
            return items
        page += 1
        if page > 1000:
            raise ValueError(f"Too many pages in {path}")


def _by_name(items: list[dict], names: set[str]) -> dict[str, dict]:
    matching = {}
    for item in items:
        if item["name"] not in names:
            continue
        if item["name"] in matching:
            raise ValueError(
                f"Found duplicate existing name: {item['name']}; no unambiguous target"
            )
        matching[item["name"]] = item
    return matching


def _create(client: httpx.Client, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise ValueError("Unexpected Langfuse API response: missing created item ID")
    return result


def _placements(board_name: str, widget_ids: list[str], *, start_y=0) -> list[dict]:
    return [
        {
            "id": str(uuid5(NAMESPACE_URL, f"{board_name}:{widget_id}")),
            "type": "widget",
            "widgetId": widget_id,
            "x": (index % 2) * 6,
            "y": start_y + (index // 2) * 6,
            "width": 6,
            "height": 6,
        }
        for index, widget_id in enumerate(widget_ids)
    ]


def apply_dashboards(
    client: httpx.Client, prompt_versions: Sequence[str] = ()
) -> list[dict]:
    """Create missing named assets and append missing placements only."""
    manifest = dashboard_manifest(prompt_versions)
    boards = _by_name(
        _list_all(client, "dashboards"), {item["name"] for item in manifest}
    )
    widgets = _by_name(
        _list_all(client, "dashboard-widgets"),
        {widget_["name"] for board in manifest for widget_ in board["widgets"]},
    )
    applied = []
    for board in manifest:
        ids = []
        for widget_ in board["widgets"]:
            existing = widgets.get(widget_["name"])
            if existing is None:
                existing = _create(client, "dashboard-widgets", widget_)
                widgets[widget_["name"]] = existing
            ids.append(existing["id"])
        current = boards.get(board["name"])
        if current is None:
            current = _create(
                client,
                "dashboards",
                {
                    "name": board["name"],
                    "description": board["description"],
                    "definition": {"widgets": _placements(board["name"], ids)},
                    "filters": [],
                },
            )
        else:
            placements = current["definition"]["widgets"]
            occupied = {
                p.get("widgetId") for p in placements if p.get("type") == "widget"
            }
            missing = [widget_id for widget_id in ids if widget_id not in occupied]
            max_y = max((p["y"] + p["height"] for p in placements), default=0)
            path = f"dashboards/{quote(current['id'], safe='')}/placements"
            for placement in _placements(board["name"], missing, start_y=max_y):
                _create(client, path, placement)
        applied.append({"name": board["name"], "id": current["id"]})
    return applied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing assets in configured Langfuse project",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Read AGENT_LANGFUSE_* settings (environment takes precedence)",
    )
    parser.add_argument(
        "--prompt-version",
        action="append",
        default=[],
        metavar="SHA256",
        help="Add exact per-mode/model charts for a previously observed prompt hash; repeat for multiple versions",
    )
    args = parser.parse_args(argv)
    try:
        prompt_versions = [_prompt_hash(value) for value in args.prompt_version]
    except ValueError:
        parser.error("Each --prompt-version must be a 64-character SHA-256 hash")
    if not args.apply:
        print(
            json.dumps(
                dashboard_manifest(prompt_versions), indent=2, ensure_ascii=False
            )
        )
        return 0
    config = {**dotenv_values(args.env_file), **os.environ}
    url = config.get("AGENT_LANGFUSE_BASE_URL")
    public_key = config.get("AGENT_LANGFUSE_PUBLIC_KEY")
    secret_key = config.get("AGENT_LANGFUSE_SECRET_KEY")
    if not all((url, public_key, secret_key)):
        parser.error("Configure all three AGENT_LANGFUSE_* values before --apply")
    try:
        endpoint = httpx.URL(url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.host
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Invalid Langfuse base URL")
        with httpx.Client(
            base_url=url.rstrip("/") + "/api/public/unstable/",
            auth=(public_key, secret_key),
            timeout=10,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            result = apply_dashboards(client, prompt_versions)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except httpx.HTTPStatusError as error:
        # Never dump the HTTP response, credentials, or project contents.
        print(
            f"Langfuse provisioning failed: HTTP {error.response.status_code}. Check v4 API compatibility and project permissions; re-run safely after correction.",
            file=sys.stderr,
        )
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
        print(
            f"Langfuse provisioning failed ({type(error).__name__}). Check configuration and existing names.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())

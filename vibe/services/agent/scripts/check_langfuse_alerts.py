#!/usr/bin/env python3
"""Evaluate Spec Review's three initial alert rules through Langfuse Metrics v2.

No network by default. --check reads aggregates for the past hour and prints
only numeric results/states; exit 0=OK, 1=ALERT, 2=UNKNOWN/NO_DATA.
--watch repeats locally and logs a structured state. No report content is
retrieved and no notifications sent. The container requires only Python.
"""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class Response(Protocol):
    status_code: int

    def json(self) -> dict: ...


class QueryClient(Protocol):
    def get(self, path: str, *, params: dict) -> Response: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class _Response:
    status_code: int
    body: bytes = b""

    def json(self) -> dict:
        return json.loads(self.body)


class MetricsClient:
    """Read-only standard-library HTTP transport; no proxy or redirect leaks."""

    def __init__(self, url: str, public: str, secret: str):
        endpoint = urlsplit(url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Invalid Langfuse base URL")
        self._base = url.rstrip("/")
        credential = base64.b64encode(f"{public}:{secret}".encode()).decode()
        self._authorization = "Basic " + credential
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def get(self, path: str, *, params: dict) -> _Response:
        if path != "/api/public/v2/metrics":
            raise ValueError("Only aggregate metrics queries may be performed")
        request = Request(
            self._base + path + "?" + urlencode(params),
            headers={
                "Authorization": self._authorization,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=15) as response:
                # Aggregated results have zero or one row; bound response bytes.
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise ValueError("Metric response exceeds size bound")
                return _Response(response.status, body)
        except HTTPError as error:
            error.close()
            return _Response(error.code)


def eq(column: str, value: str) -> dict:
    return {"type": "string", "column": column, "operator": "=", "value": value}


def alert_manifest() -> list[dict]:
    trace = eq("traceName", "spec-review")
    count = {"measure": "count", "aggregation": "count"}
    return [
        {
            "name": "Review failed",
            "view": "observations",
            "metrics": [count],
            "filters": [
                trace,
                eq("name", "spec-review"),
                {
                    "type": "stringOptions",
                    "column": "level",
                    "operator": "any of",
                    "value": ["ERROR"],
                },
            ],
            "operator": ">=",
            "threshold": 1,
            "window_minutes": 60,
        },
        {
            "name": "Required coverage incomplete",
            "view": "scores-boolean",
            "metrics": [count, {"measure": "value", "aggregation": "sum"}],
            "filters": [trace, eq("name", "spec_review_coverage_complete")],
            "operator": ">=",
            "threshold": 1,
            "window_minutes": 60,
        },
        {
            "name": "Review p95 latency",
            "view": "observations",
            "metrics": [count, {"measure": "latency", "aggregation": "p95"}],
            "filters": [trace, eq("name", "review-analysis")],
            "operator": ">",
            "threshold": 720_000,
            "window_minutes": 60,
        },
    ]


def _number(value) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("Invalid metric value")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid metric value")
    return number


def _count(value) -> int:
    number = _number(value)
    if not number.is_integer() or number > 2**53:
        raise ValueError("Invalid aggregate count")
    return int(number)


def _value(rule: dict, rows: list[dict]) -> float | int | None:
    # No dimensions/time grouping => zero or one row, not an arbitrary
    # first row from a partially malformed API response.
    if not isinstance(rows, list) or len(rows) > 1:
        raise ValueError("Unexpected metric result")
    if not rows:
        return 0 if rule["name"] == "Review failed" else None
    row = rows[0]
    count = _count(row["count_count"])
    if rule["name"] == "Review failed":
        return count
    if count == 0:
        return None
    if rule["view"] == "scores-boolean":
        # Score values are 0/1. Count - sum(value) is the exact false
        # count; unlike filtering for false only, zero data is detectable.
        true_count = _count(row["sum_value"])
        if true_count > count:
            raise ValueError("Unexpected boolean score aggregate")
        return count - true_count
    return _number(row["p95_latency"])


def evaluate_alerts(client: QueryClient, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Alert evaluation time must be timezone aware")
    results = []
    for rule in alert_manifest():
        start = now - timedelta(minutes=rule["window_minutes"])
        result = {
            "name": rule["name"],
            "operator": rule["operator"],
            "threshold": rule["threshold"],
            "window_minutes": rule["window_minutes"],
            "fromTimestamp": start.isoformat(),
            "toTimestamp": now.isoformat(),
            "value": None,
            "state": "UNKNOWN",
        }
        query = {
            "view": rule["view"],
            "metrics": rule["metrics"],
            "filters": rule["filters"],
            "dimensions": [],
            "fromTimestamp": result["fromTimestamp"],
            "toTimestamp": result["toTimestamp"],
        }
        try:
            response = client.get(
                "/api/public/v2/metrics", params={"query": json.dumps(query)}
            )
            if not 200 <= response.status_code < 300:
                result["http_status"] = response.status_code
                results.append(result)
                continue
            value = _value(rule, response.json()["data"])
            result["value"] = value
            if value is None:
                result["state"] = "NO_DATA"
            else:
                breached = (
                    value >= rule["threshold"]
                    if rule["operator"] == ">="
                    else value > rule["threshold"]
                )
                result["state"] = "ALERT" if breached else "OK"
        except Exception as error:
            # API error payloads and any credential-bearing URLs are never printed.
            result["error_type"] = type(error).__name__
        results.append(result)
    return results


def exit_status(results: list[dict]) -> int:
    states = {result["state"] for result in results}
    if "ALERT" in states:
        return 1
    return 0 if states == {"OK"} else 2


def _configuration(path: Path) -> tuple[str, str, str] | None:
    values = dict(os.environ)
    if path.is_file():
        # Used by the desktop CLI. The Compose monitor receives variables only,
        # so the lightweight container needs no extra dependencies.
        from dotenv import dotenv_values

        values = {**dotenv_values(path), **values}
    url = values.get("AGENT_LANGFUSE_BASE_URL")
    public = values.get("AGENT_LANGFUSE_PUBLIC_KEY")
    secret = values.get("AGENT_LANGFUSE_SECRET_KEY")
    return (url, public, secret) if all((url, public, secret)) else None


def _watch(env_file: Path, interval: int) -> int:
    previous_disabled = False
    try:
        while True:
            try:
                config = _configuration(env_file)
                if config is None:
                    if not previous_disabled:
                        print(
                            json.dumps(
                                {"state": "DISABLED", "reason": "project_keys_missing"}
                            ),
                            flush=True,
                        )
                    previous_disabled = True
                else:
                    previous_disabled = False
                    result = evaluate_alerts(MetricsClient(*config))
                    state = {0: "OK", 1: "ALERT", 2: "UNKNOWN"}[exit_status(result)]
                    print(json.dumps({"state": state, "rules": result}), flush=True)
            except Exception as error:
                print(
                    json.dumps(
                        {"state": "UNKNOWN", "error_type": type(error).__name__}
                    ),
                    flush=True,
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Query aggregate metrics")
    mode.add_argument(
        "--watch", action="store_true", help="Repeat checks and log local state"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be positive")
    if args.watch:
        return _watch(args.env_file, args.interval_seconds)
    if not args.check:
        print(json.dumps(alert_manifest(), indent=2))
        return 0
    try:
        config = _configuration(args.env_file)
        if config is None:
            parser.error("Configure all three AGENT_LANGFUSE_* values before --check")
        result = evaluate_alerts(MetricsClient(*config))
    except Exception as error:
        print(json.dumps({"state": "UNKNOWN", "error_type": type(error).__name__}))
        return 2
    print(json.dumps(result, indent=2))
    return exit_status(result)


if __name__ == "__main__":
    raise SystemExit(main())

"""Initial alert rules evaluate the official aggregate metrics API contract."""

import importlib.util
import json
from pathlib import Path
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_langfuse_alerts.py"
SPEC = importlib.util.spec_from_file_location("check_langfuse_alerts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
alerts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = alerts
SPEC.loader.exec_module(alerts)

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)


def evaluate(rows):
    queries = []

    def receive(request):
        assert request.method == "GET"
        assert request.url.path == "/api/public/v2/metrics"
        query = json.loads(request.url.params["query"])
        queries.append(query)
        result = rows[len(queries) - 1]
        if isinstance(result, int):
            return httpx.Response(result, text="PRIVATE CONTENT")
        return httpx.Response(200, json={"data": result})

    with httpx.Client(
        base_url="http://langfuse.test",
        transport=httpx.MockTransport(receive),
    ) as client:
        result = alerts.evaluate_alerts(client, now=NOW)
    assert len(queries) == 3
    for query in queries:
        assert datetime.fromisoformat(query["fromTimestamp"]) == NOW - timedelta(
            hours=1
        )
        assert datetime.fromisoformat(query["toTimestamp"]) == NOW
        assert not query["dimensions"]
        assert query["filters"][0]["value"] == "spec-review"
    assert queries[0]["metrics"] == [{"measure": "count", "aggregation": "count"}]
    assert queries[1]["view"] == "scores-boolean"
    assert {"measure": "value", "aggregation": "sum"} in queries[1]["metrics"]
    assert queries[2]["view"] == "observations"
    assert {"measure": "latency", "aggregation": "p95"} in queries[2]["metrics"]
    return result


def test_thresholds_alert_and_coverage_uses_count_of_false_scores():
    result = evaluate(
        [
            [{"count_count": "1"}],
            [{"count_count": "10", "sum_value": 9}],
            [{"count_count": 2, "p95_latency": 720001}],
        ]
    )
    assert [item["state"] for item in result] == ["ALERT"] * 3
    assert [item["value"] for item in result] == [1, 1, 720001]
    assert alerts.exit_status(result) == 1


def test_healthy_and_exact_latency_threshold():
    result = evaluate(
        [
            [{"count_count": 0}],
            [{"count_count": 3, "sum_value": "3"}],
            [{"count_count": 2, "p95_latency": 720000}],
        ]
    )
    assert [item["state"] for item in result] == ["OK"] * 3
    assert alerts.exit_status(result) == 0


def test_empty_metrics_not_misrepresented_as_healthy():
    result = evaluate([[], [], []])
    assert [item["state"] for item in result] == ["OK", "NO_DATA", "NO_DATA"]
    assert alerts.exit_status(result) == 2


@pytest.mark.parametrize(
    "coverage", [503, [{"count_count": 5}], [{"count_count": 1, "sum_value": 4}]]
)
def test_api_error_or_impossible_score_aggregate_reports_unknown(coverage):
    result = evaluate(
        [
            [{"count_count": 0}],
            coverage,
            [{"count_count": 1, "p95_latency": 2000}],
        ]
    )
    assert [item["state"] for item in result] == ["OK", "UNKNOWN", "OK"]
    assert alerts.exit_status(result) == 2
    assert "PRIVATE CONTENT" not in json.dumps(result)


def test_default_prints_local_rules_without_opening_network(monkeypatch, capsys):
    monkeypatch.setattr(
        alerts,
        "MetricsClient",
        lambda *args, **kwargs: pytest.fail("unexpected network"),
    )
    assert alerts.main([]) == 0
    rules = json.loads(capsys.readouterr().out)
    assert [rule["threshold"] for rule in rules] == [1, 1, 720000]


def test_stdlib_monitor_only_allows_metric_endpoint_and_no_redirect():
    client = alerts.MetricsClient(
        "http://langfuse-web:3000", "test-public", "test-secret"
    )
    with pytest.raises(ValueError):
        client.get("/api/public/v2/observations", params={})
    with pytest.raises(ValueError):
        alerts.MetricsClient("http://user:pass@host", "test-public", "test-secret")
    # Redirects must not send a project's Basic Auth to another endpoint.
    assert (
        alerts._NoRedirect().redirect_request(
            None, None, 302, "", {}, "http://other.invalid"
        )
        is None
    )


def test_watch_process_logs_alert_then_waits(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("AGENT_LANGFUSE_BASE_URL", "http://langfuse-web:3000")
    monkeypatch.setenv("AGENT_LANGFUSE_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("AGENT_LANGFUSE_SECRET_KEY", "test-secret")
    monkeypatch.setattr(alerts, "MetricsClient", lambda *args: object())
    monkeypatch.setattr(
        alerts,
        "evaluate_alerts",
        lambda client: [{"name": "Review failed", "state": "ALERT", "value": 1}],
    )

    def stop(_interval):
        raise KeyboardInterrupt

    monkeypatch.setattr(alerts.time, "sleep", stop)
    assert (
        alerts.main(
            [
                "--watch",
                "--interval-seconds",
                "60",
                "--env-file",
                str(tmp_path / "empty"),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ALERT"
    assert result["rules"][0]["value"] == 1


def test_watch_without_project_keys_disables_network(monkeypatch, capsys, tmp_path):
    for k in ("BASE_URL", "PUBLIC_KEY", "SECRET_KEY"):
        monkeypatch.delenv(f"AGENT_LANGFUSE_{k}", raising=False)
    monkeypatch.setattr(
        alerts, "MetricsClient", lambda *args: pytest.fail("unexpected network")
    )

    def stop(_interval):
        raise KeyboardInterrupt

    monkeypatch.setattr(alerts.time, "sleep", stop)
    assert alerts.main(["--watch", "--env-file", str(tmp_path / "empty")]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "DISABLED"

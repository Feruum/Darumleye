"""Provisioning uses documented public widget/placement contracts, no live API."""

from pathlib import Path
import importlib.util
import json
import sys

import httpx
import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "provision_langfuse_dashboards.py"
)
SPEC = importlib.util.spec_from_file_location("provision_langfuse_dashboards", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
provision = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision
SPEC.loader.exec_module(provision)


def test_manifest_has_five_distinct_boards_and_safe_v4_widget_shapes():
    manifest = provision.dashboard_manifest()
    assert {board["name"].split(" — ")[-1] for board in manifest} == {
        "Review Operations",
        "Agent Performance",
        "Evidence Quality",
        "Cost Intelligence",
        "Model Evaluation",
    }
    widgets = [widget for board in manifest for widget in board["widgets"]]
    assert len({widget["name"] for widget in widgets}) == len(widgets)
    for widget in widgets:
        assert widget["view"] in {
            "observations",
            "scores-numeric",
            "scores-boolean",
            "scores-categorical",
        }
        assert all(set(metric) == {"measure", "agg"} for metric in widget["metrics"])
        assert all(set(dimension) == {"field"} for dimension in widget["dimensions"])
        assert any(
            filter_["column"] == "traceName" and filter_["value"] == "spec-review"
            for filter_ in widget["filters"]
        )
    assert any(
        widget["view"] == "scores-boolean"
        and any(
            f.get("value") == "spec_review_coverage_complete" for f in widget["filters"]
        )
        for widget in widgets
    )
    assert any(
        any(f.get("key") == "cache_hit" for f in widget["filters"])
        for widget in widgets
    )
    assert any(
        any(f.get("key") == "rejected_observations" for f in widget["filters"])
        for widget in widgets
    )


def test_model_evaluation_compares_modes_models_and_selected_prompt_hashes():
    hashes = ["a" * 64, "b" * 64]
    manifest = provision.dashboard_manifest([*hashes, hashes[0]])
    evaluation = next(
        board for board in manifest if board["name"].endswith("Model Evaluation")
    )
    model_widgets = [
        widget
        for widget in evaluation["widgets"]
        if widget["dimensions"] == [{"field": "providedModelName"}]
    ]
    assert model_widgets
    for mode in ("legacy", "multi_agent"):
        scoped = [
            widget
            for widget in model_widgets
            if provision.mode_filter(mode) in widget["filters"]
        ]
        assert {"count", "latency", "totalTokens"} <= {
            metric["measure"] for widget in scoped for metric in widget["metrics"]
        }
        for version in hashes:
            matching = [
                widget
                for widget in scoped
                if provision.metadata_filter("prompt_version", version)
                in widget["filters"]
            ]
            assert {
                metric["measure"] for widget in matching for metric in widget["metrics"]
            } == {"count", "latency", "totalTokens"}
    assert len({widget["name"] for widget in evaluation["widgets"]}) == len(
        evaluation["widgets"]
    )
    with pytest.raises(ValueError, match="64"):
        provision.dashboard_manifest(["not-a-hash"])


def test_operations_counts_resumed_attempt_markers_separately_from_cache_reuse():
    operations = next(
        board
        for board in provision.dashboard_manifest()
        if board["name"].endswith("Review Operations")
    )
    resume = next(
        w
        for w in operations["widgets"]
        if w["name"].endswith("Resumed worker attempts")
    )
    assert provision.eq("name", "checkpoint") in resume["filters"]
    assert provision.metadata_filter("source", "worker") in resume["filters"]
    assert provision.metadata_filter("outcome", "completed") in resume["filters"]
    assert not any(f.get("key") == "cache_hit" for f in resume["filters"])


def test_apply_is_idempotent_and_preserves_edited_board_and_widget():
    boards = []
    widgets = []
    writes = []

    def handler(request):
        path = request.url.path.removeprefix("/api/public/unstable/")
        if request.method == "GET":
            collection = boards if path == "dashboards" else widgets
            if path not in {"dashboards", "dashboard-widgets"}:
                raise AssertionError(path)
            return httpx.Response(
                200,
                json={
                    "data": collection,
                    "meta": {
                        "page": 1,
                        "limit": 100,
                        "totalItems": len(collection),
                        "totalPages": 1,
                    },
                },
            )
        assert request.method == "POST"  # Never patch or delete operator data.
        body = json.loads(request.content)
        writes.append(path)
        if path == "dashboard-widgets":
            created = {"id": f"widget-{len(widgets)}", **body}
            widgets.append(created)
        elif path == "dashboards":
            created = {"id": f"board-{len(boards)}", **body}
            boards.append(created)
        else:
            _, board_id, _ = path.split("/")
            target = next(board for board in boards if board["id"] == board_id)
            created = {
                "id": f"placement-{len(target['definition']['widgets'])}",
                "x": 0,
                "y": 0,
                "width": 6,
                "height": 6,
                **body,
            }
            target["definition"]["widgets"].append(created)
        return httpx.Response(201, json=created)

    with httpx.Client(
        base_url="http://langfuse/api/public/unstable/",
        transport=httpx.MockTransport(handler),
    ) as client:
        provision.apply_dashboards(client)
        assert len(boards) == 5
        first_count = len(writes)
        assert first_count > 5
        # Preserve manual edits. If a placement was lost, add only the missing item.
        boards[0]["description"] = "operator revision"
        widgets[0]["description"] = "operator customization"
        board = boards[0]
        lost = board["definition"]["widgets"].pop()
        provision.apply_dashboards(client)
        assert len(writes) == first_count + 1
        assert writes[-1].endswith("/placements")
        assert board["definition"]["widgets"][-1]["widgetId"] == lost["widgetId"]
        assert boards[0]["description"] == "operator revision"
        assert widgets[0]["description"] == "operator customization"
        provision.apply_dashboards(client)
        assert len(writes) == first_count + 1
        base_widget_count = len(widgets)
        evaluation = next(
            board for board in boards if board["name"].endswith("Model Evaluation")
        )
        initial_placements = list(evaluation["definition"]["widgets"])
        provision.apply_dashboards(client, ["a" * 64])
        assert len(boards) == 5
        assert len(widgets) == base_widget_count + 6
        assert (
            evaluation["definition"]["widgets"][: len(initial_placements)]
            == initial_placements
        )
        additional_writes = len(writes)
        provision.apply_dashboards(client, ["a" * 64])
        assert len(writes) == additional_writes
        # A default provision must also leave selected-hash widgets intact.
        provision.apply_dashboards(client)
        assert len(writes) == additional_writes


def test_duplicate_existing_names_fail_before_mutating():
    name = provision.dashboard_manifest()[0]["name"]

    def handler(request):
        assert request.method == "GET"
        data = (
            [{"id": "a", "name": name}, {"id": "b", "name": name}]
            if request.url.path.endswith("/dashboards")
            else []
        )
        return httpx.Response(200, json={"data": data, "meta": {"totalPages": 1}})

    with httpx.Client(
        base_url="http://langfuse/api/public/unstable/",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="duplicate"):
            provision.apply_dashboards(client)


def test_dry_run_never_constructs_api_client(monkeypatch, capsys):
    monkeypatch.setattr(
        provision.httpx,
        "Client",
        lambda **kwargs: pytest.fail("Dry run must not connect"),
    )
    assert provision.main([]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 5
    assert provision.main(["--prompt-version", "A" * 64]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert (
        sum(len(board["widgets"]) for board in selected)
        == sum(len(board["widgets"]) for board in data) + 6
    )
    with pytest.raises(SystemExit):
        provision.main(["--prompt-version", "not-a-hash", "--apply"])


def test_pagination_reads_all_pages_before_creation():
    pages = []

    def handler(request):
        assert request.method == "GET"
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "data": [{"id": f"item-{page}", "name": f"page-{page}"}],
                "meta": {"totalPages": 2},
            },
        )

    with httpx.Client(
        base_url="http://langfuse/api/public/unstable/",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert [item["id"] for item in provision._list_all(client, "dashboards")] == [
            "item-1",
            "item-2",
        ]
    assert pages == [1, 2]

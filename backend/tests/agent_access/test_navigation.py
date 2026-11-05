"""Synthetic navigation and evidence contracts, independent of DB or app state."""

import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
pytest.importorskip("playwright")

import httpx  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from agent_access.capture import CaptureClient  # noqa: E402
from agent_access.catalog import Catalog  # noqa: E402
from agent_access.errors import AccessError  # noqa: E402
from agent_access.references import context_hash, pointer_value, validate_references  # noqa: E402
from agent_access.server import INPUT_SCHEMAS  # noqa: E402
from agent_access.service import (
    DashboardService,  # noqa: E402
    target_preview,  # noqa: E402
)
from agent_access.snapshots import SnapshotStore  # noqa: E402

SUBJECT = "00000000-0000-0000-0000-000000000001"
CLOCK = {
    "current_night": "2026-10-30",
    "action_day": "2026-11-02",
    "market_today": "2026-11-01",
    "market_tz": "America/New_York",
}


def captured(dashboard="board"):
    return {
        "schema_version": "1",
        "dashboard": dashboard,
        "asof": "2026-10-30",
        "captured_at": "2026-11-01T00:00:00Z",
        "context": {
            "clock_response": dict(CLOCK),
            "clock": "public",
            "known_at": "2026-10-31T00:00:00Z",
            "decisions_asof": "2026-10-30",
            "countdown_origin": "2026-10-30",
            "scope": "deployment_visibility_not_authorization",
            "limitations": [],
        },
        "consistency": {
            "database": "repeatable_read_read_only",
            "generation_before": "synthetic",
            "generation_after": "synthetic",
            "generation_stable": True,
        },
        "responses": [
            {"method": "GET", "path": "/clock", "query": {}, "status": 200, "body": dict(CLOCK)},
            {
                "method": "GET",
                "path": "/theses",
                "query": {"include_archived": "true"},
                "status": 200,
                "body": {
                    "a/b": {
                        "~key": [
                            None,
                            {
                                "detail": "Synthetic evidence exact 12.345",
                                "url": "https://example.invalid/reference-only",
                            },
                        ]
                    }
                },
            },
        ],
        "navigation": [
            {
                "id": "nav-synthetic",
                "kind": "fresh_capture",
                "destination": {"dashboard": "cockpit", "settings": {"thesis_id": SUBJECT}},
            }
        ],
        "references": [
            {
                "id": "ref-synthetic",
                "kind": "captured_json",
                "response_index": 1,
                "pointer": "/a~1b/~0key/1",
                "label": "Synthetic provenance",
                "category": "call_input",
            }
        ],
    }


class SyntheticCatalog(Catalog):
    def __init__(self):
        super().__init__()
        detail = copy.deepcopy(self.describe("board"))
        detail.update(
            slug="cockpit",
            surface_kind="detail",
            settings_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"thesis_id": {"type": "string", "format": "uuid"}},
                "required": ["thesis_id"],
            },
            defaults={},
            backend_query_fields=["thesis_id"],
        )
        self._by_slug["cockpit"] = detail


class SyntheticCapture:
    def __init__(self, mutate=None):
        self.calls = []
        self.mutate = mutate

    async def capture(self, descriptor, settings, asof, **kwargs):
        self.calls.append(copy.deepcopy((descriptor["slug"], settings, asof, kwargs)))
        data = captured(descriptor["slug"])
        if self.mutate and descriptor["slug"] == "cockpit":
            self.mutate(data)
        return data


class SyntheticRenderer:
    async def preflight(self):
        """No UI or browser behind this double; nothing to check."""

    async def render(self, envelope, snapshot_id, settings, target_id=None):
        return (
            {
                "status": "ready",
                "snapshot_id": snapshot_id,
                "settings": settings,
                "observation": {"text": "Synthetic exact visible provenance"},
                "targets": [
                    {
                        "id": "target-synthetic",
                        "text": "Synthetic exact visible provenance",
                        "links": [
                            {"id": "nav-synthetic", "label": "Open card", "kind": "fresh_capture"}
                        ],
                        "source_refs": [
                            {"id": "ref-synthetic", "label": "Source", "kind": "captured_json"}
                        ],
                    },
                    {"id": "target-other", "text": "Unrelated", "links": [], "source_refs": []},
                ],
            },
            b"png",
            {"asset_digest": "synthetic", "cropped": False},
        )


def service(**kwargs):
    return DashboardService(
        catalog=SyntheticCatalog(),
        capture=kwargs.pop("capture", SyntheticCapture()),
        renderer=kwargs.pop("renderer", SyntheticRenderer()),
        **kwargs,
    )


async def initial(svc):
    result, _ = await svc.call("read_dashboard", {"dashboard": "board"})
    assert result["ok"]
    return result["result"]["snapshot_id"]


def follow(snapshot_id):
    return {
        "from_snapshot_id": snapshot_id,
        "target_id": "target-synthetic",
        "link_id": "nav-synthetic",
        "freshness": "fresh",
    }


def test_fresh_follow_preserves_context_and_lineage_then_inspects_frozen_evidence():
    async def run():
        svc = service()
        parent = await initial(svc)
        child, _ = await svc.call("read_dashboard", follow(parent))
        assert child["ok"]
        result = child["result"]
        assert result["snapshot_id"] != parent and result["asof"] == "2026-10-30"
        assert result["origin"]["kind"] == "fresh_navigation"
        assert result["origin"]["consistency"] == "independent_capture"
        assert result["origin"]["parent_snapshot_id"] == parent
        assert svc.capture.calls[1] == (
            "cockpit",
            {"thesis_id": SUBJECT},
            "2026-10-30",
            {"expected_context_hash": context_hash(captured())},
        )
        source, _ = await svc.call(
            "inspect_dashboard_item",
            {
                "snapshot_id": result["snapshot_id"],
                "target_id": "target-synthetic",
                "source_ref_id": "ref-synthetic",
                "data_limit": 10,
            },
        )
        assert source["ok"] and source["result"]["origin"] == result["origin"]
        assert source["result"]["source_data"]["freshness"] == "frozen"
        assert source["result"]["source_data"]["next_offset"] == 10
        assert source["result"]["source_data"]["pointer"] == "/a~1b/~0key/1"
        assert source["result"]["source_data"]["category"] == "call_input"
        assert source["result"]["source_data"]["label"] == "Synthetic provenance"
        rendered, _ = await svc.call("render_dashboard", {"snapshot_id": result["snapshot_id"]})
        assert rendered["result"]["origin"] == result["origin"]
        assert len(svc.capture.calls) == 2
        raw, _ = await svc.call(
            "inspect_dashboard_item", {"snapshot_id": parent, "source_response_index": 1}
        )
        assert '"a/b"' in raw["result"]["source_data"]["text"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e["context"].update(clock="record"),
        lambda e: e["context"].update(countdown_origin="2026-11-02"),
        lambda e: e["context"]["clock_response"].update(market_today="2026-11-02"),
        lambda e: e.update(asof="2026-10-29"),
        lambda e: e["context"].update(known_at="2026-10-30T00:00:00Z"),
    ],
)
def test_follow_rejects_each_temporal_context_mismatch(change):
    async def run():
        svc = service(capture=SyntheticCapture(change))
        parent = await initial(svc)
        result, _ = await svc.call("read_dashboard", follow(parent))
        assert result["error"]["code"] == "context_changed"
        assert len(svc.store._items) == 1

    asyncio.run(run())


def test_follow_and_references_require_both_target_advertisement_and_envelope():
    async def run():
        svc = service()
        parent = await initial(svc)
        for target in ("target-other", "target-missing"):
            result, _ = await svc.call("read_dashboard", {**follow(parent), "target_id": target})
            assert not result["ok"]
            result, _ = await svc.call(
                "inspect_dashboard_item",
                {"snapshot_id": parent, "target_id": target, "source_ref_id": "ref-synthetic"},
            )
            assert not result["ok"]
        for field, name, args in (
            ("navigation", "read_dashboard", follow(parent)),
            (
                "references",
                "inspect_dashboard_item",
                {
                    "snapshot_id": parent,
                    "target_id": "target-synthetic",
                    "source_ref_id": "ref-synthetic",
                },
            ),
        ):
            snapshot = svc.store.get(parent)
            saved = snapshot.envelope[field]
            snapshot.envelope[field] = []
            result, _ = await svc.call(name, args)
            assert not result["ok"]
            snapshot.envelope[field] = saved
        assert len(svc.capture.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["before", "capture", "render"])
def test_parent_expiry_never_refreshes_or_returns_child(phase):
    async def run():
        now = [0]
        svc = service(store=SnapshotStore(ttl=10, clock=lambda: now[0]))
        parent = await initial(svc)
        if phase == "before":
            now[0] = 10
        elif phase == "capture":
            svc.capture.mutate = lambda data: now.__setitem__(0, 10)
        else:
            original = svc.renderer.render

            async def expire(*args):
                result = await original(*args)
                now[0] = 10
                return result

            svc.renderer.render = expire
        result, _ = await svc.call("read_dashboard", follow(parent))
        assert result["error"]["code"] == "snapshot_expired"
        assert not svc.store._items

    asyncio.run(run())


@pytest.mark.parametrize(
    "pointer,expected",
    [
        (
            "",
            {
                "a/b": {
                    "~key": [
                        None,
                        {
                            "detail": "Synthetic evidence exact 12.345",
                            "url": "https://example.invalid/reference-only",
                        },
                    ]
                }
            },
        ),
        ("/a~1b/~0key/0", None),
        ("/a~1b/~0key/1/detail", "Synthetic evidence exact 12.345"),
    ],
)
def test_pointer_escaping_root_null_and_exact_values(pointer, expected):
    assert pointer_value(captured()["responses"][1]["body"], pointer) == expected


@pytest.mark.parametrize(
    "pointer",
    [
        "missing",
        "/a~2b",
        "/a~1b/~0key/2",
        "/a~1b/~0key/-1",
        "/a~1b/~0key/01",
        "/a~1b/~0key/-",
        "/missing",
        "/" * 65,
    ],
)
def test_bad_pointer_fails_closed(pointer):
    with pytest.raises(AccessError):
        pointer_value(captured()["responses"][1]["body"], pointer)


def test_reference_limits_duplicate_ids_and_bad_indexes_reject_whole_capture():
    nullable = captured()
    nullable["references"][0].update(label=None, category=None)
    validate_references(nullable)
    for field in ("navigation", "references"):
        for mutate in (
            lambda e: e[field].append(copy.deepcopy(e[field][0])),
            lambda e: e.update({field: e[field] * 2049}),
            lambda e: e[field][0].update(id="x" * 257),
        ):
            data = captured()
            mutate(data)
            with pytest.raises(AccessError):
                validate_references(data)
    for index in (-1, 2, True):
        data = captured()
        data["references"][0]["response_index"] = index
        with pytest.raises(AccessError):
            validate_references(data)


def test_verbose_target_preserves_advertised_identifiers_before_text():
    data = {
        "text": "x" * 200000,
        "tables": [["x" * 20000] * 100] * 100,
        "id": "opaque-target",
        "links": [{"id": "opaque-link", "kind": "fresh_capture", "label": "Open"}],
        "source_refs": [{"id": "opaque-ref", "kind": "captured_json", "label": "Source"}],
    }
    page, truncation = target_preview([data], budget=16000)
    assert truncation["truncated"]
    assert page[0]["id"] == "opaque-target"
    assert page[0]["links"][0]["id"] == "opaque-link"
    assert page[0]["source_refs"][0]["id"] == "opaque-ref"


def test_all_verbose_targets_paginate_without_skipping_or_truncating_bindings():
    async def run():
        svc = service()
        snapshot_id = await initial(svc)
        targets = [
            {
                "text": "verbose " * 10000,
                "tables": [["x" * 10000] * 100],
                "id": str(index).zfill(256),
                "links": [{"id": "nav-synthetic", "kind": "fresh_capture", "label": "Open"}],
                "source_refs": [
                    {"id": "ref-synthetic", "kind": "captured_json", "label": "Source"}
                ],
            }
            for index in range(50)
        ]
        svc.store.get(snapshot_id).view["targets"] = targets
        visited, offset = [], 0
        while offset is not None:
            response, _ = await svc.call(
                "inspect_dashboard_item",
                {"snapshot_id": snapshot_id, "offset": offset, "limit": 50},
            )
            assert response["ok"]
            result = response["result"]
            assert result["targets"]
            visited.extend(target["id"] for target in result["targets"])
            # Listings leave out each reference's constant kind; every other field is whole.
            listed_refs = [
                {key: value for key, value in ref.items() if key != "kind"}
                for ref in targets[0]["source_refs"]
            ]
            assert all(
                target["links"] == targets[0]["links"] and target["source_refs"] == listed_refs
                for target in result["targets"]
            )
            offset = result["next_offset"]
        assert visited == [target["id"] for target in targets]

    asyncio.run(run())


def test_input_schema_exclusive_follow_and_reference_modes():
    read = Draft202012Validator(INPUT_SCHEMAS["read_dashboard"])
    assert read.is_valid({"dashboard": "board"}) and read.is_valid(follow("snapshot"))
    for args in (
        {**follow("snapshot"), "dashboard": "board"},
        {**follow("snapshot"), "asof": "2026-10-30"},
        {**follow("snapshot"), "settings": {}},
        {key: value for key, value in follow("snapshot").items() if key != "freshness"},
    ):
        assert not read.is_valid(args)
    inspect = Draft202012Validator(INPUT_SCHEMAS["inspect_dashboard_item"])
    assert inspect.is_valid({"snapshot_id": "s", "target_id": "t", "source_ref_id": "r"})
    assert not inspect.is_valid({"snapshot_id": "s", "source_ref_id": "r"})
    assert not inspect.is_valid(
        {"snapshot_id": "s", "target_id": "t", "source_ref_id": "r", "source_response_index": 0}
    )


def test_catalog_normalizes_uuid_settings_before_capture_and_render():
    supplied = {"thesis_id": "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF"}
    _, prepared = SyntheticCatalog().prepare("cockpit", supplied, "2026-10-30")
    assert prepared["thesis_id"] == "abcdefab-cdef-4abc-8def-abcdefabcdef"
    assert supplied["thesis_id"].startswith("ABCDEFAB")


def test_http_context_hash_and409_context_changed_are_bounded():
    async def run():
        expected = hashlib.sha256(
            json.dumps(
                {
                    "asof": "2026-10-30",
                    **{
                        key: captured()["context"][key]
                        for key in (
                            "clock_response",
                            "clock",
                            "known_at",
                            "decisions_asof",
                            "countdown_origin",
                            "scope",
                        )
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        assert context_hash(captured()) == expected

        async def handle(request):
            assert request.url.params["expected_context_hash"] == expected
            return httpx.Response(409, json={"detail": "context_changed"})

        client = CaptureClient("http://127.0.0.1:8001", transport=httpx.MockTransport(handle))
        descriptor, settings = SyntheticCatalog().prepare("board", {}, None)
        with pytest.raises(AccessError) as error:
            await client.capture(descriptor, settings, "2026-10-30", expected_context_hash=expected)
        assert error.value.code == "context_changed" and not error.value.retryable

    asyncio.run(run())


@pytest.mark.parametrize("field", list(captured()["context"]))
def test_context_hash_guards_every_context_field_except_limitations(field):
    base = captured()
    changed = copy.deepcopy(base)
    changed["context"][field] = {"synthetic_change": changed["context"][field]}
    unchanged = context_hash(changed) == context_hash(base)
    assert unchanged is (field == "limitations")


def test_context_hash_guards_an_unknown_context_field():
    base = captured()
    extended = copy.deepcopy(base)
    extended["context"]["synthetic_future_time_field"] = "2026-10-30"
    assert context_hash(extended) != context_hash(base)


def test_official_sdk_follows_only_advertised_link_and_reads_exact_frozen_source():
    async def run():
        backend = Path(__file__).resolve().parents[2]
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                "import asyncio; from test_navigation import service; from agent_access.server import run; asyncio.run(run(service()))",
            ],
            cwd=backend,
            env={"PYTHONPATH": os.pathsep.join((str(Path(__file__).parent), str(backend)))},
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=20) as client:
                await client.initialize()
                initial_result = await client.call_tool("read_dashboard", {"dashboard": "board"})
                parent = initial_result.structured_content["result"]
                target = next(target for target in parent["targets"] if target.get("links"))
                result = await client.call_tool(
                    "read_dashboard",
                    {
                        "from_snapshot_id": parent["snapshot_id"],
                        "target_id": target["id"],
                        "link_id": target["links"][0]["id"],
                        "freshness": "fresh",
                    },
                )
                assert not result.is_error
                child = result.structured_content["result"]
                target = next(target for target in child["targets"] if target.get("source_refs"))
                source = await client.call_tool(
                    "inspect_dashboard_item",
                    {
                        "snapshot_id": child["snapshot_id"],
                        "target_id": target["id"],
                        "source_ref_id": target["source_refs"][0]["id"],
                    },
                )
                assert not source.is_error
                data = source.structured_content["result"]
                assert (
                    json.loads(data["source_data"]["text"])["detail"]
                    == "Synthetic evidence exact 12.345"
                )
                assert data["origin"]["parent_snapshot_id"] == parent["snapshot_id"]
                bad = await client.call_tool(
                    "read_dashboard",
                    {
                        "from_snapshot_id": parent["snapshot_id"],
                        "target_id": target["id"],
                        "link_id": "unadvertised",
                        "freshness": "fresh",
                    },
                )
                assert bad.is_error and bad.structured_content["error"]["code"] == "unknown_link"

    asyncio.run(run())

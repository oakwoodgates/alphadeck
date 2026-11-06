"""Lean inspect replies, pointer/outline reads, split capture errors, and the trace size line.

Synthetic data only; no DB, backend, or browser.
"""

import asyncio
import copy
import json
import logging

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
pytest.importorskip("playwright")

import httpx  # noqa: E402
from jsonschema import validate  # noqa: E402

from agent_access.capture import CaptureClient  # noqa: E402
from agent_access.catalog import Catalog  # noqa: E402
from agent_access.references import context_hash  # noqa: E402
from agent_access.server import OUTPUT_SCHEMA, log_reply, output_schema  # noqa: E402
from agent_access.service import DashboardService  # noqa: E402

CLOCK = {
    "current_night": "2026-10-30",
    "action_day": "2026-11-02",
    "market_today": "2026-11-01",
    "market_tz": "America/New_York",
}
CALL = {
    "state": "Armed",
    "verdict": "Go",
    "missing": [],
    "armed_members": [{"ticker": "AAA"}, {"ticker": "BBB"}],
    "note": "x" * 50,
    "count": 3,
    "flag": True,
    "none": None,
    "a/b": 1,
}


def envelope():
    return {
        "schema_version": "1",
        "dashboard": "board",
        "asof": "2026-10-30",
        "captured_at": "2026-11-01T00:00:00Z",
        "context": {
            "scope": "deployment_visibility_not_authorization",
            "clock": "public",
            "known_at": "2026-10-31T00:00:00Z",
            "decisions_asof": "2026-10-30",
            "countdown_origin": "2026-10-30",
            "clock_response": dict(CLOCK),
        },
        "consistency": {
            "database": "repeatable_read_read_only",
            "generation_stable": True,
            "generation_before": "g1",
            "generation_after": "g1",
        },
        "responses": [
            {"method": "GET", "path": "/clock", "query": {}, "status": 200, "body": dict(CLOCK)},
            {
                "method": "GET",
                "path": "/theses/1/call",
                "query": {},
                "status": 200,
                "body": copy.deepcopy(CALL),
            },
            {
                "method": "GET",
                "path": "/theses",
                "query": {},
                "status": 200,
                "body": [{"id": index, "name": "card " + str(index)} for index in range(450)],
            },
        ],
    }


class FakeCapture:
    async def capture(self, descriptor, settings, asof):
        return envelope()


class FakeRenderer:
    async def preflight(self):
        pass

    async def render(self, capture, snapshot_id, settings, target_id=None):
        return (
            {
                "status": "ready",
                "snapshot_id": snapshot_id,
                "settings": settings,
                "observation": {"text": "label"},
                "targets": [
                    {"id": "t" + str(index), "label": "T", "text": "v", "visible": True}
                    for index in range(3)
                ],
            },
            b"png",
            {"cropped": False},
        )


async def read_snapshot(service):
    payload, _ = await service.call("read_dashboard", {"dashboard": "board"})
    assert payload["ok"], payload
    return payload["result"]["snapshot_id"]


async def inspect(service, snapshot_id, **arguments):
    payload, _ = await service.call(
        "inspect_dashboard_item", {"snapshot_id": snapshot_id, **arguments}
    )
    validate(payload, OUTPUT_SCHEMA)
    return payload


def run_inspect(arguments_list):
    async def run():
        service = DashboardService(capture=FakeCapture(), renderer=FakeRenderer())
        snapshot_id = await read_snapshot(service)
        return [await inspect(service, snapshot_id, **arguments) for arguments in arguments_list]

    return asyncio.run(run())


def test_schema_version_is_three_everywhere():
    assert OUTPUT_SCHEMA["properties"]["schema_version"] == {"const": "3"}
    (read,) = [asyncio.run(_read())]
    assert read["schema_version"] == "3"


async def _read():
    payload, _ = await DashboardService(capture=FakeCapture(), renderer=FakeRenderer()).call(
        "read_dashboard", {"dashboard": "board"}
    )
    return payload


def test_data_read_defaults_to_no_target_page_or_response_listing():
    default, explicit, paged = run_inspect(
        [
            {"source_response_index": 1},
            {"source_response_index": 1, "limit": 2, "source_limit": 2},
            {},
        ]
    )
    result = default["result"]
    assert result["targets"] == [] and result["total"] == 3
    assert result["source_responses"] == [] and result["source_total"] == 3
    assert result["next_offset"] is None and result["next_source_offset"] is None
    assert result["source_data"]["total_characters"] == len(
        json.dumps(CALL, ensure_ascii=True, separators=(",", ":"))
    )
    # Passing them explicitly still pages them.
    assert len(explicit["result"]["targets"]) == 2 and explicit["result"]["next_offset"] == 2
    assert len(explicit["result"]["source_responses"]) == 2
    assert explicit["result"]["next_source_offset"] == 2
    # A target-only call keeps its old defaults.
    assert len(paged["result"]["targets"]) == 3 and len(paged["result"]["source_responses"]) == 3


def test_explicit_zero_is_accepted_in_every_mode():
    (zero,) = run_inspect([{"limit": 0, "source_limit": 0}])
    result = zero["result"]
    assert result["targets"] == [] and result["total"] == 3
    assert result["source_responses"] == [] and result["source_total"] == 3
    assert "source_data" not in result


def test_inspect_header_is_compact_and_hashes_the_context():
    (inspected,) = run_inspect([{"source_response_index": 0}])
    result = inspected["result"]
    assert "context" not in result and "consistency" not in result
    assert result["context_hash"] == context_hash(envelope())
    assert {"snapshot_id", "dashboard", "asof", "captured_at", "expires_at", "settings"} <= set(
        result
    )
    assert set(output_schema("inspect_dashboard_item")["properties"]["result"]["required"]) <= set(
        result
    )


def test_read_dashboard_keeps_the_full_context():
    result = asyncio.run(_read())["result"]
    assert result["context"]["clock"] == "public" and "consistency" in result


def test_pointer_reads_one_value_chunked_and_echoes_the_pointer():
    scalar, nested, chunk, whole, escaped = run_inspect(
        [
            {"source_response_index": 1, "pointer": "/verdict"},
            {"source_response_index": 1, "pointer": "/armed_members/1"},
            {"source_response_index": 1, "pointer": "/note", "data_limit": 10},
            {"source_response_index": 1, "pointer": ""},
            {"source_response_index": 1, "pointer": "/a~1b"},
        ]
    )
    assert scalar["result"]["source_data"]["text"] == '"Go"'
    assert scalar["result"]["source_data"]["pointer"] == "/verdict"
    assert nested["result"]["source_data"]["text"] == '{"ticker":"BBB"}'
    data = chunk["result"]["source_data"]
    assert data["truncated"] and data["next_offset"] == 10 and data["total_characters"] == 52
    assert json.loads(whole["result"]["source_data"]["text"]) == CALL
    assert escaped["result"]["source_data"]["text"] == "1"


def test_unknown_pointer_and_pointer_with_reference_are_rejected():
    unknown, bad_index, with_reference = run_inspect(
        [
            {"source_response_index": 1, "pointer": "/nope"},
            {"source_response_index": 1, "pointer": "/armed_members/9"},
            {"source_ref_id": "r", "target_id": "t0", "pointer": "/state"},
        ]
    )
    for payload in (unknown, bad_index):
        assert not payload["ok"] and payload["error"]["code"] == "unknown_pointer"
        assert payload["error"]["retryable"] is False and "outline" in payload["error"]["message"]
    assert with_reference["error"]["code"] == "invalid_arguments"


def test_pointer_alone_is_invalid():
    (alone,) = run_inspect([{"pointer": "/state"}])
    assert alone["error"]["code"] == "invalid_arguments"


def test_outline_of_object_array_and_scalar_has_no_values():
    obj, arr, scalar, nested = run_inspect(
        [
            {"source_response_index": 1, "outline": True},
            {"source_response_index": 1, "pointer": "/armed_members", "outline": True},
            {"source_response_index": 1, "pointer": "/count", "outline": True},
            {"source_response_index": 1, "pointer": "/armed_members/0", "outline": True},
        ]
    )
    data = obj["result"]["source_data"]
    assert data["encoding"] == "json_outline" and data["type"] == "object"
    assert data["total_characters"] == len(json.dumps(CALL, separators=(",", ":")))
    by_key = {entry["key"]: entry for entry in data["entries"]}
    assert by_key["state"] == {"key": "state", "type": "string", "characters": 7}
    assert by_key["armed_members"]["type"] == "array"
    assert by_key["count"]["type"] == "number" and by_key["flag"]["type"] == "boolean"
    assert by_key["none"] == {"key": "none", "type": "null", "characters": 4}
    assert "Armed" not in json.dumps(data) and "text" not in data
    assert data["next_outline_offset"] is None and data["outline_total"] == len(CALL)
    items = arr["result"]["source_data"]
    assert items["type"] == "array" and items["pointer"] == "/armed_members"
    assert [entry["key"] for entry in items["entries"]] == [0, 1]
    assert items["entries"][0] == {"key": 0, "type": "object", "characters": 16}
    leaf = scalar["result"]["source_data"]
    assert leaf["type"] == "number" and leaf["total_characters"] == 1 and "entries" not in leaf
    assert [entry["key"] for entry in nested["result"]["source_data"]["entries"]] == ["ticker"]


def test_outline_pages_two_hundred_at_a_time():
    first, second, third = run_inspect(
        [
            {"source_response_index": 2, "outline": True},
            {"source_response_index": 2, "outline": True, "outline_offset": 200},
            {"source_response_index": 2, "outline": True, "outline_offset": 400},
        ]
    )
    one, two, three = (item["result"]["source_data"] for item in (first, second, third))
    assert len(one["entries"]) == 200 and one["next_outline_offset"] == 200
    assert one["outline_total"] == 450 and one["entries"][0]["key"] == 0
    assert two["entries"][0]["key"] == 200 and two["next_outline_offset"] == 400
    assert len(three["entries"]) == 50 and three["next_outline_offset"] is None
    assert three["entries"][-1]["key"] == 449


def test_outline_on_source_ref_id_and_outline_without_a_source():
    async def run():
        class RefCapture(FakeCapture):
            async def capture(self, descriptor, settings, asof):
                data = envelope()
                data["references"] = [
                    {
                        "id": "call",
                        "kind": "captured_json",
                        "response_index": 1,
                        "pointer": "/armed_members",
                        "category": "call_result",
                        "label": "Armed",
                    }
                ]
                return data

        class RefRenderer(FakeRenderer):
            async def render(self, capture, snapshot_id, settings, target_id=None):
                view, png, metadata = await super().render(capture, snapshot_id, settings)
                view["targets"][0]["source_refs"] = [{"id": "call", "kind": "captured_json"}]
                return view, png, metadata

        service = DashboardService(capture=RefCapture(), renderer=RefRenderer())
        snapshot_id = await read_snapshot(service)
        outlined = await inspect(
            service, snapshot_id, target_id="t0", source_ref_id="call", outline=True
        )
        read = await inspect(service, snapshot_id, target_id="t0", source_ref_id="call")
        bare = await inspect(service, snapshot_id, outline=True)
        return outlined, read, bare

    outlined, read, bare = asyncio.run(run())
    data = outlined["result"]["source_data"]
    assert data["type"] == "array" and data["reference_id"] == "call" and len(data["entries"]) == 2
    assert outlined["result"]["targets"] == []
    assert read["result"]["source_data"]["text"] == '[{"ticker":"AAA"},{"ticker":"BBB"}]'
    assert bare["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize(
    "error, code, message",
    [
        (
            httpx.ConnectError("refused"),
            "backend_unavailable",
            "http://127.0.0.1:8001 isn't answering",
        ),
        (
            httpx.ConnectTimeout("slow"),
            "backend_unavailable",
            "http://127.0.0.1:8001 isn't answering",
        ),
        (httpx.ReadTimeout("slow"), "capture_timeout", "within 130 seconds"),
        (httpx.WriteTimeout("slow"), "capture_timeout", "120 seconds"),
        (httpx.PoolTimeout("slow"), "capture_timeout", "Retry later"),
        (
            httpx.RemoteProtocolError("odd"),
            "backend_unavailable",
            "http://127.0.0.1:8001 dropped the capture connection",
        ),
    ],
)
def test_capture_transport_errors_are_split(error, code, message):
    async def handle(request):
        raise error

    async def run():
        descriptor, settings = Catalog().prepare("board", {}, None)
        client = CaptureClient("http://127.0.0.1:8001", transport=httpx.MockTransport(handle))
        with pytest.raises(Exception) as caught:
            await client.capture(descriptor, settings, None)
        return caught.value

    caught = asyncio.run(run())
    assert caught.code == code and caught.retryable is True and message in caught.message


def test_capture_timeout_message_uses_the_configured_timeout():
    async def handle(request):
        raise httpx.ReadTimeout("slow")

    async def run():
        descriptor, settings = Catalog().prepare("board", {}, None)
        client = CaptureClient(
            "http://127.0.0.1:8001", timeout=45, transport=httpx.MockTransport(handle)
        )
        with pytest.raises(Exception) as caught:
            await client.capture(descriptor, settings, None)
        return caught.value

    assert "within 45 seconds" in asyncio.run(run()).message


def test_trace_logs_reply_size_in_characters_never_content(caplog):
    payload = {"schema_version": "3", "request_id": "r", "ok": True, "result": {"secret": "zzz"}}
    with caplog.at_level(logging.INFO, logger="agent_access.server"):
        log_reply("inspect_dashboard_item", payload)
    expected = len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    assert f"tool=inspect_dashboard_item ok=True chars={expected}" in caplog.text
    assert "zzz" not in caplog.text

"""Batch reads of advertised references, lean target listings and the read page budget.

Synthetic data only; no DB, backend, or browser.
"""

import asyncio
import copy
import json

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

from board_scale import envelope as board_envelope_at_scale  # noqa: E402
from board_scale import scale_service  # noqa: E402
from board_scale import view as scale_view  # noqa: E402
from jsonschema import Draft202012Validator, validate  # noqa: E402
from test_board_references import BoardCapture, BoardRenderer, board_envelope  # noqa: E402

from agent_access.catalog import Catalog  # noqa: E402
from agent_access.server import INPUT_SCHEMAS, output_schema  # noqa: E402
from agent_access.service import DashboardService  # noqa: E402

BOARD_SETTINGS = {"dq_open": True, "archived_open": False}


def compact(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


async def call(service, tool, **arguments):
    payload, _ = await service.call(tool, arguments)
    validate(payload, output_schema(tool))
    return payload


def board_service():
    envelope, a, c = board_envelope()
    renderer = BoardRenderer(envelope, a, c)
    service = DashboardService(catalog=Catalog(), capture=BoardCapture(envelope), renderer=renderer)
    return service, envelope, renderer


def test_listings_omit_the_constant_reference_kind_and_resolution_still_works():
    service, envelope, renderer = board_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        paged = (await call(service, "inspect_dashboard_item", snapshot_id=read["snapshot_id"]))[
            "result"
        ]
        for page in (read["targets"], paged["targets"]):
            refs = [ref for target in page for ref in target["source_refs"]]
            assert refs and all(set(ref) == {"id", "label", "category"} for ref in refs)
        # The stored target keeps `kind`; resolving a listed ID reads the stored target.
        stored = renderer.targets[0]["source_refs"]
        assert all(ref["kind"] == "captured_json" for ref in stored)
        one = await call(
            service,
            "inspect_dashboard_item",
            snapshot_id=read["snapshot_id"],
            target_id="target-queue-a",
            source_ref_id=read["targets"][0]["source_refs"][1]["id"],
        )
        assert one["ok"] and one["result"]["source_data"]["kind"] == "captured_json"

    asyncio.run(run())


def test_batch_returns_each_reference_in_request_order_like_single_reads():
    service, envelope, _ = board_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = read["snapshot_id"]
        refs = read["targets"][0]["source_refs"]
        ids = [ref["id"] for ref in reversed(refs)]
        batch = await call(
            service,
            "inspect_dashboard_item",
            snapshot_id=snapshot,
            target_id="target-queue-a",
            source_ref_ids=ids,
        )
        assert batch["ok"], batch
        result = batch["result"]
        # A data read: no target page and no response listing unless asked.
        assert result["targets"] == [] and result["source_responses"] == []
        assert "source_data" not in result
        items = result["source_data_items"]
        assert [item["reference_id"] for item in items] == ids
        for item in items:
            assert set(item) == {
                "reference_id",
                "category",
                "label",
                "pointer",
                "response_index",
                "path",
                "text",
                "total_characters",
                "truncated",
                "next_offset",
            }
            single = (
                await call(
                    service,
                    "inspect_dashboard_item",
                    snapshot_id=snapshot,
                    target_id="target-queue-a",
                    source_ref_id=item["reference_id"],
                )
            )["result"]["source_data"]
            assert item["text"] == single["text"]
            assert item["total_characters"] == single["total_characters"]
            assert (item["pointer"], item["category"], item["label"]) == (
                single["pointer"],
                single["category"],
                single["label"],
            )
            assert (item["response_index"], item["path"]) == (
                single["response_index"],
                single["path"],
            )
            assert item["truncated"] is False and item["next_offset"] is None
        by_label = {item["label"]: item for item in items}
        assert json.loads(by_label["verdict"]["text"]) == "starter_entry"
        assert by_label["theses[0]"]["category"] == "thesis_summary"

    asyncio.run(run())


def big_envelope(count=32, size=9000):
    """One response of `count` large string values (quotes inflate their encoding), each referenced."""
    envelope, _, _ = board_envelope()
    body = {f"k{n}": f'"{n}"' * (size // 3) for n in range(count)}
    envelope["responses"].append(
        {"method": "GET", "path": "/data-flags", "query": {"x": "big"}, "status": 200, "body": body}
    )
    index = len(envelope["responses"]) - 1
    refs = [
        {
            "id": f"ref_big_{n}",
            "kind": "captured_json",
            "response_index": index,
            "pointer": f"/k{n}",
            "category": "data_flag",
            "label": f"big {n}",
        }
        for n in range(count)
    ]
    envelope["references"] = envelope["references"] + refs
    return envelope, body, refs


class BigRenderer:
    def __init__(self, refs):
        self.refs = refs

    async def preflight(self):
        """No UI or browser behind this double."""

    async def render(self, envelope, snapshot_id, settings, target_id=None):
        target = {"id": "target-big", "text": "big", "source_refs": copy.deepcopy(self.refs)}
        view = {"status": "ready", "observation": {"text": "Board"}, "targets": [target]}
        return view, b"png", {"asset_digest": "synthetic", "cropped": False}


def test_a_cut_item_continues_with_source_ref_id_and_the_reply_stays_bounded():
    envelope, body, refs = big_envelope()
    service = DashboardService(
        catalog=Catalog(), capture=BoardCapture(envelope), renderer=BigRenderer(refs)
    )

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = read["snapshot_id"]
        batch = await call(
            service,
            "inspect_dashboard_item",
            snapshot_id=snapshot,
            target_id="target-big",
            source_ref_ids=[ref["id"] for ref in refs],
        )
        assert batch["ok"], batch
        items = batch["result"]["source_data_items"]
        assert len(items) == 32
        assert compact(batch) <= service.reply_ceiling
        first = items[0]
        assert len(first["text"]) == 4000 and first["truncated"]
        assert first["next_offset"] == 4000
        # Every item is present; the budget cuts later texts, each stating where to continue.
        assert all(item["truncated"] for item in items)
        assert any(item["text"] == "" and item["next_offset"] == 0 for item in items)
        for item in (first, items[-1]):
            parts, offset = [item["text"]], item["next_offset"]
            while offset is not None:
                data = (
                    await call(
                        service,
                        "inspect_dashboard_item",
                        snapshot_id=snapshot,
                        target_id="target-big",
                        source_ref_id=item["reference_id"],
                        data_offset=offset,
                    )
                )["result"]["source_data"]
                assert data["offset"] == offset
                parts.append(data["text"])
                offset = data["next_offset"]
            key = item["pointer"][1:]
            assert json.loads("".join(parts)) == body[key]

    asyncio.run(run())


def test_one_unadvertised_reference_fails_the_whole_batch():
    service, _, _ = board_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = read["snapshot_id"]
        mine = [ref["id"] for ref in read["targets"][0]["source_refs"]]
        c_only = {ref["id"] for ref in read["targets"][2]["source_refs"]} - set(mine)
        outcomes = []
        for ids in (mine[:2] + ["ref_absent"], mine[:2] + sorted(c_only)[:1]):
            outcomes.append(
                await call(
                    service,
                    "inspect_dashboard_item",
                    snapshot_id=snapshot,
                    target_id="target-queue-a",
                    source_ref_ids=ids,
                )
            )
        return outcomes

    for payload in asyncio.run(run()):
        assert not payload["ok"] and payload["error"]["code"] == "unknown_reference"
        assert "result" not in payload


BAD_BATCHES = [
    {"source_ref_ids": ["r"]},  # no target_id
    {"target_id": "t", "source_ref_ids": []},
    {"target_id": "t", "source_ref_ids": ["r", "r"]},
    {"target_id": "t", "source_ref_ids": [f"r{n}" for n in range(33)]},
    {"target_id": "t", "source_ref_ids": ["r"], "source_ref_id": "r"},
    {"target_id": "t", "source_ref_ids": ["r"], "source_response_index": 1},
    {"target_id": "t", "source_ref_ids": ["r"], "pointer": "/a"},
    {"target_id": "t", "source_ref_ids": ["r"], "outline": True},
    {"target_id": "t", "source_ref_ids": ["r"], "outline": False},
    {"target_id": "t", "source_ref_ids": ["r"], "outline_offset": 0},
    {"target_id": "t", "source_ref_ids": ["r"], "data_offset": 0},
    {"target_id": "t", "source_ref_ids": ["r"], "data_limit": 100},
]


@pytest.mark.parametrize("arguments", BAD_BATCHES)
def test_batch_argument_rules_hold_in_the_schema_and_the_service(arguments):
    arguments = {"snapshot_id": "s", **arguments}
    assert not Draft202012Validator(INPUT_SCHEMAS["inspect_dashboard_item"]).is_valid(arguments)
    service, _, _ = board_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        payload, _ = await service.call(
            "inspect_dashboard_item", {**arguments, "snapshot_id": read["snapshot_id"]}
        )
        return payload

    payload = asyncio.run(run())
    assert payload["error"]["code"] == "invalid_arguments"


def test_a_valid_batch_passes_the_input_schema():
    arguments = {"snapshot_id": "s", "target_id": "t", "source_ref_ids": ["a", "b"], "limit": 0}
    assert Draft202012Validator(INPUT_SCHEMAS["inspect_dashboard_item"]).is_valid(arguments)


@pytest.mark.parametrize("ceiling, first_page", [(40_000, 15), (20_000, 5)])
def test_real_scale_board_pages_hold_the_targets_under_the_reply_ceiling(ceiling, first_page):
    """On the 66-card / 42-armed fixture every page fits the ceiling, never drops or repeats a
    target, and holds what fits even when that is fewer than the limit asked for."""
    service = scale_service(ceiling)

    async def run():
        read = await call(service, "read_dashboard", dashboard="board", settings=BOARD_SETTINGS)
        assert read["ok"], read
        result = read["result"]
        assert result["target_total"] == 108
        assert len(result["targets"]) >= first_page and compact(read) <= ceiling
        ids, offset = [t["id"] for t in result["targets"]], result["next_target_offset"]
        while offset is not None:
            page = await call(
                service,
                "inspect_dashboard_item",
                snapshot_id=result["snapshot_id"],
                offset=offset,
                limit=50,
            )
            assert compact(page) <= ceiling and page["result"]["targets"]
            assert page["result"]["target_shape"] == "listing"
            ids += [t["id"] for t in page["result"]["targets"]]
            offset = page["result"]["next_offset"]
        return ids

    ids = asyncio.run(run())
    data = board_envelope_at_scale()
    assert ids == [target["id"] for target in scale_view(data)["targets"]]


def test_listings_are_lean_and_a_target_id_opens_the_full_record():
    service = scale_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = read["snapshot_id"]
        listed = read["targets"][0]
        record = (
            await call(
                service, "inspect_dashboard_item", snapshot_id=snapshot, target_id=listed["id"]
            )
        )["result"]
        return listed, record

    listed, record = asyncio.run(run())
    assert set(listed) == {
        "id",
        "label",
        "text",
        "text_total_characters",
        "visible",
        "clipped",
        "in_viewport",
        "links",
        "source_refs",
    }
    assert len(listed["text"]) <= 200
    assert record["target_shape"] == "record" and record["next_offset"] is None
    (full,) = record["targets"]
    assert {"bounds", "tables", "reference_links"} <= set(full)
    assert full["source_refs"] == listed["source_refs"] and full["links"] == listed["links"]
    # A target-only call lists no captured responses by default.
    assert record["source_responses"] == [] and record["next_source_offset"] is None

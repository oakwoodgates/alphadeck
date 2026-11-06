"""The reply ceiling: its configuration bounds and the guarantees every handler keeps under it.

Synthetic data only; no DB, backend, or browser.
"""

import asyncio
import json
import sys

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

from board_scale import envelope as board_envelope  # noqa: E402
from board_scale import scale_service  # noqa: E402
from jsonschema import validate  # noqa: E402

from agent_access import server  # noqa: E402
from agent_access.errors import AccessError  # noqa: E402
from agent_access.server import output_schema  # noqa: E402
from agent_access.service import (  # noqa: E402
    DEFAULT_REPLY_CEILING,
    MAX_REPLY_CEILING,
    MIN_REPLY_CEILING,
    DashboardService,
)

CLOCK = {
    "current_night": "2026-10-30",
    "action_day": "2026-11-02",
    "market_today": "2026-11-01",
    "market_tz": "America/New_York",
}


def compact(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


async def call(service, tool, **arguments):
    payload, _ = await service.call(tool, arguments)
    assert compact(payload) <= service.reply_ceiling
    validate(payload, output_schema(tool))
    return payload


# --- configuration ---------------------------------------------------------------------------


def test_the_default_ceiling_is_forty_thousand_characters():
    assert DEFAULT_REPLY_CEILING == 40_000
    assert (MIN_REPLY_CEILING, MAX_REPLY_CEILING) == (16_000, 120_000)
    assert DashboardService(capture=object(), renderer=object()).reply_ceiling == 40_000


@pytest.mark.parametrize("value", ["15999", "120001", "0", "-40000"])
def test_a_reply_ceiling_outside_its_bounds_refuses_to_start(value, monkeypatch, capsys):
    started = []

    async def run(service):
        started.append(service)

    monkeypatch.setattr(server, "run", run)
    monkeypatch.setattr(sys, "argv", ["alphadeck-mcp", "--reply-ceiling", value])
    with pytest.raises(SystemExit) as exited:
        server.main()
    assert exited.value.code == 2 and not started
    assert "Reply ceiling must be between 16000 and 120000 characters." in capsys.readouterr().err


@pytest.mark.parametrize("value", [16_000, 20_000, 120_000])
def test_a_reply_ceiling_inside_its_bounds_reaches_the_service(value, monkeypatch):
    started = []

    async def run(service):
        started.append(service)

    monkeypatch.setattr(server, "run", run)
    monkeypatch.setattr(sys, "argv", ["alphadeck-mcp", "--reply-ceiling", str(value)])
    server.main()
    assert [service.reply_ceiling for service in started] == [value]


@pytest.mark.parametrize("value", [15_999, 120_001, 40_000.0, True])
def test_the_service_rejects_a_ceiling_outside_its_bounds(value):
    with pytest.raises(AccessError) as error:
        DashboardService(capture=object(), renderer=object(), reply_ceiling=value)
    assert error.value.code == "invalid_configuration"


def test_tool_descriptions_state_the_configured_ceiling():
    descriptions = server.tool_descriptions(20_000)
    assert descriptions.keys() == server.INPUT_SCHEMAS.keys()
    for name in ("read_dashboard", "inspect_dashboard_item", "list_dashboards"):
        assert "reply ceiling (20000 characters)" in descriptions[name]
    assert not any("{ceiling}" in text for text in descriptions.values())
    for name in ("view_pointer", "index=true", "match="):
        assert name in descriptions["inspect_dashboard_item"]
    assert "SUMMARY" in descriptions["read_dashboard"]


def test_an_over_ceiling_reply_is_output_too_large_naming_the_paging_knobs():
    class Wordy(DashboardService):
        async def describe_dashboard(self, dashboard):
            return {"dashboard": {"notes": "x" * 50_000}, "policy": {}}, None

    payload, _ = asyncio.run(
        Wordy(capture=object(), renderer=object()).call("describe_dashboard", {"dashboard": "b"})
    )
    assert not payload["ok"] and payload["error"]["code"] == "output_too_large"
    message = payload["error"]["message"]
    assert "40000-character reply ceiling" in message
    assert all(
        knob in message for knob in ("limit", "data_limit", "outline_offset", "view_pointer")
    )


# --- read_dashboard never fails on size after a capture ----------------------------------------


def oversized_envelope() -> dict:
    return {
        "schema_version": "1",
        "dashboard": "board",
        "asof": "2026-10-30",
        "captured_at": "2026-11-01T00:00:00Z",
        "context": {
            "scope": "deployment_visibility_not_authorization",
            "clock": "record",
            "known_at": None,
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
            *(
                {
                    "method": "GET",
                    "path": f"/theses/{n}/call",
                    "query": {"asof": "2026-10-30"},
                    "status": 200,
                    "body": {"verdict": "not_yet", "padding": "p" * 3000},
                }
                for n in range(300)
            ),
        ],
    }


def oversized_target(name: str, refs: int) -> dict:
    return {
        "id": "target-" + name,
        "label": "Synthetic " + name,
        "text": "· synthetic words \n" * 600,
        "total_characters": 90_000,
        "visible": True,
        "clipped": False,
        "in_viewport": True,
        "bounds": {"x": 1.5, "y": 2.5, "width": 3.5, "height": 4.5},
        "tables": [{"headers": ["h"] * 40, "rows": [["c" * 40] * 40] * 120, "row_count": 120}],
        "links": [],
        "source_refs": [
            {
                "id": f"ref_{name}_{k:04d}",
                "kind": "captured_json",
                "label": "Synthetic reference label " + "L" * 220,
                "category": "call_input",
            }
            for k in range(refs)
        ],
        "reference_links": [{"label": "x", "url": "https://example.invalid/" + "u" * 80}] * 100,
    }


class OversizedCapture:
    async def capture(self, descriptor, settings, asof, **kwargs):
        return oversized_envelope()


class OversizedRenderer:
    async def preflight(self):
        """No UI or browser behind this double."""

    async def render(self, envelope, snapshot_id, settings, target_id=None):
        view = {
            "status": "ready",
            "observation": {
                "title": "Synthetic oversized dashboard " + "T" * 400,
                "text": "· rendered words\n" * 700,
                "truncated": True,
                "total_characters": 250_000,
                "tables": [{"headers": ["header " + "H" * 60] * 60, "rows": [], "row_count": 9}]
                * 30,
                "notes": [f"Synthetic note {k}: " + "n" * 400 for k in range(1000)],
                "note_count": 1200,
                "notes_truncated": True,
                "annotations": [{"text": "a" * 400}] * 1000,
                "annotation_count": 1000,
                "charts": [{"paths": [{"d": "M0,0" + "L1,1" * 5000}]}] * 50,
                "controls": [{"label": "c" * 100}] * 500,
                "target_count": 3,
            },
            # The first target's references alone exceed any ceiling; the others are ordinary.
            "targets": [
                oversized_target("huge", 400),
                oversized_target("small-a", 2),
                oversized_target("small-b", 2),
            ],
        }
        return view, b"png", {"asset_digest": "synthetic", "cropped": False}


@pytest.mark.parametrize("ceiling", [MIN_REPLY_CEILING, 20_000, DEFAULT_REPLY_CEILING])
def test_read_dashboard_never_fails_on_size_after_capturing(ceiling):
    service = DashboardService(
        capture=OversizedCapture(), renderer=OversizedRenderer(), reply_ceiling=ceiling
    )

    async def run():
        read = await call(service, "read_dashboard", dashboard="board", target_limit=50)
        assert read["ok"], read
        result = read["result"]
        snapshot = result["snapshot_id"]
        frozen = service.store.get(snapshot).view
        # Nothing fits after the first target's bindings: no targets, the SAME offset, and why.
        assert result["targets"] == [] and result["next_target_offset"] == 0
        assert "offset=0" in result["targets_truncation"]["note"]
        summary = result["observation"]
        notes = frozen["observation"]["notes"]
        assert summary["notes"] == notes[: len(summary["notes"])] and summary["notes"]
        assert summary["next_note_index"] == len(summary["notes"]) and summary["note_count"] == 1200
        assert {"text", "notes"} <= set(summary["capture_truncated"])
        assert frozen["observation"]["text"].startswith(summary["text"])
        assert summary["text_total_characters"] == 250_000
        assert result["source_total"] == 301 and result["next_source_offset"] is not None
        # The listing continues past the oversized target with a stub that names it.
        page = (await call(service, "inspect_dashboard_item", snapshot_id=snapshot, offset=0))[
            "result"
        ]
        stub = page["targets"][0]
        assert stub["id"] == "target-huge" and stub["bindings_omitted"] is True
        assert page["next_offset"] is not None and page["next_offset"] >= 1
        # Its bindings read whole through view_pointer, chunk by chunk.
        parts, offset = [], 0
        while offset is not None:
            chunk = (
                await call(
                    service,
                    "inspect_dashboard_item",
                    snapshot_id=snapshot,
                    target_id="target-huge",
                    view_pointer="/source_refs",
                    data_offset=offset,
                )
            )["result"]["view_data"]
            assert chunk["scope"] == "target" and chunk["offset"] == offset
            parts.append(chunk["text"])
            offset = chunk["next_offset"]
        assert json.loads("".join(parts)) == frozen["targets"][0]["source_refs"]
        # The next note continues from the summary through view_pointer.
        note = (
            await call(
                service,
                "inspect_dashboard_item",
                snapshot_id=snapshot,
                view_pointer=f"/notes/{summary['next_note_index']}",
            )
        )["result"]["view_data"]
        assert json.loads(note["text"]) == notes[summary["next_note_index"]]
        # The full record of the oversized target is a stub too; one ordinary target opens whole
        # bindings with its DOM details cut to fit.
        record = (
            await call(
                service, "inspect_dashboard_item", snapshot_id=snapshot, target_id="target-small-a"
            )
        )["result"]
        (opened,) = record["targets"]
        assert len(opened["source_refs"]) == 2 and record["truncation"]["truncated"]
        assert all(path.startswith("/") for path in record["truncation"]["paths"])

    asyncio.run(run())


def test_a_listing_page_is_lean_whatever_the_target_holds():
    service = DashboardService(capture=OversizedCapture(), renderer=OversizedRenderer())

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        page = await call(
            service, "inspect_dashboard_item", snapshot_id=read["snapshot_id"], offset=1
        )
        return page["result"]

    page = asyncio.run(run())
    for entry in page["targets"]:
        assert not {"tables", "reference_links", "bounds"} & set(entry)
        assert len(entry["text"]) <= 200 and entry["text_total_characters"] == 90_000


# --- data reads under the ceiling ------------------------------------------------------------


@pytest.mark.parametrize("ceiling", [20_000, DEFAULT_REPLY_CEILING])
def test_a_large_data_limit_is_clamped_to_the_room_and_pages_the_whole_value(ceiling):
    service = scale_service(ceiling)
    data = board_envelope()
    paths = [item["path"] for item in data["responses"]]
    flags = paths.index("/data-flags")

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        parts, offset, calls = [], 0, 0
        while offset is not None:
            source = (
                await call(
                    service,
                    "inspect_dashboard_item",
                    snapshot_id=read["snapshot_id"],
                    source_response_index=flags,
                    data_offset=offset,
                    data_limit=120_000,
                )
            )["result"]["source_data"]
            assert source["offset"] == offset and source["text"]
            parts.append(source["text"])
            offset, calls = source["next_offset"], calls + 1
        return "".join(parts), calls

    text, calls = asyncio.run(run())
    assert json.loads(text) == data["responses"][flags]["body"]
    assert calls >= len(text) // ceiling


def test_a_data_read_keeps_room_for_its_data_when_listings_are_asked_for():
    service = scale_service(20_000)

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        return (
            await call(
                service,
                "inspect_dashboard_item",
                snapshot_id=read["snapshot_id"],
                source_response_index=1,
                limit=50,
                source_limit=50,
            )
        )["result"]

    result = asyncio.run(run())
    assert result["targets"] and result["source_responses"]
    assert len(result["source_data"]["text"]) >= 5_000


def test_outline_pages_fit_the_ceiling_with_long_keys():
    service = scale_service(MIN_REPLY_CEILING)

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = service.store.get(read["snapshot_id"])
        snapshot.envelope["responses"].append(
            {
                "method": "GET",
                "path": "/clock",
                "query": {"synthetic": "keys"},
                "status": 200,
                "body": {f"{'k' * 150}{n}": n for n in range(400)},
            }
        )
        index = len(snapshot.envelope["responses"]) - 1
        keys, offset = [], 0
        while offset is not None:
            page = (
                await call(
                    service,
                    "inspect_dashboard_item",
                    snapshot_id=read["snapshot_id"],
                    source_response_index=index,
                    outline=True,
                    outline_offset=offset,
                )
            )["result"]["source_data"]
            keys += [entry["key"] for entry in page["entries"]]
            offset = page["next_outline_offset"]
        return keys

    keys = asyncio.run(run())
    assert keys == [f"{'k' * 150}{n}" for n in range(400)]


# --- the frozen view: view_pointer ---------------------------------------------------------


def test_view_pointer_reads_the_frozen_observation_and_rejects_what_does_not_resolve():
    service = scale_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = read["snapshot_id"]
        frozen = service.store.get(snapshot).view["observation"]

        async def view(**arguments):
            return await call(service, "inspect_dashboard_item", snapshot_id=snapshot, **arguments)

        whole = (await view(view_pointer="", outline=True))["result"]["view_data"]
        assert whole["scope"] == "observation" and whole["encoding"] == "json_outline"
        assert {entry["key"] for entry in whole["entries"]} == set(frozen)
        notes = (await view(view_pointer="/notes"))["result"]["view_data"]
        assert json.loads(notes["text"]) == frozen["notes"] and notes["view_pointer"] == "/notes"
        annotation = (await view(view_pointer="/annotations/3/title"))["result"]["view_data"]
        assert json.loads(annotation["text"]) == frozen["annotations"][3]["title"]
        missing = await view(view_pointer="/nope")
        malformed = await view(view_pointer="nope")
        absent = await view(view_pointer="/notes", target_id="target-absent")
        mixed = await view(view_pointer="/notes", source_response_index=0)
        return missing, malformed, absent, mixed

    missing, malformed, absent, mixed = asyncio.run(run())
    for payload in (missing, malformed):
        assert payload["error"]["code"] == "unknown_pointer"
        assert "outline" in payload["error"]["message"]
    assert absent["error"]["code"] == "unknown_target"
    assert mixed["error"]["code"] == "invalid_arguments"


# --- index and match ---------------------------------------------------------------------


@pytest.mark.parametrize("ceiling", [20_000, DEFAULT_REPLY_CEILING])
def test_index_lists_every_board_target_as_id_and_label_in_one_reply(ceiling):
    service = scale_service(ceiling)

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        page = await call(
            service, "inspect_dashboard_item", snapshot_id=read["snapshot_id"], index=True
        )
        return read, page

    read, page = asyncio.run(run())
    result = page["result"]
    assert result["target_shape"] == "index" and result["next_offset"] is None
    assert result["total"] == read["target_total"] == len(result["targets"]) == 108
    assert all(set(entry) == {"id", "label"} for entry in result["targets"])
    assert result["source_responses"] == []
    print(f"\nindex of 108 Board targets: {compact(page):,} characters")


def test_match_filters_targets_by_id_or_label_before_paging():
    service = scale_service()

    async def run():
        read = (await call(service, "read_dashboard", dashboard="board"))["result"]
        snapshot = read["snapshot_id"]

        async def inspect(**arguments):
            return (
                await call(service, "inspect_dashboard_item", snapshot_id=snapshot, **arguments)
            )["result"]

        one = await inspect(match="SYNTHETIC CARD 07")
        by_id = await inspect(match=one["targets"][0]["id"][-12:].upper())
        none = await inspect(match="no such synthetic text")
        many, pages, offset = [], 0, 0
        while offset is not None:
            page = await inspect(match="Synthetic card", offset=offset, limit=50)
            assert page["total"] == 108 and page["match"] == "Synthetic card"
            many += page["targets"]
            offset, pages = page["next_offset"], pages + 1
        indexed = await inspect(match="Synthetic card 07", index=True)
        return one, by_id, none, many, pages, indexed

    one, by_id, none, many, pages, indexed = asyncio.run(run())
    assert one["total"] == 2 and one["target_shape"] == "listing"
    assert [t["label"] for t in one["targets"]][1] == "Synthetic card 07"
    assert all("Synthetic card 07" in t["label"] for t in one["targets"])
    assert by_id["total"] == 1 and by_id["targets"][0]["id"] == one["targets"][0]["id"]
    assert none["total"] == 0 and none["targets"] == [] and none["next_offset"] is None
    assert len(many) == 108 and len({t["id"] for t in many}) == 108 and pages > 1
    assert indexed["target_shape"] == "index" and indexed["total"] == 2

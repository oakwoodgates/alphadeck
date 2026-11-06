"""Synthetic-only capture and renderer dependencies for the workflow SDK tests."""

import copy
import os
from uuid import UUID

from test_browser import populated_cockpit, response
from test_navigation import SyntheticCapture, SyntheticRenderer, service

from agent_access.capture import validate_capture
from agent_access.renderer import FrozenRenderer
from agent_access.service import DashboardService
from agent_access.snapshots import SnapshotStore


class RecoveryCapture(SyntheticCapture):
    async def capture(self, descriptor, settings, asof, **kwargs):
        data = await super().capture(descriptor, settings, asof, **kwargs)
        # First follow sees a new countdown origin. Reopening Board invalidates
        # the stale parent in a capacity-one store and establishes a new context.
        if len(self.calls) == 2:
            data["context"]["countdown_origin"] = "2026-11-02"
        return data


class PagedRenderer(SyntheticRenderer):
    async def render(self, envelope, snapshot_id, settings, target_id=None):
        view, png, metadata = await super().render(envelope, snapshot_id, settings, target_id)
        view["targets"].reverse()  # The first preview deliberately lacks the link.
        return view, png, metadata


def recovery_service():
    return service(
        capture=RecoveryCapture(), renderer=PagedRenderer(), store=SnapshotStore(capacity=1)
    )


def board_fixture(data):
    thesis = data["responses"][1]["body"]
    thesis.update(archived=False, basket_size=len(thesis["basket"]))
    call = data["responses"][2]
    theses, calls = [], []
    for source, name, state in [
        ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Synthetic Zebra", "armed"),
        ("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "Synthetic warming", "warming"),
        (thesis["id"], thesis["name"], "armed"),
        ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "Synthetic Alpha", "armed"),
        ("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "Synthetic archived", None),
    ]:
        theses.append({**thesis, "id": source, "name": name, "archived": state is None})
        if state is not None:
            call_copy = copy.deepcopy(call)
            call_copy["path"] = f"/theses/{source}/call"
            call_copy["body"]["thesis_id"] = source
            if state == "warming":
                call_copy["body"].update(state=state, verdict="not_yet", armed_members=[])
            calls.append(call_copy)
    flag = {
        "kind": "mixed_scale_history",
        "label": "mixed-scale price history",
        "level": "in_doubt",
        "reason": "Synthetic price-series gap",
        "clears": "Synthetic review pending",
        "since": data["asof"],
        "affects": ["price"],
        "columns": ["ret_7d"],
        "dates": [],
        "evidence": [],
        "call_affected": False,
    }
    member = {
        "security_id": thesis["basket"][0]["security_id"],
        "ticker": thesis["basket"][0]["ticker"],
        "marked": True,
        "flags": [flag],
        "splits": [],
        "theses": [{"id": thesis["id"], "name": thesis["name"]}],
    }
    data.update(
        responses=[
            data["responses"][0],
            response("/theses", theses, {"include_archived": "true"}),
            *calls,
            response(
                "/data-flags",
                {"asof": data["asof"], "members": [member], "names": 1},
                {"asof": data["asof"]},
            ),
        ],
        navigation=[
            {
                "id": "nav_synthetic_board",
                "kind": "fresh_capture",
                "destination": {
                    "dashboard": "cockpit",
                    "settings": {"thesis_id": thesis["id"]},
                },
            }
        ],
    )
    # The evidence references the real Board adapter advertises for these frozen responses.
    from app.routers.dashboards.capture_adapters.board import card_references
    from app.routers.dashboards.capture_contract import CapturedResponse

    class Budget:
        def check_budget(self):
            pass

    frozen = [CapturedResponse.model_validate(item) for item in data["responses"]]
    data["references"] = [
        reference.model_dump(mode="json") for reference in card_references(frozen, Budget())
    ]


def rotation_group(label, card_number, median, members):
    thesis_id = str(UUID(int=100 + card_number))
    rows = []
    for security_number, value in members:
        ticker = "SYNFLAG" if value is None else f"SYN{security_number}"
        rows.append(
            {
                "security_id": str(UUID(int=security_number)),
                "ticker": ticker,
                "cards": [
                    {
                        "thesis_id": thesis_id,
                        "thesis_name": label,
                        "ticker": ticker,
                        "links": [None],
                    }
                ],
                "returns": {"ret_7d": value},
                "above_50d": None,
                "rvol20": None,
                "cross": None,
                "cross_date": None,
                "rs_leading": None,
                "flag": "synthetic mixed-scale history" if value is None else None,
            }
        )
    values = [value for _, value in members if value is not None]
    return {
        "key": thesis_id,
        "thesis_id": thesis_id,
        "thesis_name": label,
        "link": None,
        "business_type": None,
        "supersector": None,
        "names": len(rows),
        "flagged": len(rows) - len(values),
        "windows": [
            {
                "key": "ret_7d",
                "median": median,
                "n": len(values),
                "up": sum(value > 0 for value in values),
                "above_spy": None,
                "tone": None,
            }
        ],
        "above_50d": 0,
        "above_50d_n": 0,
        "rvol20_median": None,
        "rvol20_n": 0,
        "golden": 0,
        "death": 0,
        "rs_leaders": 0,
        "rs_n": 0,
        "members": rows,
    }


def rotation_fixture(data, settings, asof):
    from app.routers.dashboards.rotation_map import RotationMapOut

    start = asof == "2026-09-10"
    assert start or asof == "2026-10-30"
    group_by, silent = settings["group_by"], settings["include_silent"]
    paired = rotation_group(
        "Synthetic paired" if start else "Synthetic renamed",
        1,
        -1.2 if start else 2.3,
        (
            [(1, -5.0), (2, -3.4), (3, 1.0), (4, 4.0)]
            if start
            else [(1, -1.0), (2, 1.0), (3, 3.6), (5, 5.0)]
        ),
    )
    thin = rotation_group(
        "Synthetic thin",
        2,
        None if start else 1.0,
        [(6, -1.0), (7, 1.0), (8, None)] if start else [(6, -1.0), (7, 1.0), (8, None), (9, 3.0)],
    )
    old = rotation_group("Synthetic old", 3, 0.6, [(10, -1.0), (11, 0.6), (12, 2.0)])
    new = rotation_group("Synthetic new", 4, 0.9, [(13, -1.0), (14, 0.9), (15, 2.0)])
    groups = [paired, thin, old] if start else [new, thin, paired]
    companies = sum(group["names"] for group in groups)
    body = {
        "asof": asof,
        "group_by": group_by,
        "include_silent": silent,
        "live_cards": 3,
        "cards_counted": 3,
        "cards_not_yet_created": 0,
        "roster_fallback_cards": int(start),
        "basket_rows": companies + 1,
        "silent_rows_left_out": 1,
        "unresolved_rows": 0,
        "companies": companies,
        "priced_companies": companies - 1,
        "spy": None,
        "groups": groups,
        "flagged": [
            {
                "security_id": str(UUID(int=8)),
                "ticker": "SYNFLAG",
                "reason": "synthetic mixed-scale history",
            }
        ],
        "min_priced": 3,
        "cross_days": 7,
        "cache": "computed",
        "compute_ms": 0,
    }
    body = RotationMapOut.model_validate(body).model_dump(mode="json")
    data.update(
        asof=asof,
        responses=[
            data["responses"][0],
            response(
                "/dashboards/rotation-map",
                body,
                {"asof": asof, "group_by": group_by, "include_silent": str(silent).lower()},
            ),
        ],
        references=[],
        navigation=[],
    )
    if start:
        data["context"].update(
            clock="public",
            known_at="2026-09-11T00:00:00Z",
            decisions_asof=asof,
            countdown_origin=asof,
        )


class BrowserCapture:
    async def capture(self, descriptor, settings, asof, **_):
        data = populated_cockpit()
        slug = descriptor["slug"]
        data["dashboard"] = slug
        if slug == "board":
            assert asof in {None, data["asof"]}
            board_fixture(data)
        elif slug == "rotation-map":
            rotation_fixture(data, settings, asof)
        else:
            from app.routers.dashboards.capture_adapters.cockpit import LIMITATIONS

            assert slug == "cockpit" and asof == data["asof"]
            assert settings["thesis_id"] == data["responses"][1]["body"]["id"]
            data["responses"][2]["body"]["missing"] = ["Synthetic supporting filing unavailable"]
            # As the real capture does: the Cockpit adds its adapter's notes to the shared ones,
            # so a followed capture's limitations differ from its Board parent's.
            data["context"]["limitations"].extend(LIMITATIONS)
        return validate_capture(data, slug, descriptor)


def browser_service():
    return DashboardService(
        capture=BrowserCapture(), renderer=FrozenRenderer(os.environ["MCP_TEST_UI_BASE"])
    )

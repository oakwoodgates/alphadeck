"""O5 — the freeze detector reads COMPLETED EDGAR fetches, and the catch-up exemption covers only the theses a
catch-up SKIPPED.

Real shapes, no fitting:

- the EdgarClient's own counters through its real ``get_text`` / ``_fetch`` / ``polite_get`` (only the wire,
  ``httpx.get``, is stubbed), failing the ways prod failed: the 2026-07-22 EMPTY User-Agent (``_fetch``
  raises before a byte leaves the box), a 404, and a 2xx;
- three prod run-of-record artifacts, copied out READ-ONLY (``tests/fixtures/cron_runs/`` — thesis names
  replaced with ``thesis-<id8>``, the per-thesis ticker inventories dropped; nothing the freeze verdict
  reads was touched), re-derived by the Admin history: each keeps its pre-O5 verdict (no past row
  changes color).
  * ``20260723T015952Z`` — 2026-07-22 21:59 ET: the empty User-Agent. 255 EDGAR pulls ATTEMPTED, 255 of 255
    names errored, the price leg fine (89 bars on one thesis) — logged ``edgar_fetches: 255``, not frozen.
  * ``20260725T010517Z`` — 2026-07-24 21:05 ET: 181 attempted, 181 names errored, and two theses (90 and 159
    names) still RECORDED a call-of-record with every EDGAR-fetching name failed — the partial shape the R2
    withhold misses and the attempt count read as healthy.
  * ``20261013T205743Z`` — 2026-10-13 16:57 ET: prod's one ``--catch-up`` artifact (744 attempts).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.routers.admin import _admin_run_out
from ingest.edgar.client import EdgarClient
from pipeline import daily
from pipeline.cron_run_log import build_run_payload

_RUNS = Path(__file__).resolve().parent.parent / "fixtures" / "cron_runs"
_ASOF = date(2026, 10, 21)


def _artifact(stem: str) -> dict:
    return json.loads((_RUNS / f"{stem}.json").read_text(encoding="utf-8"))


def _tr(**kw) -> daily.ThesisRunResult:
    return daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", **kw)


def _wire(monkeypatch, status: int, body: str = '{"ok": true}') -> list[str]:
    """Stub ONLY the wire: every ``httpx.get`` answers ``status``; the real ``polite_get`` / ``_fetch`` run."""
    sent: list[str] = []

    def _get(url, **kw):
        sent.append(url)
        return httpx.Response(status, text=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _get)
    return sent


# --- the client: attempts vs completions ----------------------------------------------------------


def test_the_2026_07_22_EMPTY_USER_AGENT_attempts_every_pull_and_completes_none(
    tmp_path, monkeypatch
):
    """The real failure: ``_fetch`` refuses before the network (SEC requires a declared User-Agent), so the
    attempt counter climbs while nothing leaves the box — the number the old freeze detector read.
    """
    sent = _wire(monkeypatch, 200)
    client = EdgarClient(cache_dir=tmp_path, allow_live=True, user_agent="ua")
    client.user_agent = ""  # what compose resolved `${ALPHADECK_USER_AGENT:-}` to that night
    for cik in ("0000000001", "0000000002", "0000000003"):
        with pytest.raises(RuntimeError, match="ALPHADECK_USER_AGENT"):
            client.get_json("https://data.sec.gov/x", f"submissions/CIK{cik}.json")
    assert sent == []  # not a byte left the box
    assert client.live_fetches == 3 and client.live_completed == 0


def test_a_2xx_COMPLETES_a_404_and_a_cache_hit_do_not(tmp_path, monkeypatch):
    client = EdgarClient(cache_dir=tmp_path, allow_live=True, user_agent="test ua")
    _wire(monkeypatch, 200)
    client.get_json("https://data.sec.gov/s", "submissions/CIK0000000001.json")
    assert (client.live_fetches, client.live_completed) == (1, 1)
    client.get_json("https://data.sec.gov/s", "submissions/CIK0000000001.json")  # fresh cache hit
    assert (client.live_fetches, client.live_completed) == (1, 1)
    _wire(monkeypatch, 404)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("https://data.sec.gov/c", "companyfacts/CIK0000000002.json")
    assert (client.live_fetches, client.live_completed) == (2, 1)  # attempted; no data arrived


# --- the predicate: completed, over the theses the pass processed ---------------------------------


def test_a_pass_whose_EVERY_pull_failed_is_FROZEN_where_the_attempt_rule_was_SILENT(
    tmp_path, monkeypatch
):
    """The 07-24 21:05 shape through the real counters: a thesis RECORDED its call with every EDGAR pull
    failed. The pre-O5 rule (attempts) is silent — nothing withheld, nothing errored, 3 "fetches"; O5 pages
    FROZEN and says the pulls were attempted, so the page reads as a dead feed, not a cold cache."""
    _wire(monkeypatch, 200)
    client = EdgarClient(cache_dir=tmp_path, allow_live=True, user_agent="ua")
    client.user_agent = ""
    for cik in ("0000000001", "0000000002", "0000000003"):
        with pytest.raises(RuntimeError):
            client.get_json("https://data.sec.gov/x", f"submissions/CIK{cik}.json")
    recorded = _tr(recorded=True, edgar_fetches=client.live_fetches)

    # pre-O5 (no completed count on the result): the attempt rule — silent
    assert daily.assess_health([recorded], asof=_ASOF, allow_live=True) is None

    recorded.edgar_fetches_completed = client.live_completed
    h = daily.assess_health([recorded], asof=_ASOF, allow_live=True)
    assert h is not None and h.frozen is True
    assert h.edgar_fetches == 3 and h.edgar_fetches_completed == 0 and h.freeze_theses == 1
    assert "FROZEN — 0 of 3 EDGAR fetches COMPLETED across 1 theses" in h.label
    assert "every pull failed" in h.label


def test_a_pass_that_COMPLETED_fetches_is_quiet_and_one_that_reached_out_never_reads_cold_cache():
    ok = [_tr(recorded=True, edgar_fetches=120, edgar_fetches_completed=118)]
    assert daily.assess_health(ok, asof=_ASOF, allow_live=True) is None
    cold = [_tr(recorded=True, edgar_fetches=0, edgar_fetches_completed=0)]
    h = daily.assess_health(cold, asof=_ASOF, allow_live=True)
    assert h is not None and h.frozen and "(the cache never refreshed)" in h.label


def test_a_CATCH_UP_is_judged_on_the_theses_it_PROCESSED_its_skipped_ones_are_left_out():
    """M2 skips a thesis whose on-time row stands (no ingest, no client — ``edgar_fetches_completed`` None).
    Those are out of the predicate; the theses the catch-up processed are judged like any pass's."""
    skipped = [_tr(skipped="on-time call-of-record already recorded") for _ in range(9)]
    dead = _tr(recorded=True, edgar_fetches=40, edgar_fetches_completed=0)
    h = daily.assess_health(skipped + [dead], asof=_ASOF, allow_live=True, catch_up=True)
    assert h is not None and h.frozen is True  # the wholesale exemption is gone
    assert h.freeze_theses == 1 and "across 1 theses" in h.label  # never "across 10"

    alive = _tr(recorded=True, edgar_fetches=40, edgar_fetches_completed=40)
    assert (
        daily.assess_health(skipped + [alive], asof=_ASOF, allow_live=True, catch_up=True) is None
    )
    # a catch-up that skipped EVERY thesis processed nothing — it cannot be frozen, and it does not page
    assert daily.assess_health(skipped, asof=_ASOF, allow_live=True, catch_up=True) is None


def test_a_PRE_O5_result_keeps_the_rule_it_was_paged_under_a_catch_up_exempt_wholesale():
    """The contract the Admin history relies on (direct inputs: a result with no completed count): attempts,
    the whole roster, a catch-up exempt — exactly the predicate that paged before O5."""
    zero = [_tr(recorded=False, edgar_fetches=0), _tr(recorded=True, edgar_fetches=0)]
    assert daily.assess_health(zero, asof=_ASOF, allow_live=True, catch_up=True) is None
    h = daily.assess_health(zero, asof=_ASOF, allow_live=True)
    assert h is not None and h.frozen and h.edgar_fetches_completed is None
    assert "FROZEN — 0 EDGAR fetches across 2 theses (the cache never refreshed)" in h.label


# --- the Admin history: real prod artifacts keep their verdicts; a new artifact reads O5 ------------


@pytest.mark.parametrize(
    ("stem", "healthy", "withheld"),
    [
        (
            "20260723T015952Z",
            False,
            5,
        ),  # unhealthy on 5 withheld calls — never FROZEN (255 attempts)
        (
            "20260725T010517Z",
            False,
            3,
        ),  # unhealthy on 3 withheld calls — the 2 recorded theses unseen
        ("20261013T205743Z", True, 0),  # the catch-up: healthy
    ],
)
def test_a_real_PRE_O5_artifact_keeps_its_PRE_O5_verdict(stem, healthy, withheld):
    row = _admin_run_out(_artifact(stem))
    assert row.healthy is healthy
    assert not any(p.startswith("FROZEN") for p in row.problems)
    assert row.edgar_fetches_completed is None  # not measured then — never a coerced 0
    if withheld:
        assert f"{withheld} call(s) WITHHELD — TOTAL INGEST FAILURE" in row.problems


def test_a_NEW_artifact_carries_completed_counts_and_the_history_pages_what_the_pass_paged():
    """The 07-22 night as an O5 pass would write it: the real artifact's roster and attempt counts, each
    thesis's completed count 0 (the documented cause — every pull raised before the network) — through the
    REAL writer (``build_run_payload``) and the REAL reader (``_admin_run_out``)."""
    doc = _artifact("20260723T015952Z")
    results = [
        daily.ThesisRunResult(
            thesis_id=uuid.UUID(t["id"]),
            name=t["name"],
            recorded=t["recorded"],
            withheld_reason=t["withheld_reason"],
            edgar_fetches=t["edgar_fetches"],
            edgar_fetches_completed=0,
        )
        for t in doc["theses"]
    ]
    started = datetime.fromisoformat(doc["started_at"])
    payload = build_run_payload(
        results,
        asof=date.fromisoformat(doc["asof"]),
        allow_live=True,
        started_at=started,
        finished_at=datetime.fromisoformat(doc["finished_at"]),
    )
    assert payload["edgar_fetches"] == 255 and payload["edgar_fetches_completed"] == 0
    assert all(t["edgar_fetches_completed"] == 0 for t in payload["theses"])

    row = _admin_run_out(json.loads(json.dumps(payload)))  # the on-disk round trip
    assert row.healthy is False and row.edgar_fetches == 255 and row.edgar_fetches_completed == 0
    assert row.problems[0] == (
        "FROZEN — 0 of 255 EDGAR fetches COMPLETED across 6 theses "
        "(every pull failed — no EDGAR data reached the pass)"
    )
    assert "5 call(s) WITHHELD — TOTAL INGEST FAILURE" in row.problems


def test_a_SKIPPED_thesis_writes_a_null_completed_count_and_the_run_sums_the_rest():
    results = [
        _tr(recorded=True, edgar_fetches=12, edgar_fetches_completed=11),
        _tr(skipped="on-time call-of-record already recorded"),
    ]
    payload = build_run_payload(
        results,
        asof=_ASOF,
        allow_live=True,
        started_at=datetime(2026, 10, 22, 2, 30, tzinfo=timezone.utc),
        finished_at=datetime(2026, 10, 22, 2, 41, tzinfo=timezone.utc),
        catch_up=True,
    )
    assert [t["edgar_fetches_completed"] for t in payload["theses"]] == [11, None]
    assert payload["edgar_fetches_completed"] == 11
    row = _admin_run_out(json.loads(json.dumps(payload)))
    assert row.healthy is True and row.edgar_fetches_completed == 11

"""Filling a session the vendor served no close for from Massive.com's raw bar (W6-3b, the SNDK fix).

REAL data only, cut from the 2026-11-25 read-only probe (``tests/fixtures/massive/gap_fill``):

- ``yahoo_<T>.json``: the vendor's chart bodies as Yahoo served them at 21:22-21:23 ET (``range=1mo``). SNDK's
  pre-split bars are already divided by 8 (11-19 = 94.395) and 11-20..11-24 carry null closes (11-25's null is
  the session not yet settled); SCNI is null from 11-19; PARA's first post-split session (11-23) is null; DFSC's
  first post-split session (11-16) is null; GNS serves zero-volume placeholders (0.101) 11-10..11-18 and a null
  11-19.
- ``grouped_cut.json``: the grouped raw days 11-18..11-25 cut to the names above, each row as Massive.com served it.
- ``range_<T>_2026-11-02_2026-11-25.json``: the per-ticker raw ranges of SNDK and GNS (GNS: no bar 11-10..11-19).

A test that needs a value no source served says so (a STATED edit) and edits one real value.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from domain.splits import STATUS_CONFIRMED, STATUS_NEEDS_REVIEW, ListedSplit
from ingest import CacheMiss
from ingest.massive_bars import MassiveBar, MassiveBarsError, parse_grouped, parse_range
from ingest.prices import gap_fill as gf
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from ingest.prices.ingest_security import ingest_bars_for_security
from ingest.prices.settle import withhold_unsettled
from ingest.prices.split_normalize import normalize
from repositories import split_repo

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "massive" / "gap_fill"

NOV16, NOV19, NOV20 = date(2026, 11, 16), date(2026, 11, 19), date(2026, 11, 20)
NOV23, NOV24, NOV25 = date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25)
WINDOW = 20  # Settings.price_gap_fill_split_sessions


def _night(d: date) -> datetime:
    """The nightly's instant on market day ``d`` (22:30 ET, EST = UTC-5)."""
    return datetime(d.year, d.month, d.day, 22, 30, tzinfo=timezone(timedelta(hours=-5)))


def _split(eff: date, frm: int, to: int, status: str = STATUS_CONFIRMED) -> ListedSplit:
    return ListedSplit(eff, eff, Decimal(frm), Decimal(to), status)


SNDK_8_FOR_1 = _split(
    NOV20, 1, 8
)  # 8-for-1 forward, first session 2026-11-20 (the list: auto:sec+polygon)
SCNI_1_FOR_4 = _split(NOV19, 4, 1)
PARA_1_FOR_100 = _split(NOV23, 100, 1)
GNS_1_FOR_10 = _split(NOV20, 10, 1)
DFSC_1_FOR_9 = _split(NOV16, 9, 1, STATUS_NEEDS_REVIEW)  # still needs review on prod's list


def _yahoo(tk: str) -> list[dict]:
    return parse_yahoo_chart(json.loads((FIX / f"yahoo_{tk}.json").read_text(encoding="utf-8")))


def _cut() -> dict:
    return json.loads((FIX / "grouped_cut.json").read_text(encoding="utf-8"))["sessions"]


def _grouped(session: date):
    return parse_grouped(json.dumps(_cut()[session.isoformat()]), session=session)


def _range(tk: str) -> dict[date, MassiveBar]:
    return parse_range((FIX / f"range_{tk}_2026-11-02_2026-11-25.json").read_text(encoding="utf-8"))


def _massive(tk: str) -> dict[date, MassiveBar]:
    """Every raw bar of ``tk`` the probe read: the grouped days, then the per-ticker range where there is one."""
    out = {}
    for s in _cut():
        b = _grouped(date.fromisoformat(s)).get(tk)
        if b is not None:
            out[date.fromisoformat(s)] = b
    if (FIX / f"range_{tk}_2026-11-02_2026-11-25.json").exists():
        out.update(_range(tk))
    return out


def _settled(tk: str, night: date) -> list[dict]:
    """The vendor's pull as the price leg sees it on ``night`` (D2: unsettled bars withheld)."""
    fresh, _ = withhold_unsettled(_yahoo(tk), now=_night(night))
    return fresh


def _stored(bars: list[dict], *, drop: tuple[date, ...] = ()) -> dict[date, dict]:
    """The store as ``eod_loader.stored_bars`` returns it (Decimal close / volume)."""
    return {
        b["d"]: {
            "d": b["d"],
            "close": Decimal(str(b["close"])),
            "volume": None if b["volume"] is None else Decimal(str(b["volume"])),
        }
        for b in bars
        if b["d"] not in drop
    }


def _plan(fresh, stored, in_force, *, night, massive, scope=gf.SCOPE_SPLIT, pending=(), tk="SNDK"):
    return gf.plan_fill(
        fresh,
        stored,
        in_force,
        sessions=gf.recent_sessions(gf.settled_session(_night(night)), WINDOW),
        massive=massive,
        scope=scope,
        split_window=WINDOW,
        pending=pending,
        symbol=tk,
    )


def _closes(plan) -> list[tuple[date, Decimal]]:
    return [(f.d, f.raw_close) for f in plan.fills]


# --- the pure rule ----------------------------------------------------------------------------------------


def test_sndk_four_sessions_yahoo_nulled_are_filled_with_massive_s_raw_bars():
    """The live defect: the store ends 11-19 at 94.395 (the list's /8 already applied), Yahoo nulls every
    post-split session; Massive.com traded each one. Four RAW fills, nothing rescaled (on/after the split's first
    session the raw bar IS on the list's scale)."""
    fresh = _settled("SNDK", NOV25)
    assert max(b["d"] for b in fresh) == NOV19 and fresh[-1]["close"] == pytest.approx(
        94.395, abs=1e-4
    )
    plan = _plan(fresh, _stored(fresh), [SNDK_8_FOR_1], night=NOV25, massive=_massive("SNDK"))
    assert _closes(plan) == [
        (NOV20, Decimal("79.24")),
        (NOV23, Decimal("87.71")),
        (NOV24, Decimal("77.95")),
        (NOV25, Decimal("73.61")),
    ]
    assert all(f.factor == Fraction(1) for f in plan.fills)
    assert all(f.close == f.raw_close and f.volume == f.raw_volume for f in plan.fills)
    assert plan.fills[0].raw_volume == Decimal("127860864.911014")
    assert {f.reason for f in plan.fills} == {gf.REASON_SPLIT_WINDOW}
    assert {f.vendor_symbol for f in plan.fills} == {"SNDK"} and plan.refused == []
    assert plan.fills[0].bar() == {
        "d": NOV20,
        "open": Decimal("89.98"),
        "high": Decimal("90.67"),
        "low": Decimal("78.25"),
        "close": Decimal("79.24"),
        "volume": Decimal("127860864.911014"),
    }


def test_a_first_session_hole_inside_the_vendor_s_window_is_one_fill():
    """The DFSC shape on REAL data (DFSC's 11-16 raw bar was not probed; PARA's 11-23 has the same shape): the
    vendor nulls only the first post-split session and serves the next one, so the hole sits inside the stored
    window — one fill, through the hole path."""
    fresh = _settled("PARA", NOV24)
    assert NOV23 not in {b["d"] for b in fresh} and max(b["d"] for b in fresh) == NOV24
    plan = _plan(
        fresh, _stored(fresh), [PARA_1_FOR_100], night=NOV24, massive=_massive("PARA"), tk="PARA"
    )
    assert _closes(plan) == [(NOV23, Decimal("2.72"))] and plan.refused == []


def test_gns_halt_placeholders_are_never_overwritten_and_a_hole_before_the_split_is_listed():
    """GNS did not trade 11-10..11-19 (Massive.com has no bar); Yahoo serves zero-volume placeholders 11-10..11-18
    (a vendor bar always wins) and a null 11-19 — BEFORE the split's first session, so never filled: listed. (The
    night of 11-24: Yahoo serves 11-20..11-24.)"""
    fresh = _settled("GNS", NOV24)
    plan = _plan(
        fresh, _stored(fresh), [GNS_1_FOR_10], night=NOV24, massive=_massive("GNS"), tk="GNS"
    )
    assert plan.fills == []
    assert [(r.d, r.why) for r in plan.refused] == [(NOV19, gf.WHY_BEFORE_SPLIT)]
    # nothing in force (scope ``all``): Massive.com has no trade on 11-19 — still nothing
    plan = _plan(
        fresh,
        _stored(fresh),
        [],
        night=NOV24,
        massive=_massive("GNS"),
        scope=gf.SCOPE_ALL,
        tk="GNS",
    )
    assert plan.fills == [] and [(r.d, r.why) for r in plan.refused] == [(NOV19, gf.WHY_NO_TRADE)]


def test_a_massive_bar_with_zero_volume_is_no_trade():
    """STATED edit: Massive.com's real 11-20 SNDK row with its volume set to 0."""
    massive = _massive("SNDK")
    b = massive[NOV20]
    massive[NOV20] = MassiveBar(b.open, b.high, b.low, b.close, Decimal(0), b.otc)
    fresh = _settled("SNDK", NOV25)
    plan = _plan(fresh, _stored(fresh), [SNDK_8_FOR_1], night=NOV25, massive=massive)
    assert NOV20 not in {f.d for f in plan.fills}
    assert (NOV20, gf.WHY_NO_TRADE) in [(r.d, r.why) for r in plan.refused]


def test_a_vendor_bar_always_wins_and_a_stored_bar_is_never_overwritten():
    """STATED edits: the vendor serves 11-20 (Massive.com's own print, the volume as Yahoo rounds it); the store
    holds 11-23 (an earlier pass's bar). Neither is ever replaced."""
    fresh = _settled("SNDK", NOV25) + [
        {
            "d": NOV20,
            "open": 89.98,
            "high": 90.67,
            "low": 78.25,
            "close": 79.24,
            "volume": 127860900,
        }
    ]
    stored = _stored(fresh)
    stored[NOV23] = {"d": NOV23, "close": Decimal("87.7"), "volume": Decimal(114032600)}
    plan = _plan(fresh, stored, [SNDK_8_FOR_1], night=NOV25, massive=_massive("SNDK"))
    assert [f.d for f in plan.fills] == [NOV24, NOV25]


def test_no_split_in_force_fills_nothing_under_split_and_every_gap_under_all():
    fresh = _settled("SNDK", NOV25)
    plan = _plan(fresh, _stored(fresh), [], night=NOV25, massive=_massive("SNDK"))
    assert plan.fills == [] and {r.why for r in plan.refused} == {gf.WHY_NO_SPLIT}
    plan = _plan(
        fresh, _stored(fresh), [], night=NOV25, massive=_massive("SNDK"), scope=gf.SCOPE_ALL
    )
    assert [f.d for f in plan.fills] == [NOV20, NOV23, NOV24, NOV25]
    assert {f.reason for f in plan.fills} == {gf.REASON_ALL}


def test_off_fills_nothing_and_asks_for_nothing():
    fresh = _settled("SNDK", NOV25)
    plan = _plan(
        fresh,
        _stored(fresh),
        [SNDK_8_FOR_1],
        night=NOV25,
        massive=_massive("SNDK"),
        scope=gf.SCOPE_OFF,
    )
    assert plan.fills == [] and plan.refused == []
    assert (
        gf.wanted(
            fresh,
            _stored(fresh),
            [SNDK_8_FOR_1],
            sessions=gf.recent_sessions(NOV25, WINDOW),
            scope=gf.SCOPE_OFF,
            split_window=WINDOW,
        )
        == []
    )


def test_a_session_on_or_after_a_pending_split_s_first_session_is_never_filled():
    """DFSC (and EMBJ) on prod: the first post-split session is null on Yahoo and the split still NEEDS REVIEW. A
    split the operator has not settled is applied to nothing, so no fill runs ahead of it — refused before
    Massive.com is even asked (no raw bar is needed to say so)."""
    fresh = _settled("DFSC", NOV24)
    assert NOV16 not in {b["d"] for b in fresh}
    want = gf.wanted(
        fresh,
        _stored(fresh),
        [],
        sessions=gf.recent_sessions(NOV24, WINDOW),
        scope=gf.SCOPE_ALL,
        split_window=WINDOW,
        pending=[DFSC_1_FOR_9],
    )
    assert want == []
    plan = _plan(
        fresh,
        _stored(fresh),
        [],
        night=NOV24,
        massive={},
        scope=gf.SCOPE_ALL,
        pending=[DFSC_1_FOR_9],
        tk="DFSC",
    )
    assert plan.fills == [] and [(r.d, r.why) for r in plan.refused] == [
        (NOV16, gf.WHY_PENDING_SPLIT)
    ]
    # PARA's real 11-23 bar with its split still pending: the same refusal although Massive.com traded it
    fresh = _settled("PARA", NOV24)
    pending = [_split(NOV23, 100, 1, STATUS_NEEDS_REVIEW)]
    plan = _plan(
        fresh, _stored(fresh), [], night=NOV24, massive=_massive("PARA"), pending=pending, tk="PARA"
    )
    assert plan.fills == [] and [(r.d, r.why) for r in plan.refused] == [
        (NOV23, gf.WHY_PENDING_SPLIT)
    ]


def test_sndk_25_sessions_on_with_the_vendor_still_silent_is_still_filled():
    """STATED: Yahoo still nulls every SNDK session 25 sessions after the split (the store holds the earlier
    fills); Massive.com's real 11-25 print re-dated to the night stands in for that night's bar. Past the
    20-session window the fill continues while the vendor has served NOTHING since the split — and stops once
    it serves anything."""
    night = date(2026, 12, 28)
    assert (
        gf.recent_sessions(night, 25)[0] == NOV20
    )  # 25 sessions on, the split's first session the first
    fresh = _settled("SNDK", NOV25)
    stored = _stored(fresh)
    for d in gf.sessions_between(NOV20, night - timedelta(days=1)):
        stored[d] = {"d": d, "close": Decimal("73.61"), "volume": Decimal(1)}
    massive = {night: _massive("SNDK")[NOV25]}
    plan = gf.plan_fill(
        fresh,
        stored,
        [SNDK_8_FOR_1],
        sessions=gf.recent_sessions(night, WINDOW),
        massive=massive,
        scope=gf.SCOPE_SPLIT,
        split_window=WINDOW,
        symbol="SNDK",
    )
    assert [(f.d, f.reason) for f in plan.fills] == [(night, gf.REASON_VENDOR_SILENT)]
    # the vendor served one post-split bar: past the window, the gap is the vendor's ordinary late close
    served = fresh + [{**fresh[-1], "d": NOV23}]
    plan = gf.plan_fill(
        served,
        stored,
        [SNDK_8_FOR_1],
        sessions=gf.recent_sessions(night, WINDOW),
        massive=massive,
        scope=gf.SCOPE_SPLIT,
        split_window=WINDOW,
        symbol="SNDK",
    )
    assert plan.fills == [] and [(r.d, r.why) for r in plan.refused] == [
        (night, gf.WHY_OUTSIDE_WINDOW)
    ]


def test_tonight_s_session_is_a_candidate_only_once_it_has_settled():
    """D2: before the settle time tonight's session is no candidate (its bar is not a close yet)."""
    afternoon = datetime(2026, 11, 25, 15, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert gf.settled_session(afternoon) == NOV24
    assert gf.settled_session(_night(NOV25)) == NOV25
    # a Saturday morning: the newest settled session is Friday's
    assert gf.settled_session(datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)) == NOV20
    fresh, _ = withhold_unsettled(_yahoo("SNDK"), now=afternoon)
    plan = gf.plan_fill(
        fresh,
        _stored(fresh),
        [SNDK_8_FOR_1],
        sessions=gf.recent_sessions(gf.settled_session(afternoon), WINDOW),
        massive=_massive("SNDK"),
        scope=gf.SCOPE_SPLIT,
        split_window=WINDOW,
        symbol="SNDK",
    )
    assert [f.d for f in plan.fills] == [NOV20, NOV23, NOV24]  # never 11-25 before it settles


def test_a_fill_is_never_rescaled_by_the_split_normalizer():
    """The fills ride ``fresh`` into ``normalize``: bars on/after the last split in force are untouched."""
    fresh = _settled("SNDK", NOV25)
    stored = _stored(fresh)
    plan = _plan(fresh, stored, [SNDK_8_FOR_1], night=NOV25, massive=_massive("SNDK"))
    norm = normalize(fresh + [f.bar() for f in plan.fills], stored, [SNDK_8_FOR_1])
    by_d = {b["d"]: b for b in norm.bars}
    assert [by_d[f.d]["close"] for f in plan.fills] == [f.raw_close for f in plan.fills]
    assert norm.first_night == [] and norm.total == 0


def test_with_w0_1_the_fill_is_the_tail_a_first_night_s_raw_history_is_proven_against():
    """GNS's real series, STATED edits (the W0 shape): the store holds the history already on the new scale (the
    vendor's early re-base landed, x10) and nothing on/after the 11-20 first session; on the first night the
    vendor serves the history RAW again and null post-split bars (11-20 nulled). The fill (Massive.com's real
    11-20 bar) is the vendor's post-split tail W0-1 reads the raw run against: the whole run is absorbed.
    """
    raw = [b for b in _yahoo("GNS") if b["d"] < NOV19]
    stored = {
        b["d"]: {"d": b["d"], "close": Decimal(str(b["close"])) * 10, "volume": Decimal(1000)}
        for b in raw
    }
    fresh = [dict(b, volume=10000) for b in raw]
    plan = _plan(fresh, stored, [GNS_1_FOR_10], night=NOV20, massive=_massive("GNS"), tk="GNS")
    assert _closes(plan) == [(NOV20, Decimal("0.4993"))]
    norm = normalize(fresh + [f.bar() for f in plan.fills], stored, [GNS_1_FOR_10])
    assert norm.scaled == {"vendor_raw": len(raw)}


# --- the pass's Massive.com reads (GapSource) -------------------------------------------------------------


class _Ranges:
    """The range read's seam: answers from the probe's per-ticker ranges, counting every read."""

    def __init__(self, ranges: dict[str, dict[date, MassiveBar]], *, fail: Exception | None = None):
        self.ranges = ranges
        self.fail = fail
        self.asked: list[tuple[str, date, date]] = []

    def bars(self, symbol, start, end, *, allow_live):
        self.asked.append((symbol, start, end))
        if self.fail is not None:
            raise self.fail
        return {d: b for d, b in self.ranges.get(symbol, {}).items() if start <= d <= end}


def test_tonight_reads_the_grouped_day_and_only_older_sessions_cost_one_range_read():
    src = _Ranges({"SNDK": _range("SNDK")})
    gap = gf.GapSource(src, tonight=_grouped(NOV20), split_sessions=WINDOW, max_names=10)
    fresh = _settled("SNDK", NOV20)
    plan = gap.plan(fresh, _stored(fresh), [SNDK_8_FOR_1], symbol="SNDK", now=_night(NOV20))
    assert _closes(plan) == [(NOV20, Decimal("79.24"))] and src.asked == []  # no request
    fresh = _settled("SNDK", NOV25)
    gap = gf.GapSource(src, tonight=_grouped(NOV25), split_sessions=WINDOW, max_names=10)
    plan = gap.plan(fresh, _stored(fresh), [SNDK_8_FOR_1], symbol="SNDK", now=_night(NOV25))
    assert [f.d for f in plan.fills] == [NOV20, NOV23, NOV24, NOV25]
    assert src.asked == [("SNDK", NOV20, NOV24)] and gap.range_reads == 1


def test_past_the_cap_a_name_is_deferred_and_counted_and_tonight_still_fills():
    src = _Ranges({"SNDK": _range("SNDK")})
    gap = gf.GapSource(src, tonight=_grouped(NOV25), split_sessions=WINDOW, max_names=0)
    fresh = _settled("SNDK", NOV25)
    plan = gap.plan(fresh, _stored(fresh), [SNDK_8_FOR_1], symbol="SNDK", now=_night(NOV25))
    assert [f.d for f in plan.fills] == [NOV25] and plan.deferred and src.asked == []
    assert gap.deferred == ["SNDK"]
    assert {(r.d, r.why) for r in plan.refused} == {
        (NOV20, gf.WHY_NOT_READ),
        (NOV23, gf.WHY_NOT_READ),
        (NOV24, gf.WHY_NOT_READ),
    }


@pytest.mark.parametrize(
    "fail",
    [MassiveBarsError("HTTP 500 for <url>"), CacheMiss("no cached range (live pulls disabled)")],
)
def test_a_failed_range_read_fills_only_what_was_read_and_says_why(fail):
    src = _Ranges({}, fail=fail)
    gap = gf.GapSource(src, tonight=_grouped(NOV25), split_sessions=WINDOW, max_names=10)
    fresh = _settled("SNDK", NOV25)
    plan = gap.plan(fresh, _stored(fresh), [SNDK_8_FOR_1], symbol="SNDK", now=_night(NOV25))
    assert [f.d for f in plan.fills] == [NOV25] and not plan.deferred
    assert plan.note and str(fail) in plan.note


def test_a_name_massive_did_not_trade_tonight_makes_no_request_for_tonight():
    """SCNI's first night (11-19): the grouped day HAS its bar; a name absent from tonight's grouped day is no
    trade, never a range read for tonight."""
    src = _Ranges({})
    gap = gf.GapSource(src, tonight=_grouped(NOV19), split_sessions=WINDOW, max_names=10)
    fresh = _settled("SCNI", NOV19)
    plan = gap.plan(fresh, _stored(fresh), [SCNI_1_FOR_4], symbol="SCNI", now=_night(NOV19))
    assert _closes(plan) == [(NOV19, Decimal("1.5542"))] and src.asked == []
    plan = gap.plan(fresh, _stored(fresh), [SCNI_1_FOR_4], symbol="SCNIX", now=_night(NOV19))
    assert plan.fills == [] and src.asked == []


# --- the price leg (DB-backed, COUNT THE TABLE) -----------------------------------------------------------


class _Vendor:
    def __init__(self, bars):
        self.bars = bars

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return [dict(b) for b in self.bars]


def _seed(db, tk: str, split: ListedSplit) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, tk, date(2020, 1, 1)),
        )
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        key_date=split.key_date,
        values={
            "effective_date": split.effective_date,
            "ratio_from": split.ratio_from,
            "ratio_to": split.ratio_to,
            "status": split.status,
            "confirmed_via": "operator" if split.status == STATUS_CONFIRMED else None,
            "value_source": "both",
            "valid_from": split.effective_date,
        },
        action="confirm" if split.status == STATUS_CONFIRMED else "detect",
        author="operator" if split.status == STATUS_CONFIRMED else "system:detect_splits",
    )
    db.commit()
    return sid


def _tape(db, sid) -> dict[date, Decimal]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (d) d, close FROM fact_price_eod WHERE security_id = %s "
            "ORDER BY d, recorded_at DESC",
            (sid,),
        )
        return {r["d"]: r["close"] for r in cur.fetchall()}


def _count(db, table: str, sid) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE security_id = %s", (sid,))
        return cur.fetchone()["n"]


def test_sndk_the_price_leg_stores_massive_s_raw_close_for_11_20(db):
    """The headline: the night of SNDK's first post-split session, Yahoo serves no close; the price leg stores
    Massive.com's raw 11-20 bar (from tonight's grouped day: no request) and records its provenance.
    """
    sid = _seed(db, "SNDK", SNDK_8_FOR_1)
    ingest_prices(
        db, sid, [b for b in _yahoo("SNDK") if b["d"] <= NOV19], tenant_id=DEFAULT_TENANT_ID
    )
    db.commit()
    n0 = _count(db, "fact_price_eod", sid)
    src = _Ranges({"SNDK": _range("SNDK")})
    gap = gf.GapSource(src, tonight=_grouped(NOV20), split_sessions=WINDOW, max_names=10)
    res = ingest_bars_for_security(
        db,
        Security(id=sid, tenant_id=DEFAULT_TENANT_ID, ticker="SNDK"),
        tenant_id=DEFAULT_TENANT_ID,
        allow_live=True,
        force_refresh=True,
        source=_Vendor(_yahoo("SNDK")),
        now=_night(NOV20),
        gap_source=gap,
    )
    db.commit()
    tape = _tape(db, sid)
    assert NOV20 in tape, "no bar appended for SNDK 11-20"
    assert tape[NOV20] == Decimal("79.24")
    assert (res.appended, res.reversioned, res.filled, res.filled_dates) == (1, 0, 1, (NOV20,))
    assert _count(db, "fact_price_eod", sid) == n0 + 1 and _count(db, "price_bar_fill", sid) == 1
    assert src.asked == []

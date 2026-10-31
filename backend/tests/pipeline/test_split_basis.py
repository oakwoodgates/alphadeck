"""T4 — the split-basis defect monitor (``pipeline/split_basis.py``), proved on REAL tape shapes.

Every shape test reads ``tests/fixtures/prices/split_basis_tapes_2026_10.json`` — bars extracted read-only
from prod ``fact_price_eod`` on 2026-10-16 (its ``_provenance`` says how; no value altered). Each case is a
real instance of the thing it stands for: VWAV's half-adjusted island, MPU's / SEGG's flapping versions,
MYPS's clean reverse split and ZCSH's clean forward split (each re-based ONCE), DFNS's genuine squeeze and
CORT's genuine crash, MI's unadjusted reverse split, MMTIF's one-cent tick flips, MVNC's thin prints, and
GGRP's packed one-bar islands. The assertions are the monitor's CONTRACT on those instances (flag / do not
flag, which dates, roughly what factor) — never a snapshot of every number it computes.

The DB tests write bars through plain INSERTs (the tape's shape, its real version stamps where the fixture
has them) and COUNT THE TABLE around the scan: the monitor reads, it never writes.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from db.session import DEFAULT_TENANT_ID, connect
from pipeline import cron_run_log, daily
from pipeline.self_audit import AuditContext, check_split_defects_flagged
from pipeline.split_basis import (
    CLIFF,
    DATES_CAP,
    FLAP,
    ISLAND,
    SETTLED_REASON,
    BarVersion,
    Finding,
    PublicVersion,
    SettledDefect,
    SplitDefect,
    SplitScan,
    TapeBar,
    _is_boundary,
    defect_keys_of,
    flap_findings,
    fold,
    new_labels_of,
    report_lines,
    run_split_scan,
    scan_split_defects,
    settle_flap,
    settle_split_defects,
    tape_findings,
    view_lookback_days,
    window_days,
)

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "split_basis_tapes_2026_10.json"
)
_DOC = json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _tape(ticker: str) -> list[TapeBar]:
    return [TapeBar(date.fromisoformat(d), c, v) for d, c, v in _DOC[ticker]["tape"]]


def _history(ticker: str) -> dict[date, list[BarVersion]]:
    return {
        date.fromisoformat(d): [BarVersion(datetime.fromisoformat(ra), c) for ra, c, _v in vs]
        for d, vs in _DOC[ticker]["versions"].items()
    }


def _kind(findings, kind):
    return [f for f in findings if f.kind == kind]


_EVER = date(2000, 1, 1)  # a flap_since that admits every recorded change


# --- the real shapes: what MUST flag -------------------------------------------------------------------


def test_VWAV_half_adjusted_ISLAND_flags_its_two_unadjusted_bars():
    found = tape_findings(_tape("VWAV"))
    (island,) = _kind(found, ISLAND)
    assert island.dates == (date(2026, 9, 15), date(2026, 9, 16))
    assert 15 < island.factor < 21  # a 1-for-20 split seen through two genuine sessions
    assert not _kind(found, CLIFF)


def test_VWAV_versions_FLAP_the_bars_the_vendor_flipped_and_flipped_back():
    (flap,) = flap_findings(_history("VWAV"), flap_since=date(2026, 9, 1))
    assert {date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)} <= set(flap.dates)
    assert 19 < flap.factor < 21


def test_MPU_flap_history_flags_and_ages_out_after_its_last_flip():
    hist = _history("MPU")
    (flap,) = flap_findings(hist, flap_since=date(2026, 7, 18))
    assert flap.kind == FLAP and set(flap.dates) == set(hist)  # every extracted bar flapped
    assert 19 < flap.factor < 21
    # the latest change back was recorded 2026-10-15 (UTC): a window starting after it sees no live flap
    assert flap_findings(hist, flap_since=date(2026, 10, 16)) == []


def test_SEGG_flap_history_flags_at_its_x7_factor():
    (flap,) = flap_findings(_history("SEGG"), flap_since=_EVER)
    assert 6.5 < flap.factor < 7.5


def test_MI_unadjusted_reverse_split_is_a_lone_CLIFF():
    (cliff,) = tape_findings(_tape("MI"))
    assert cliff.kind == CLIFF and cliff.dates == (date(2026, 5, 18),)
    assert cliff.factor > 50  # 0.196 on 318K shares -> 18.90 on 12K, both levels persisting


def test_GGRP_packed_one_bar_islands_all_flag():
    islands = _kind(tape_findings(_tape("GGRP")), ISLAND)
    assert {f.dates for f in islands} == {
        (date(2026, 7, 29),),
        (date(2026, 8, 4),),
        (date(2026, 8, 7),),
        (date(2026, 8, 11),),
    }
    assert all(6 < f.factor < 10 for f in islands)


# --- the real shapes: what must NEVER flag -------------------------------------------------------------


@pytest.mark.parametrize("ticker", ["MYPS", "ZCSH"])
def test_a_CLEAN_split_re_based_once_never_flags(ticker):
    """MYPS (1-for-10 reverse) and ZCSH (3-for-1 forward): the vendor re-based every old bar ONCE and the
    ingest re-versioned it — the honest path. The tape is smooth and no bar changed basis twice."""
    assert tape_findings(_tape(ticker)) == []
    hist = _history(ticker)
    assert len(hist) >= 15  # the re-based bars really are in the extract (two versions each)
    assert flap_findings(hist, flap_since=_EVER) == []


def test_a_GENUINE_squeeze_on_normal_volume_never_flags():
    """DFNS 07-27..07-30: x3, x1.8, x2.1, x1.7 in four sessions; the x2 continuation days come on volume
    that falls by LESS than the price factor (07-29: x2.09 on x0.74). A real move, not a basis."""
    bars = _tape("DFNS")
    assert any(b.close and b.close > 80 for b in bars) and any(
        b.close and b.close < 5 for b in bars
    )
    assert tape_findings(bars) == []
    assert flap_findings(_history("DFNS"), flap_since=_EVER) == []  # its x125 re-base happened once


def test_a_GENUINE_crash_on_heavy_volume_never_flags():
    """CORT 2025-12-31: 70.20 -> 34.80 on 20.3M shares — a lone DOWN step with volume UP by about the
    price factor, which is also what an unadjusted forward split looks like. Never flagged (by design).
    """
    assert tape_findings(_tape("CORT")) == []


def test_sub_two_cent_TICK_flips_never_flag():
    bars = _tape("MMTIF")
    assert {round(b.close, 6) for b in bars} <= {0.01, 0.02}  # the real quotes: one cent apart
    assert tape_findings(bars) == []


def test_THIN_prints_never_flag():
    assert tape_findings(_tape("MVNC")) == []
    assert flap_findings(_history("MVNC"), flap_since=_EVER) == []


# --- the rule's contract on direct inputs ----------------------------------------------------------------


def _b(day: int, close: float | None, volume: float | None = 1_000_000.0) -> TapeBar:
    return TapeBar(date(2026, 1, 1) + timedelta(days=day), close, volume)


def test_a_boundary_needs_both_closes_at_or_above_the_tick_floor():
    assert _is_boundary(_b(0, 0.019), _b(1, 0.2)) is False
    assert _is_boundary(_b(0, 0.02), _b(1, 0.2)) is True
    assert _is_boundary(_b(0, 0.0199999995529652), _b(1, 0.2)) is True  # 0.02 as float32 stores it
    assert _is_boundary(_b(0, 1.0), _b(1, 1.7)) is False  # below K_MIN either way
    assert _is_boundary(_b(0, 1.0), _b(1, None)) is False


def test_a_flap_needs_BOTH_directions_and_a_recent_change():
    t = datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc)
    d = date(2026, 8, 3)
    once = {d: [BarVersion(t, 0.5), BarVersion(t + timedelta(days=1), 5.0)]}
    assert flap_findings(once, flap_since=_EVER) == []  # one re-base is the honest re-version
    back = {d: [*once[d], BarVersion(t + timedelta(days=2), 0.5)]}
    (flap,) = flap_findings(back, flap_since=date(2026, 9, 1))
    assert flap.dates == (d,) and flap.factor == pytest.approx(10.0)
    assert flap_findings(back, flap_since=date(2026, 9, 4)) == []  # the last flip is too old


def test_fold_caps_dates_but_keeps_the_full_event_count():
    sid = uuid.uuid4()
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(DATES_CAP + 5)]
    found = [Finding(ISLAND, (d,), 8.0) for d in days] + [Finding(CLIFF, (days[0],), 3.0)]
    island, cliff = fold(sid, "GGRP", found)
    assert island.kind == ISLAND and island.events == DATES_CAP + 5
    assert island.dates == tuple(days[-DATES_CAP:])  # the most recent ones
    assert cliff.kind == CLIFF and cliff.events == 1
    assert island.key == f"island:{sid}" and island.label == f"GGRP island x8.0 ({days[-1]})"
    assert (
        SplitDefect(sid, None, FLAP, (), 20.0, 3).label == f"{sid} flap x20"
    )  # ticker-less: by id


def test_the_window_covers_the_widest_price_lookback_on_the_call_path():
    from domain.config import DEFAULT_CONFIG as c

    assert window_days() == max(
        c.breakout_lookback_days,
        c.breakout_52w_lookback_days,
        c.laggard_lookback_days,
        c.breakdown_core_lookback_days,
    )


def test_the_settle_lookback_is_the_longest_DECLARED_price_horizon_of_any_reader():
    """Read off the horizon registry (call path and display columns alike), never shorter than the window."""
    from domain.config import DEFAULT_CONFIG as c
    from signals.horizons import call_horizons, display_horizons

    declared = [
        h["fact_price_eod"]
        for h in (*call_horizons(c), *display_horizons())
        if h.get("fact_price_eod") is not None
    ]
    assert view_lookback_days() == max(declared) >= window_days()


# --- the SETTLE check: a flap no reader sees on two bases (direct-input contract cases) ------------------
#
# Each case is a version history built for the rule it pins (no real tape needed: the rule is a statement
# about which version each night's view picks). Instants are UTC: a bar's first version is public at its
# session settle, a re-version from its own recorded_at (db.public_clock's plain price rule); a night's view
# is known through the end of that day.

_D0 = date(2026, 6, 1)
_BARS = 40
_ASOF_S = date(2026, 8, 15)
_T1 = datetime(2026, 7, 20, 2, 30, tzinfo=timezone.utc)  # the vendor re-bases (night of 07-19 ET)
_T2 = datetime(2026, 7, 25, 2, 30, tzinfo=timezone.utc)  # ... and flips it back


def _settle(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 22, 0, tzinfo=timezone.utc)


def _night(n: date) -> datetime:
    return datetime(n.year, n.month, n.day, 23, 59, tzinfo=timezone.utc)


def _flipping(rebase_at, *, publics_at_settle: bool = False, k: float = 10.0):
    """``_BARS`` smooth daily bars (~5.0 on 1M shares), each stored once the next morning, then re-based by
    ``k`` at ``rebase_at(i)`` (None = never) and flipped back at ``_T2`` — every flipped bar FLAPS. With
    ``publics_at_settle`` every version is public at the bar's settle (the listed-split rule the SQL applies
    to a bar before a listed split)."""
    out: dict[date, list[PublicVersion]] = {}
    for i in range(_BARS):
        d = _D0 + timedelta(days=i)
        c = 5.0 + 0.05 * (i % 3)
        stored = _settle(d) + timedelta(hours=4, minutes=30)
        vs = [(stored, c)]
        at = rebase_at(i)
        if at is not None:
            vs += [(at, c * k), (_T2, c)]
        out[d] = [
            PublicVersion(
                _settle(d) if (publics_at_settle or j == 0) else max(_settle(d), rec),
                rec,
                uuid.uuid4(),
                close,
                1e6,
            )
            for j, (rec, close) in enumerate(vs)
        ]
    return out


def _flap_dates(versions):
    hist = {d: [BarVersion(v.recorded_at, v.close) for v in vs] for d, vs in versions.items()}
    (f,) = flap_findings(hist, flap_since=_ASOF_S - timedelta(days=90))
    return f.dates


def _verdict(versions, **kw):
    return settle_flap(
        _flap_dates(versions),
        versions,
        asof=_ASOF_S,
        window=430,
        lookback=600,
        known_at=_night,
        **kw,
    )


def test_a_WHOLESALE_re_base_is_SETTLED_every_night_read_one_basis():
    """The vendor re-based EVERY bar at once and back: the stored history flaps, yet each night's view sat on
    one basis (x1 before, x10 on every bar between the flips, x1 after)."""
    versions = _flipping(lambda i: _T1)
    v = _verdict(versions)
    assert v.settled is True and v.mixed is None
    assert v.reason.startswith(SETTLED_REASON) and v.nights == 431


def test_a_PARTIAL_re_base_that_leaves_a_night_MIXED_stays_a_defect():
    """The vendor re-based the 10 oldest bars on 07-20 and the other 30 only on 07-22: the views of 07-20 and
    07-21 read 10 bars at x10 beside 30 at x1 — two bases in one lookback. The defect names that night.
    """
    versions = _flipping(lambda i: _T1 if i < 10 else _T1 + timedelta(days=2))
    v = _verdict(versions)
    assert v.settled is False and v.mixed is not None
    assert v.mixed.night == date(2026, 7, 20) and v.mixed.nights == 2
    assert v.mixed.events == 10 and v.mixed.dates == tuple(
        _D0 + timedelta(days=i) for i in range(10)
    )
    assert v.mixed.majority == pytest.approx(1.0) and v.mixed.other == pytest.approx(10.0)
    assert "the history view of 2026-07-20 read 10 bar(s)" in v.reason and "x10 apart" in v.reason


def test_the_same_partial_re_base_on_bars_a_LISTED_SPLIT_covers_is_SETTLED():
    """db.public_clock puts every version of a bar before a listed split at its session settle, so every
    night's view reads the bar's NEWEST version — the partial re-base never reaches a view."""
    versions = _flipping(
        lambda i: _T1 if i < 10 else _T1 + timedelta(days=2), publics_at_settle=True
    )
    assert _verdict(versions).settled is True


def test_a_NEWEST_TAPE_cliff_keeps_the_flap_a_defect():
    """What the live call reads: the newest tape steps up x10 on a /10 volume at bar 20 (an unadjusted
    reverse split, T4's cliff). A flap elsewhere on that name is not settled — the live tape mixes bases.
    """
    versions = _flipping(lambda i: _T1 if i == 5 else None)
    for i in range(20, _BARS):
        d = _D0 + timedelta(days=i)
        versions[d] = [replace_close(v, v.close * 10, 1e5) for v in versions[d]]
    assert [f.kind for f in tape_findings(_newest_tape(versions))] == [CLIFF]
    v = _verdict(versions)
    assert v.settled is False and v.mixed is None
    assert "newest-version tape itself carries a basis break" in v.reason and "cliff" in v.reason


def test_a_flapping_bar_whose_NEWEST_version_sits_a_basis_off_its_neighbors_is_a_defect():
    """Flipped up, back, and up again: its newest version reads x10 beside x1 neighbors on the live tape,
    on flat volume — the level gates do not judge it, the bar-to-neighbor rule does."""
    versions = _flipping(lambda i: None)
    d = _D0 + timedelta(days=5)
    (v1,) = versions[d]
    versions[d] = [
        v1,
        PublicVersion(_T1, _T1, uuid.uuid4(), 50.0, 1e6),
        PublicVersion(_T1 + timedelta(days=1), _T1 + timedelta(days=1), uuid.uuid4(), 5.0, 1e6),
        PublicVersion(_T2, _T2, uuid.uuid4(), 50.0, 1e6),
    ]
    assert tape_findings(_newest_tape(versions)) == []
    v = _verdict(versions)
    assert v.settled is False and "the newest version of 2026-06-06 sits a basis away" in v.reason


def test_the_verdict_is_the_VIEWS_on_the_view_scale_the_evidence_is_put_on_it():
    """on_view_scale multiplies every version of one bar by the same factor, so the raw ratio IS the view's
    ratio; the evidence reads the off bar on that night's scale (a 1-for-2 split listed after the tape: the
    07-20 view, before it, reads the stored post-split values / 2)."""
    from decimal import Decimal

    versions = _flipping(lambda i: _T1 if i < 10 else _T1 + timedelta(days=2))
    split = {
        "key_date": date(2026, 8, 3),
        "effective_date": date(2026, 8, 3),
        "ratio_from": Decimal(2),
        "ratio_to": Decimal(1),
        "status": "confirmed",
    }
    from domain.splits import STORE_THROUGH_COL, on_view_scale

    store = date(2026, 8, 14)
    # the property the verdict rests on: the 07-20 view's picks and the newest tape, both put on that
    # night's scale by the history views' own conversion, keep every bar's raw version ratio
    k = _night(date(2026, 7, 20))
    picks, refs = [], []
    for d, vs in sorted(versions.items()):
        ordered = sorted(vs, key=lambda v: (v.public, v.recorded_at, v.vid))
        pick = [v for v in ordered if v.public <= k][-1]
        newest = max(vs, key=lambda v: (v.recorded_at, v.vid))
        picks.append({"d": d, "close": pick.close, STORE_THROUGH_COL: store})
        refs.append({"d": d, "close": newest.close, STORE_THROUGH_COL: store})
    on_view = on_view_scale(picks, [split], through=date(2026, 7, 20))
    ref_view = on_view_scale(refs, [split], through=date(2026, 7, 20))
    assert on_view[0]["close"] == pytest.approx(picks[0]["close"] / 2)  # the conversion did apply
    for p, r, pv, rv in zip(picks, refs, on_view, ref_view):
        assert pv["close"] / rv["close"] == pytest.approx(p["close"] / r["close"])

    plain = _verdict(versions)
    scaled = _verdict(versions, split_rows=[split], store_through=store)
    assert scaled.settled is plain.settled is False
    assert scaled.mixed.night == plain.mixed.night and scaled.mixed.factor == plain.mixed.factor
    d, seen, ref = scaled.mixed.example
    assert (
        d == _D0 and seen == pytest.approx(25.0) and ref == pytest.approx(2.5)
    )  # x10 vs x1, both / 2


def replace_close(v: PublicVersion, close: float, volume: float) -> PublicVersion:
    return PublicVersion(v.public, v.recorded_at, v.vid, close, volume)


def _newest_tape(versions):
    return [
        TapeBar(d, n.close, n.volume)
        for d in sorted(versions)
        for n in [max(versions[d], key=lambda v: (v.recorded_at, v.vid))]
    ]


# --- the nightly integration (pure parts) ----------------------------------------------------------------


def _defect(ticker="VWAV", kind=ISLAND, sid=None):
    return SplitDefect(
        sid or uuid.uuid4(), ticker, kind, (date(2026, 9, 15), date(2026, 9, 16)), 18.02, 2
    )


def test_the_split_BASELINE_is_the_newest_live_FINISHED_pass_that_scanned_a_catch_up_included(
    tmp_path,
):
    """`cron_run_log.previous_split_defects`: every newer artifact below is one the diff must SKIP — a
    failed scan, a --no-live pass, a pre-T4 artifact, and a killed pass's start artifact (M1) — so the
    baseline stays the older full pass. A catch-up that SKIPPED a thesis (M2) is NOT skipped — it is the
    baseline in WHOLE (the stale-tape / name-error twins count it only for the theses it covered, the
    per-thesis carry-forward): the scan reads the whole basket union, so its inventory is complete (the
    re-page that excluding it caused is pinned by the catch-up sequence tests at the end of this file).
    """
    a, b, c = _defect(), _defect("MPU", FLAP), _defect("BTOC", CLIFF)
    t0 = datetime(2026, 10, 13, 2, 30, tzinfo=timezone.utc)

    def _write(i, *, split, allow_live=True, results=()):
        at = t0 + timedelta(hours=i)
        kw = {
            "asof": date(2026, 10, 12),
            "allow_live": allow_live,
            "started_at": at,
            "finished_at": at,
        }
        path = cron_run_log.write_cron_run_log(list(results), **kw, base_dir=tmp_path, split=split)
        assert path is not None
        return path

    assert cron_run_log.previous_split_defects(base_dir=tmp_path) is None  # nothing scanned yet
    _write(0, split=SplitScan(evaluated=True, defects=(a, b)))  # the baseline
    _write(1, split=SplitScan(error="OperationalError: down"))  # a failed scan
    _write(2, split=SplitScan(evaluated=True, defects=(c,)), allow_live=False)  # --no-live
    pre_t4 = _write(3, split=None)  # strip the keys: written before T4
    doc = json.loads(pre_t4.read_text(encoding="utf-8"))
    pre_t4.write_text(
        json.dumps({k: v for k, v in doc.items() if not k.startswith("split_")}), encoding="utf-8"
    )
    started = t0 + timedelta(hours=4)
    assert cron_run_log.write_run_start(
        asof=date(2026, 10, 12), allow_live=True, started_at=started, base_dir=tmp_path
    )  # M1 — a pass that never finished

    assert cron_run_log.previous_split_defects(base_dir=tmp_path) == {a.key, b.key}

    skipped = daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", skipped="on-time row stands")
    _write(5, split=SplitScan(evaluated=True, defects=(c,)), results=[skipped])  # M2 catch-up
    assert cron_run_log.previous_split_defects(base_dir=tmp_path) == {c.key}  # IS the baseline

    _write(6, split=SplitScan(evaluated=True))  # a newer clean, finished, full pass IS the baseline
    assert cron_run_log.previous_split_defects(base_dir=tmp_path) == set()


def test_defect_keys_of_skips_a_malformed_row():
    d = _defect()
    doc = SplitScan(evaluated=True, defects=(d,)).payload()
    doc["split_defects"] += [{"kind": "island"}, "junk", {"security_id": "x"}]
    assert defect_keys_of(doc) == {d.key}
    assert defect_keys_of({}) == set()


def test_new_labels_of_reads_what_the_pass_paged():
    d = _defect()
    doc = SplitScan(evaluated=True, defects=(d,), new=(d,)).payload()
    assert new_labels_of(doc) == (d.label,)
    assert new_labels_of({}) == ()


def test_run_split_scan_does_not_scan_on_no_live_and_is_FAIL_OPEN_on_a_fault():
    def _never():
        raise AssertionError("a --no-live pass must not open a connection")

    off = run_split_scan(
        asof=date(2026, 10, 16), allow_live=False, connect=_never, previous=_never, clock=lambda: 0
    )
    assert off.evaluated is False and off.error is None and off.defects == ()

    def _down():
        raise OSError("connection refused")

    broken = run_split_scan(
        asof=date(2026, 10, 16), allow_live=True, connect=_down, previous=_never, clock=lambda: 0
    )
    assert broken.evaluated is False and broken.error == "OSError: connection refused"


def test_report_lines_new_LOUD_known_QUIET_failure_LOUD_clean_SILENT():
    old, new = _defect("GGRP"), _defect("VWAV")
    lines = report_lines(SplitScan(evaluated=True, defects=(old, new), new=(new,)))
    assert lines[0].startswith("SPLIT-BASIS DEFECTS — NEW")
    assert lines[1].startswith("  VWAV island x18") and "2026-09-15, 2026-09-16" in lines[1]
    assert lines[2].startswith("1 known split-basis defect(s)")
    assert report_lines(SplitScan(evaluated=True)) == []
    assert report_lines(SplitScan()) == []  # --no-live: nothing scanned, nothing said
    assert report_lines(SplitScan(error="OSError: x"))[0].startswith("SPLIT-BASIS MONITOR FAILED")


def _audit_ctx(*, split, allow_live=True):
    return AuditContext(
        asof=date(2026, 10, 16),
        started_at=datetime(2026, 10, 17, 2, 31, tzinfo=timezone.utc),
        allow_live=allow_live,
        run_kind="cron",
        code_sha="abc",
        results=[],
        run_at=datetime(2026, 1, 1, 22, 30).time(),
        tz=timezone.utc,
        connect=connect,
        split=split,
    )


def test_the_audit_check_REPORTS_defects_and_FAILS_only_when_the_scan_could_not_run():
    d = _defect()
    standing = check_split_defects_flagged(
        _audit_ctx(split=SplitScan(evaluated=True, defects=(d,)))
    )
    assert standing.ok is True and "1 split-basis defect(s) flagged (0 new" in standing.detail
    clean = check_split_defects_flagged(_audit_ctx(split=SplitScan(evaluated=True)))
    assert clean.ok is True and "no split-basis defect" in clean.detail
    broken = check_split_defects_flagged(_audit_ctx(split=SplitScan(error="OSError: down")))
    assert broken.ok is False and "could not run (OSError: down)" in broken.detail
    assert check_split_defects_flagged(_audit_ctx(split=None)).ok is None
    assert check_split_defects_flagged(_audit_ctx(split=SplitScan(), allow_live=False)).ok is None


def test_assess_health_PAGES_a_new_defect_as_a_benign_flag():
    h = daily.assess_health(
        [], asof=date(2026, 10, 16), allow_live=True, split_defects_new=("VWAV island x18",)
    )
    assert h is not None and h.split_defects_new == ("VWAV island x18",)
    assert "1 NEW split-basis defect(s) on price tapes: VWAV island x18" in h.label
    assert "not a cron error" in h.label
    assert daily.assess_health([], asof=date(2026, 10, 16), allow_live=True) is None


def test_the_artifact_carries_the_split_keys_and_a_None_scan_reads_not_evaluated():
    d = _defect()
    at = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)
    kw = {"asof": date(2026, 10, 16), "allow_live": True, "started_at": at, "finished_at": at}
    doc = cron_run_log.build_run_payload(
        [], **kw, split=SplitScan(True, (d,), (d,), None, 430, 1.5)
    )
    assert doc["split_evaluated"] is True and doc["split_window_days"] == 430
    assert doc["split_defects"] == [d.to_json()]
    assert doc["split_defects_new"][0]["label"] == d.label
    bare = cron_run_log.build_run_payload([], **kw)
    assert bare["split_evaluated"] is False and bare["split_defects"] == []


# --- the scan, on a real DB ------------------------------------------------------------------------------


def _security(db, ticker):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, f"{uuid.uuid4().int % 10**10:010d}", "2025-01-01"),
        )
    db.commit()
    return sid


def _thesis(db, name, members, *, archived=False):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", datetime.now(timezone.utc) if archived else None),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _load(db, sid, ticker):
    """Store a fixture case's bars: every recorded version of a multi-version bar at its REAL stamp, and a
    single-version bar once (its stamp only has to sort; it is the bar's only version)."""
    case = _DOC[ticker]
    with db.cursor() as cur:
        for d, close, volume in case["tape"]:
            versions = case["versions"].get(d)
            rows = (
                [(datetime.fromisoformat(ra), c, v) for ra, c, v in versions]
                if versions
                else [
                    (
                        datetime.fromisoformat(d).replace(tzinfo=timezone.utc) + timedelta(days=1),
                        close,
                        volume,
                    )
                ]
            )
            for recorded_at, c, v in rows:
                cur.execute(
                    "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, "
                    "recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (DEFAULT_TENANT_ID, sid, d, c, v, d, recorded_at),
                )
    db.commit()


def _count(db, table="fact_price_eod"):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        n = cur.fetchone()["n"]
    db.commit()
    return n


def test_the_scan_reads_the_ACTIVE_basket_union_and_WRITES_NOTHING(db):
    vwav, mpu, clean, gone = (_security(db, t) for t in ("VWAV", "MPU", "MYPS", "GONE"))
    _load(db, vwav, "VWAV")
    _load(db, mpu, "MPU")
    _load(db, clean, "MYPS")
    _load(db, gone, "VWAV")  # the same defective tape, but only an ARCHIVED thesis holds it
    _thesis(db, "active", [("VWAV", vwav), ("MYPS", clean), ("VWAV", vwav)])  # VWAV in two links
    _thesis(db, "active-2", [("MPU", mpu), ("UNRESOLVED", None)])
    _thesis(db, "archived", [("GONE", gone)], archived=True)
    before = _count(db)

    found = scan_split_defects(db, asof=date(2026, 10, 16))
    db.rollback()

    assert _count(db) == before  # count the table: the monitor never writes
    assert {(d.ticker, d.kind) for d in found} == {("MPU", FLAP), ("VWAV", ISLAND), ("VWAV", FLAP)}
    (island,) = [d for d in found if d.kind == ISLAND]
    assert island.security_id == vwav and island.dates == (date(2026, 9, 15), date(2026, 9, 16))
    assert [d.ticker for d in found] == sorted(d.ticker for d in found)  # ordered by ticker


def test_run_split_scan_is_READ_ONLY_and_pages_only_what_is_NEW(db):
    vwav = _security(db, "VWAV")
    _load(db, vwav, "VWAV")
    _thesis(db, "active", [("VWAV", vwav)])
    opened = []

    def _connect():
        c = connect()
        opened.append(c)
        return c

    first = run_split_scan(
        asof=date(2026, 10, 16),
        allow_live=True,
        connect=_connect,
        previous=lambda: None,
        clock=lambda: 0,
    )
    assert first.evaluated and first.error is None
    assert {d.kind for d in first.defects} == {ISLAND, FLAP} and first.new == first.defects
    assert opened[0].read_only is True and opened[0].closed  # its own read-only connection, closed

    known = {d.key for d in first.defects}
    second = run_split_scan(
        asof=date(2026, 10, 16),
        allow_live=True,
        connect=connect,
        previous=lambda: known,
        clock=lambda: 0,
    )
    assert second.new == () and second.known == len(first.defects)


def test_the_PASS_writes_the_inventory_PAGES_new_defects_ONCE_and_audits(db, monkeypatch, tmp_path):
    """End to end through run_daily_pass on a real DB: the scan runs after the loop, rides the artifact,
    the new defects page once, and the NEXT pass over the same tapes pages nothing for them."""
    from domain.settings import get_settings

    vwav = _security(db, "VWAV")
    _load(db, vwav, "VWAV")
    _thesis(db, "split-thesis-name", [("VWAV", vwav)])
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    monkeypatch.setenv("ALPHADECK_IMAGE_SHA", "t4testsha")  # a known sha: the identity check passes
    get_settings.cache_clear()
    for target in (
        "pipeline.ingest_benchmarks.ingest_benchmarks",
        "pipeline.ingest_fundamentals.ingest_fundamentals",
    ):
        monkeypatch.setattr(target, lambda conn, **k: [])
    for target in ("radar.shell_sweep.run_shell_sweep", "radar.spac.run_spac_radar"):
        monkeypatch.setattr(target, lambda conn, **k: SimpleNamespace(summary="s", errors=[]))
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    pages = []
    notifier = SimpleNamespace(
        notify=lambda e: None, notify_health=pages.append, notify_spac_status=lambda e: None
    )
    asof = date(2026, 10, 16)
    try:
        first = daily.run_daily_pass(asof=asof, allow_live=True, notifier=notifier, run_kind="cron")
        doc1 = cron_run_log.list_run_logs(base_dir=tmp_path)[0]  # newest first
        second = daily.run_daily_pass(
            asof=asof, allow_live=True, notifier=notifier, run_kind="cron"
        )
        doc2 = cron_run_log.list_run_logs(base_dir=tmp_path)[0]
    finally:
        get_settings.cache_clear()

    assert {d.kind for d in first.split.new} == {ISLAND, FLAP}
    assert doc1["split_evaluated"] is True
    assert {d["kind"] for d in doc1["split_defects"]} == {ISLAND, FLAP}
    assert len(doc1["split_defects_new"]) == 2
    assert second.split.new == () and second.split.known == 2  # diffed against the first artifact
    assert doc2["split_evaluated"] is True and doc2["split_defects_new"] == []
    split_pages = [p for p in pages if p.split_defects_new]
    assert len(split_pages) == 1 and "VWAV island" in split_pages[0].label
    assert "split-thesis-name" not in split_pages[0].label  # a page never names a thesis
    audit = {a.check: a for a in first.audit}["split_defects_flagged"]
    assert audit.ok is True and "2 split-basis defect(s) flagged (2 new" in audit.detail


# --- the M2 catch-up and the split baseline: a defect pages ONCE across a skipped-thesis catch-up --------
#
# THE REAL SEQUENCE, on a real DB and real tapes (VWAV's half-adjusted island + flap, MYPS's clean re-base):
# each pass runs the split leg exactly as `_run_daily_pass_locked` does — `run_split_scan` over the active
# basket union, diffed against `cron_run_log.previous_split_defects`, then the run artifact written with
# the pass's ROSTER (a `--catch-up` that found an on-time row for a thesis records it `skipped`, M2).

_T4_ASOF = date(2026, 10, 16)
_T0 = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)


def _split_pass(tmp_path, hour, roster):
    """One pass's split leg + its artifact. ``roster`` = [(thesis_id, skipped_reason | None)]."""
    scan = run_split_scan(
        asof=_T4_ASOF,
        allow_live=True,
        connect=connect,
        previous=lambda: cron_run_log.previous_split_defects(base_dir=tmp_path),
        clock=lambda: 0,
    )
    at = _T0 + timedelta(hours=hour)
    results = [
        daily.ThesisRunResult(thesis_id=tid, name="T", recorded=None if why else False, skipped=why)
        for tid, why in roster
    ]
    assert cron_run_log.write_cron_run_log(
        results,
        asof=_T4_ASOF,
        allow_live=True,
        started_at=at,
        finished_at=at,
        base_dir=tmp_path,
        split=scan,
    )
    return scan


_ON_TIME = "on-time call-of-record already recorded"


def test_a_catch_up_that_SKIPS_the_defects_thesis_does_not_make_the_next_full_pass_RE_PAGE(
    db, tmp_path
):
    """The spec's sequence: a full pass pages D -> a --catch-up that skips D's thesis -> the next full pass
    must NOT page D."""
    vwav, myps = _security(db, "VWAV"), _security(db, "MYPS")
    _load(db, vwav, "VWAV")
    _load(db, myps, "MYPS")
    t_vwav = _thesis(db, "holds VWAV", [("VWAV", vwav)])
    t_myps = _thesis(db, "holds MYPS", [("MYPS", myps)])
    full = [(t_vwav, None), (t_myps, None)]

    first = _split_pass(tmp_path, 0, full)
    assert {(d.ticker, d.kind) for d in first.new} == {("VWAV", ISLAND), ("VWAV", FLAP)}  # paged
    catch_up = _split_pass(tmp_path, 10, [(t_vwav, _ON_TIME), (t_myps, None)])
    assert catch_up.new == () and catch_up.known == 2
    assert _split_pass(tmp_path, 20, full).new == ()  # NOT paged again


def test_a_defect_FIRST_SEEN_by_a_catch_up_pages_once_not_again_at_the_next_full_pass(db, tmp_path):
    """The re-page the M2 roster rule caused: the catch-up's scan reads the WHOLE union (a skipped thesis's
    names included), so its inventory is complete — yet it was excluded as a baseline, and the next full pass
    diffed against the pass BEFORE the catch-up and paged its defect a second time. VWAV's tape lands while
    the catch-up runs (its thesis is processed; the other thesis is skipped)."""
    vwav, myps = _security(db, "VWAV"), _security(db, "MYPS")
    _load(db, myps, "MYPS")
    t_vwav = _thesis(db, "holds VWAV", [("VWAV", vwav)])
    t_myps = _thesis(db, "holds MYPS", [("MYPS", myps)])
    full = [(t_vwav, None), (t_myps, None)]

    assert _split_pass(tmp_path, 0, full).defects == ()  # no VWAV tape stored yet
    _load(db, vwav, "VWAV")  # the catch-up's price leg stores VWAV's tape
    catch_up = _split_pass(tmp_path, 10, [(t_vwav, None), (t_myps, _ON_TIME)])
    assert {(d.ticker, d.kind) for d in catch_up.new} == {("VWAV", ISLAND), ("VWAV", FLAP)}  # paged
    nxt = _split_pass(tmp_path, 20, full)
    assert nxt.new == () and nxt.known == 2  # paged ONCE


def test_a_defect_genuinely_CLEARED_during_a_catch_up_and_then_BACK_pages_again(db, tmp_path):
    """The other side of "pages once": a defect that left the inventory (here the operator archived the
    only thesis holding VWAV before the catch-up) and came back (restored before the next pass) is news
    again — the catch-up's complete inventory is the baseline, so its absence there is real."""
    vwav, myps = _security(db, "VWAV"), _security(db, "MYPS")
    _load(db, vwav, "VWAV")
    _load(db, myps, "MYPS")
    t_vwav = _thesis(db, "holds VWAV", [("VWAV", vwav)])
    t_myps = _thesis(db, "holds MYPS", [("MYPS", myps)])
    second = _thesis(db, "also MYPS", [("MYPS", myps)])

    assert len(_split_pass(tmp_path, 0, [(t_vwav, None), (t_myps, None), (second, None)]).new) == 2
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET archived_at = now() WHERE id = %s", (t_vwav,))
    db.commit()
    catch_up = _split_pass(tmp_path, 10, [(t_myps, _ON_TIME), (second, None)])
    assert catch_up.defects == ()  # cleared from the union
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET archived_at = NULL WHERE id = %s", (t_vwav,))
    db.commit()
    back = _split_pass(tmp_path, 20, [(t_vwav, None), (t_myps, None), (second, None)])
    assert {(d.ticker, d.kind) for d in back.new} == {("VWAV", ISLAND), ("VWAV", FLAP)}


# --- the SETTLE check on a real DB: the history views' own public-clock SQL picks the versions ----------
#
# The same direct-input layouts as the pure cases above, stored as rows (each version at its real stamp);
# the public instants now come from ``db.public_clock`` itself — a first version at its 18:00 ET settle, a
# re-version from its recorded_at, and every version of a bar before a LISTED split at its settle.

_SPLIT_DAY = date(2026, 7, 13)


def _store_flipping(db, sid, *, partial: bool, listed: bool):
    """40 smooth bars 06-01..07-10, re-based x10 (all at _T1, or the 10 oldest at _T1 and the rest two days
    later) and flipped back at _T2. ``listed``: 5 post-split bars 07-13..07-17 (stored once) and a confirmed
    1-for-2 split on the list at 07-13 — the stored tape already on its scale (continuous)."""
    from repositories import split_repo

    rows = []
    for i in range(_BARS):
        d = _D0 + timedelta(days=i)
        c = 5.0 + 0.05 * (i % 3)
        rows.append((d, c, 1e6, _settle(d) + timedelta(hours=4, minutes=30)))
        at = _T1 if (not partial or i < 10) else _T1 + timedelta(days=2)
        rows += [(d, c * 10, 1e5, at), (d, c, 1e6, _T2)]
    if listed:
        for i in range(5):
            d = _SPLIT_DAY + timedelta(days=i)
            rows.append((d, 5.0 + 0.05 * (i % 3), 1e6, _settle(d) + timedelta(hours=4, minutes=30)))
    with db.cursor() as cur:
        for d, c, v, at in rows:
            cur.execute(
                "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, "
                "recorded_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, sid, d, c, v, d, at),
            )
    if listed:
        split_repo.append_version(
            db,
            tenant_id=DEFAULT_TENANT_ID,
            security_id=sid,
            key_date=_SPLIT_DAY,
            values={
                "effective_date": _SPLIT_DAY,
                "ratio_from": 2,
                "ratio_to": 1,
                "status": "confirmed",
                "confirmed_via": "auto:sec+polygon",
                "value_source": "both",
                "valid_from": _SPLIT_DAY,
            },
            action="backfill",
            author="system:test",
        )
    db.commit()


@pytest.mark.parametrize(
    "partial, listed, settled",
    [
        (False, False, True),  # a wholesale re-base: every night read one basis
        (True, False, False),  # a partial one: the 07-20 / 07-21 views read two
        (
            True,
            True,
            True,
        ),  # the same partial one on bars a listed split covers: newest read every night
    ],
)
def test_the_settle_check_reads_the_PUBLIC_CLOCK_and_WRITES_NOTHING(db, partial, listed, settled):
    sid = _security(db, "FLIP")
    _store_flipping(db, sid, partial=partial, listed=listed)
    _thesis(db, "active", [("FLIP", sid)])
    before = (_count(db), _count(db, "security_split"))

    defects = scan_split_defects(db, asof=_ASOF_S)
    assert [(d.ticker, d.kind, d.events) for d in defects] == [("FLIP", FLAP, _BARS)]
    kept, done = settle_split_defects(db, defects, asof=_ASOF_S)
    db.rollback()

    assert (_count(db), _count(db, "security_split")) == before  # count the tables: SELECTs only
    if settled:
        assert kept == () and [s.defect for s in done] == list(defects)
        assert done[0].reason.startswith(SETTLED_REASON)
    else:
        assert done == () and len(kept) == 1
        # _T1 (02:30 UTC on 07-20) is 22:30 ET on Sunday 07-19. Since F6 a night runs to EDGAR's next open
        # (the next weekday's 06:00, ``known_at_for_asof``), so Friday 07-17's night — like the weekend's —
        # runs to Monday 06:00 and already reads the 10 re-based bars; the rest land at _T1 + 2 days (Tuesday
        # 22:30), so Friday through Monday read two bases (before F6: Sunday and Monday, 2 nights)
        assert "the history view of 2026-07-17 read 10 bar(s)" in kept[0].evidence
        assert "(4 night(s) mixed)" in kept[0].evidence
        assert kept[0].to_json()["evidence"] == kept[0].evidence  # rides the artifact


def test_a_SETTLED_flap_never_pages_never_counts_and_the_audit_still_PASSES(db):
    """The first pass after deploy treats every row as news (``previous`` = None) — and still pages nothing
    for a settled flap: it is named (artifact, report, audit detail), never a defect."""
    sid = _security(db, "FLIP")
    _store_flipping(db, sid, partial=True, listed=True)
    _thesis(db, "active", [("FLIP", sid)])

    scan = run_split_scan(
        asof=_ASOF_S, allow_live=True, connect=connect, previous=lambda: None, clock=lambda: 0
    )

    assert scan.evaluated and scan.error is None and scan.settle_error is None
    assert scan.defects == () and scan.new == () and scan.known == 0
    assert [s.defect.ticker for s in scan.settled] == ["FLIP"]
    doc = scan.payload()
    assert doc["split_defects"] == [] and doc["split_defects_new"] == []
    assert doc["split_settled"][0]["ticker"] == "FLIP" and doc["split_settled"][0]["reason"]
    assert defect_keys_of(doc) == set()  # never in the next pass's diff baseline
    audit = check_split_defects_flagged(_audit_ctx(split=scan))
    assert audit.ok is True and "1 settled flap(s)" in audit.detail
    assert (
        daily.assess_health([], asof=_ASOF_S, allow_live=True, split_defects_new=scan.new_labels)
        is None
    )
    (line,) = report_lines(scan)
    assert line.startswith("1 settled split-basis flap(s)") and "no view is affected" in line


def test_a_settle_check_FAULT_keeps_every_flap_LOUD(db, monkeypatch):
    sid = _security(db, "FLIP")
    _store_flipping(db, sid, partial=False, listed=False)
    _thesis(db, "active", [("FLIP", sid)])

    def _boom(*a, **k):
        raise RuntimeError("settle read failed")

    monkeypatch.setattr("pipeline.split_basis.settle_split_defects", _boom)
    scan = run_split_scan(
        asof=_ASOF_S, allow_live=True, connect=connect, previous=lambda: None, clock=lambda: 0
    )
    assert (
        scan.evaluated
        and scan.settled == ()
        and scan.settle_error == "RuntimeError: settle read failed"
    )
    assert [d.kind for d in scan.new] == [
        FLAP
    ]  # it pages exactly as before the settle check existed
    assert any(
        line.startswith("split-basis settle check could not run") for line in report_lines(scan)
    )


@pytest.mark.parametrize("partial, drawn", [(False, False), (True, True)])
def test_the_DATA_FLAG_applies_the_same_settled_rule(db, partial, drawn):
    """S3's compute-on-read ⚑ reads the same T4 flap findings; with no split on the list the S2 per-bar rule
    settles nothing, so T4's name-level check decides: a wholesale re-base draws no mixed-scale flag, a
    partial one that left a night mixed does."""
    from domain.data_flags import MIXED_SCALE
    from pipeline.data_flags import read_flags

    sid = _security(db, "FLIP")
    _store_flipping(db, sid, partial=partial, listed=False)
    before = _count(db)
    flags = read_flags(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_ids=[sid],
        tickers={sid: "FLIP"},
        asof=_ASOF_S,
        known_at=None,
        clock="record",
        stale_days=5,
    )
    db.rollback()
    assert _count(db) == before
    assert (MIXED_SCALE in [f.kind for f in flags[sid].flags]) is drawn


def test_the_settled_payload_and_report_name_each_row():
    d = SplitDefect(uuid.uuid4(), "MPU", FLAP, (date(2026, 9, 14),), 20.0, 270)
    s = SettledDefect(d, f"{SETTLED_REASON} (431 night(s) checked)")
    doc = SplitScan(evaluated=True, settled=(s,)).payload()
    assert doc["split_settled"] == [{**d.to_json(), "reason": s.reason}]
    assert "evidence" not in doc["split_settled"][0]  # only a KEPT flap carries evidence
    assert SplitScan().payload()["split_settled"] == []

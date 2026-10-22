"""The data flags' reader (split plan S3, ``pipeline/data_flags.py``) on REAL views of prod's data — the
``tests/fixtures/splits/s3`` extracts (see its README): each security's tape, split rows, share count and bar
versions exactly as ``PointInTimeData`` read them on a read-only copy of prod (2026-10-22 16:14 UTC).
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from uuid import UUID

import pytest

from domain import data_flags as m
from pipeline import data_flags as df
from pipeline.split_basis import BarVersion, TapeBar, flap_findings

FX = Path(__file__).resolve().parents[1] / "fixtures" / "splits" / "s3"
STALE_DAYS = 5  # ``Settings.tape_stale_days``


@cache
def _views() -> list[dict]:
    with gzip.open(FX / "views.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)["views"]


def _dec(v):
    return None if v is None else Decimal(v)


def _date(v):
    return None if v is None else date.fromisoformat(v[:10])


def view(ticker: str, asof: str) -> dict:
    v = next(x for x in _views() if x["ticker"] == ticker and x["asof"] == asof)
    bars = [
        {"d": date.fromisoformat(d), "close": _dec(c), "volume": _dec(vol)}
        for d, c, vol in v["bars"]
    ]
    splits = []
    for r in v["splits"]:
        row = dict(r)
        for k in ("key_date", "effective_date", "sec_filed", "polygon_execution_date"):
            row[k] = _date(row.get(k))
        for k in ("ratio_from", "ratio_to"):
            row[k] = _dec(row[k])
        splits.append(row)
    shares = None
    if v["shares"]:
        shares = {**v["shares"], "valid_from": _date(v["shares"]["valid_from"])}
    versions = {
        date.fromisoformat(d): [
            BarVersion(datetime.fromisoformat(at), None if c is None else float(c)) for at, c in vs
        ]
        for d, vs in v["versions"].items()
    }
    return {
        "sid": UUID(v["security_id"]),
        "ticker": v["ticker"],
        "asof": date.fromisoformat(v["asof"]),
        "bars": bars,
        "splits": splits,
        "shares": shares,
        "versions": versions or None,
    }


def flags_of(ticker: str, asof: str, **kw) -> m.MemberFlags:
    v = view(ticker, asof)
    return df.member_flags(
        v["sid"],
        v["ticker"],
        bars=v["bars"],
        split_rows=v["splits"],
        asof=v["asof"],
        stale_days=STALE_DAYS,
        versions=v["versions"],
        shares=v["shares"],
        **kw,
    )


def kinds(mf: m.MemberFlags) -> list[str]:
    return [f.kind for f in mf.flags]


@cache
def _provenance() -> dict:
    return json.loads((FX / "provenance.json").read_text(encoding="utf-8"))


# --- the S2 follow-up: a repaired name's old flip history draws nothing ------------------------------------


def test_btoc_repaired_its_old_flaps_are_settled_and_it_carries_no_flag():
    v = view("BTOC", "2026-10-22")
    # the stored version history still FLAPS (T4 reads it: 291 bars on 10-21's artifact) ...
    flaps = flap_findings(v["versions"], flap_since=date(2026, 7, 24))
    assert flaps and len(flaps[0].dates) > 200
    # ... but every flapping bar's newest version sits on the split list's scale (the repair re-scaled it)
    tape = [TapeBar(b["d"], float(b["close"]), float(b["volume"] or 0)) for b in v["bars"]]
    from domain.splits import listed_from_row

    settled = df.settled_flaps(
        flaps[0].dates, tape, [listed_from_row(r) for r in v["splits"]], asof=v["asof"]
    )
    assert settled == set(flaps[0].dates)
    mf = flags_of("BTOC", "2026-10-22")
    assert mf.flags == () and not mf.marked


def test_a_flapping_bar_with_no_split_on_the_list_after_it_is_not_settled():
    v = view("BTOC", "2026-10-22")
    flaps = flap_findings(v["versions"], flap_since=date(2026, 7, 24))
    tape = [TapeBar(b["d"], float(b["close"]), float(b["volume"] or 0)) for b in v["bars"]]
    # the list read without BTOC's 2026-10-15 split: nothing normalizes the vendor's flips any more
    assert df.settled_flaps(flaps[0].dates, tape, [], asof=v["asof"]) == set()


def test_btoc_split_row_names_its_sources_and_the_repair_that_corrected_its_history(tmp_path):
    (tmp_path / "20261022T094736Z-split-basis.json").write_text(
        json.dumps(_provenance()["repair"]), encoding="utf-8"
    )
    corr = df.repair_corrections(tmp_path)
    mf = flags_of("BTOC", "2026-10-22", corrections=corr)
    (s,) = mf.splits
    assert (s.label, s.status, s.effective_date) == (
        "1-for-16 reverse split",
        "confirmed",
        date(2026, 10, 15),
    )
    assert s.confirmed_by == "the SEC filing and Polygon agree" and s.took_effect
    assert s.sec_form == "8-K" and s.sec_filed == date(2026, 10, 9) and s.factor == 16.0
    # 10-15 is the first bar at the new price; the newest bar (10-21) is four bars after it
    assert s.bars_ago == 4
    assert s.corrected_on == date(2026, 10, 22)
    assert "had not adjusted" in (s.corrected_note or "")
    # a view of a day before the repair ran never shows it (no lookahead)
    v = view("BTOC", "2026-10-22")
    past = df.split_labels(v["sid"], v["splits"], asof=date(2026, 10, 21), corrections=corr)
    assert past[0].corrected_on is None


def test_a_bar_is_credited_to_the_split_it_lacked_and_a_revoked_repair_corrects_nothing(tmp_path):
    (tmp_path / "20261022T094736Z-split-basis.json").write_text(
        json.dumps(_provenance()["repair"]), encoding="utf-8"
    )
    corr = df.repair_corrections(tmp_path)
    artl = "40d75d6c-21c1-452f-97a0-1e83bfe0a62b"
    # ARTL's one re-scaled bar (2024-09-04, x9) lacked only its 2026-08-31 1-for-9 split
    assert (artl, date(2026, 8, 31)) in corr
    assert (artl, date(2026, 3, 10)) not in corr and (artl, date(2025, 6, 13)) not in corr
    (tmp_path / "20261023T000000Z-split-basis-revoke.json").write_text(
        json.dumps(
            {"kind": df.REPAIR_REVOKE_KIND, "revokes": _provenance()["repair"]["recorded_at"]}
        ),
        encoding="utf-8",
    )
    assert df.repair_corrections(tmp_path) == {}


def test_the_repair_kinds_mirror_the_repair_tool():
    from pipeline import repair_split_basis

    assert df.REPAIR_KIND == repair_split_basis.KIND
    assert df.REPAIR_REVOKE_KIND == repair_split_basis.KIND_REVOKE
    assert df.DEFAULT_REPAIRS_DIR == repair_split_basis._DEFAULT_LOG_DIR


# --- mixed-scale history: the refused stale tails ----------------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "cliff"), [("JAGX", date(2024, 9, 17)), ("KITT", date(2024, 9, 25))]
)
def test_the_refused_stale_tail_is_a_mixed_scale_flag_that_reaches_no_cell(ticker, cliff):
    mf = flags_of(ticker, "2026-10-22")
    assert kinds(mf) == [m.MIXED_SCALE]
    (f,) = mf.flags
    assert f.level == m.IN_DOUBT and f.since == cliff and f.dates == (cliff,)
    # no column on the row reads a bar from 2024-09: the flag is real, the row's numbers are not touched
    assert f.columns == () and not mf.marked
    assert "history only" in f.reason


# --- split needs review --------------------------------------------------------------------------------


def test_mvnc_split_needs_review_reaches_the_cells_across_it_and_the_market_cap():
    mf = flags_of("MVNC", "2026-10-22")
    assert kinds(mf) == [m.SPLIT_REVIEW, m.MKTCAP_SHARES]
    review, cap = mf.flags
    assert review.since == date(2026, 10, 13) and "1-for-100 reverse split" in review.reason
    # the 2026-10-13 step sits inside every window but the 1d return's (the newest bar is 10-21)
    assert "ret_1d" not in review.columns
    assert {"ret_7d", "ret_1y", "sma", "path", "rvol8", "ins_30d"} <= set(review.columns)
    assert review.evidence and review.evidence[0].label.startswith("Polygon")
    assert cap.level == m.IN_DOUBT and cap.columns == ("mktcap",)
    assert "share count is from 2026-05-07" in cap.reason
    assert mf.marked


def test_wshp_split_predates_its_tape_so_the_flag_reaches_no_cell():
    mf = flags_of("WSHP", "2026-10-22")
    assert kinds(mf) == [m.SPLIT_REVIEW]
    (f,) = mf.flags
    assert f.since == date(2025, 10, 22) and f.columns == () and not mf.marked
    assert "SEC 6-K" in f.reason


def test_zcsh_forward_split_needs_review_on_todays_view():
    mf = flags_of("ZCSH", "2026-10-22")
    assert kinds(mf)[0] == m.SPLIT_REVIEW
    assert "3-for-1 forward split" in mf.flags[0].reason
    assert mf.splits[0].status == "needs_review" and not mf.splits[0].took_effect


# --- a stopped feed --------------------------------------------------------------------------------------


def _skyt_reading() -> df.StopReading:
    doc = {
        "asof": _provenance()["skyt_stale"]["asof"],
        "theses": [{"name": "t", "tape_stale": [_provenance()["skyt_stale"]["row"]]}],
    }
    return df.stop_readings(doc)[UUID(_provenance()["skyt_stale"]["row"]["security_id"])]


def test_skyt_stopped_trading_is_known_wrong_on_every_price_cell():
    mf = flags_of("SKYT", "2026-10-22", reading=_skyt_reading())
    (f,) = mf.flags
    assert f.kind == m.FEED_STOPPED and f.level == m.KNOWN_WRONG
    assert f.since == date(2026, 8, 7)
    assert f.reason.startswith("Stopped trading 2026-08-07: a delisting form was filed 2026-08-10")
    assert f.columns == m.PRICE_CELLS + m.VOLUME_CELLS + ("mktcap",)


def test_without_the_nightly_reading_a_stopped_feed_says_only_what_the_tape_shows():
    (f,) = flags_of("SKYT", "2026-10-22").flags
    assert f.reason.startswith("Price feed stopped: no price since 2026-08-07 (76 days)")
    # a reading of a DIFFERENT stop is not borrowed
    other = df.StopReading(edge=date(2026, 7, 1), closed_at=date(2026, 7, 2))
    (g,) = flags_of("SKYT", "2026-10-22", reading=other).flags
    assert g.reason == f.reason


# --- the S5 split-night hold, read through its own rule -------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "asof", "words"),
    [
        ("SMTK", "2026-08-20", "stepped x59 on 08-20"),
        ("ZCSH", "2026-09-29", "stepped down to 1/3.1 on 09-29"),
    ],
)
def test_the_split_night_hold_is_a_flag_with_the_call_affected(ticker, asof, words):
    pytest.importorskip("domain.split_hold")  # S5 (PR #437) — the seam until it is on main
    mf = flags_of(ticker, asof)
    f = mf.flags[0]
    assert f.kind == m.SPLIT_HOLD and f.call_affected and f.level == m.IN_DOUBT
    assert words in f.reason and "held on the call card" in f.reason
    assert f.columns == m.PRICE_CELLS + m.VOLUME_CELLS and f.since == date.fromisoformat(asof)
    assert mf.marked


# --- the run-artifact readers ----------------------------------------------------------------------------


def test_first_night_corrections_read_what_the_price_leg_writes():
    """The contract between ``ingest_thesis.split_reads_of`` (the artifact writer) and this reader."""
    from types import SimpleNamespace

    from pipeline.ingest_thesis import split_reads_of

    sid = UUID("11111111-1111-1111-1111-111111111111")
    reads = split_reads_of(
        [
            SimpleNamespace(
                security_id=sid,
                ticker="AXG",
                price_bars_split_normalized={"first_night": 380},
                price_split_first_night=("1-for-200 reverse split 2026-10-26",),
            )
        ]
    )
    doc = {"mode": "live", "asof": "2026-10-26", "split_reads": reads}
    corr = df.first_night_corrections([doc])
    c = corr[(str(sid), date(2026, 10, 26))]
    assert (c.on, c.bars, c.source) == (date(2026, 10, 26), 380, "first_night")
    assert df.first_night_corrections([{**doc, "mode": "no-live"}]) == {}

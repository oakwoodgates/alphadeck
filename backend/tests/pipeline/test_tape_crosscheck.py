"""THE TAPE CROSS-CHECK (``pipeline/tape_crosscheck.py``, W6-3a) — tonight's stored closes against Massive.com's
raw grouped day, a MONITOR: no tape write, never paged, fail-open.

The Massive.com side is REAL: ``tests/fixtures/massive/grouped/<session>.json`` are rows cut from the
2026-11-25 probe's cached grouped days (``tests/ingest/test_massive_bars.py`` says how). The stored side is the
measured shape of each case (the W6 plan's measurements, 2026-11-25): SNDK (Yahoo nulls every
post-split session of its 8-for-1 of 11-20; stored tape ends 11-19 at 94.395 = 755.16 / 8), SCNI (1-for-4 of
11-19, stored tape ends 11-18), GNS (Yahoo's 0.101 zero-volume placeholder on sessions Massive.com shows no
trade), MARA (Yahoo's official close 5.09 vs Massive.com's last trade 5.085), HCMC (Yahoo 0.0001 vs
Massive.com 0.000001, sub-penny OTC), BRK-B (Massive.com writes BRK.B). The DB test proves the read: the newest
stored version per company, the split list, one read-only connection, nothing written.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID, connect
from domain.splits import STATUS_CONFIRMED, STATUS_NEEDS_REVIEW, ListedSplit
from ingest import CacheMiss
from ingest.massive_bars import GroupedDay, MassiveBarsError, NotServedYet, parse_grouped
from pipeline import daily
from pipeline import tape_crosscheck as tc
from pipeline.cron_run_log import build_run_payload
from pipeline.ingest_thesis import NameResult
from pipeline.tape_health import StaleTape
from repositories import split_repo

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "massive" / "grouped"
NOV18, NOV19, NOV20, NOV25, NOV26 = (date(2026, 11, d) for d in (18, 19, 20, 25, 26))
TOL = 0.02  # the shipped default (Settings.tape_crosscheck_tolerance)


def _day(session: date) -> GroupedDay:
    return parse_grouped((FIX / f"{session.isoformat()}.json").read_text("utf-8"), session=session)


class Co:
    """A basket company: the ticker the master holds, its security id, its vendor price symbol (if any)."""

    def __init__(self, ticker: str | None, *, symbol: str | None = None):
        self.ticker = ticker
        self.symbol = symbol
        self.sid = uuid.uuid4()

    def name(self, *, edge: date | None = None) -> tc.Name:
        return tc.Name(self.sid, self.ticker, self.symbol, edge=edge)

    def walk(self, edge: date | None, **kw) -> NameResult:
        """Its per-name result after tonight's price leg (the tape edge, the vendor price symbol)."""
        return NameResult(
            self.ticker, self.sid, 0, 0, tape_edge=edge, price_symbol=self.symbol, **kw
        )


def _split(eff: date, frm: int, to: int, status: str = STATUS_CONFIRMED) -> ListedSplit:
    return ListedSplit(
        key_date=eff,
        effective_date=eff,
        ratio_from=Decimal(frm),
        ratio_to=Decimal(to),
        status=status,
    )


SNDK_8_FOR_1 = _split(NOV20, 1, 8)  # 8-for-1 forward, first session 2026-11-20
SCNI_1_FOR_4 = _split(NOV19, 4, 1)  # 1-for-4 reverse, first session 2026-11-19


def _judge(session, judged=(), *, stored=None, listed=None, stale=(), asof=None):
    return tc.judge(
        session=session,
        asof=asof or session,
        judged=[c.name() for c in judged],
        stale=list(stale),
        stored={k.sid: Decimal(str(v)) for k, v in (stored or {}).items()},
        massive=_day(session),
        listed={k.sid: v for k, v in (listed or {}).items()},
        tolerance=TOL,
    )


def _tickers(rows) -> list[str | None]:
    return [r.ticker for r in rows]


# --- (1) the measured shapes -----------------------------------------------------------------------------


def test_SNDK_traded_on_Massive_with_NO_stored_bar_is_a_VENDOR_GAP():
    sndk = Co("SNDK")
    out = _judge(NOV20, [sndk], listed={sndk: [SNDK_8_FOR_1]})
    (gap,) = out.vendor_gap
    assert (gap.ticker, gap.symbol, gap.ours, gap.massive) == ("SNDK", "SNDK", None, 79.24)
    assert gap.volume == pytest.approx(127_860_864.911014)
    assert out.disagree == () and out.no_trade == () and (out.judged, out.compared) == (1, 0)


def test_GNS_stored_placeholder_with_NO_Massive_bar_is_NO_TRADE_never_a_disagreement():
    gns = Co(
        "GNS"
    )  # Yahoo's 0.101 zero-volume placeholder; Massive.com has no GNS row on 11-18 (a halt)
    out = _judge(NOV18, [gns], stored={gns: "0.101"})
    (row,) = out.no_trade
    assert (row.ticker, row.ours, row.massive, row.ratio) == ("GNS", 0.101, None, None)
    assert out.disagree == () and out.vendor_gap == () and out.compared == 0


def test_MARA_official_close_vs_Massives_last_trade_AGREES():
    mara = Co("MARA")
    out = _judge(NOV20, [mara], stored={mara: "5.09"})  # Massive.com 5.085: 0.098% apart
    assert (out.compared, out.disagree, out.no_trade, out.vendor_gap) == (1, (), (), ())


def test_HCMC_sub_penny_AGREES_under_the_one_cent_floor():
    hcmc = Co("HCMC")
    out = _judge(
        NOV18, [hcmc], stored={hcmc: "0.0001"}
    )  # Massive.com 0.000001: x100 apart, < 1 cent
    assert (out.compared, out.disagree) == (1, ())


def test_a_x8_STEP_DISAGREES_and_reads_SPLIT_SHAPED_a_plain_move_does_not():
    sndk, mara = Co("SNDK"), Co("MARA")
    out = _judge(
        NOV20,
        [sndk, mara],
        stored={
            sndk: "633.92",
            mara: "6.10",
        },  # SNDK on the pre-split scale; MARA simply wrong by x1.2
        listed={sndk: [SNDK_8_FOR_1]},
    )
    assert _tickers(out.disagree) == ["MARA", "SNDK"]
    mara_row, sndk_row = out.disagree
    assert sndk_row.ratio == pytest.approx(8.0) and sndk_row.split_shaped is True
    assert sndk_row.split == "8-for-1 forward split of 2026-11-20"
    assert mara_row.ratio == pytest.approx(6.10 / 5.085) and mara_row.split_shaped is False
    assert mara_row.split is None and out.compared == 2


def test_the_judge_puts_Massives_raw_bar_on_the_lists_scale():
    """A raw bar traded BEFORE a split in force is scaled onto the store's scale (``scale_after``): SNDK's raw
    11-19 close 755.16 against the store's 94.395 (= 755.16 / 8) agrees once judged through 11-20.
    """
    sndk = Co("SNDK")
    out = _judge(NOV19, [sndk], stored={sndk: "94.395"}, listed={sndk: [SNDK_8_FOR_1]}, asof=NOV20)
    assert (out.compared, out.disagree) == (1, ())
    # read on its own night (no split in force yet), the same pair is the x1/8 step
    raw = _judge(NOV19, [sndk], stored={sndk: "94.395"}, listed={sndk: [SNDK_8_FOR_1]})
    (row,) = raw.disagree
    assert row.ratio == pytest.approx(0.125) and row.split_shaped is True


def test_a_class_share_is_read_under_Massives_dot_and_a_price_symbol_wins():
    brk = Co("BRK-B")
    alias = Co(
        "OLDCO", symbol="MARA"
    )  # the master's vendor price symbol is the one the tape is priced under
    out = _judge(NOV20, [brk, alias], stored={brk: "570", alias: "5.085"})
    assert (out.compared, out.disagree, out.no_trade) == (2, (), ())


def test_a_company_with_NO_symbol_is_UNMAPPED_never_judged_against_Massive():
    nosym = Co(None)
    out = _judge(NOV20, [nosym], stored={nosym: "1.0"})
    (row,) = out.unmapped
    assert (row.ticker, row.symbol, row.ours) == (None, None, 1.0)
    assert out.no_trade == () and out.compared == 0 and out.judged == 1


def test_a_STALE_row_that_TRADED_tonight_reads_a_vendor_gap_not_a_rename():
    sndk, scni, gone, never = Co("SNDK"), Co("SCNI"), Co("NOTVQ"), Co("GNS")
    out = _judge(
        NOV25,
        stale=[
            sndk.name(edge=NOV19),
            scni.name(edge=NOV18),
            gone.name(edge=date(2026, 10, 1)),  # Massive.com has no row: nothing to add
            never.name(edge=None),  # never priced, yet Massive.com lists a trade
        ],
        listed={
            sndk: [SNDK_8_FOR_1],
            scni: [SCNI_1_FOR_4],
            never: [_split(date(2026, 11, 20), 10, 1, STATUS_NEEDS_REVIEW)],
        },
    )
    assert _tickers(out.stale_traded) == ["GNS", "SCNI", "SNDK"]
    assert [s.line for s in out.stale_traded] == [
        "GNS: no close ever stored, but Massive.com shows it traded tonight (0.435) — a vendor gap, not a "
        "rename; the 1-for-10 reverse split of 2026-11-20 is on the list (needs review)",
        "SCNI: no close stored since 2026-11-18, but Massive.com shows it traded tonight (1.69) — a vendor "
        "gap, not a rename; the 1-for-4 reverse split of 2026-11-19 is on the list",
        "SNDK: no close stored since 2026-11-19, but Massive.com shows it traded tonight (73.61) — a vendor "
        "gap, not a rename; the 8-for-1 forward split of 2026-11-20 is on the list",
    ]
    # never a judged row: the stale inventory owns these names
    assert out.judged == 0 and out.vendor_gap == ()


def test_the_report_on_the_fixture_night_is_one_quiet_line_plus_one_per_stale_row_that_traded():
    mara, brk, hcmc, gns, aitx = Co("MARA"), Co("BRK-B"), Co("HCMC"), Co("GNS"), Co("AITX")
    sndk, scni = Co("SNDK"), Co("SCNI")
    out = _judge(
        NOV25,
        [mara, brk, hcmc, gns, aitx],
        stored={mara: "5.42", brk: "579.32", hcmc: "0.0001", gns: "0.435"},  # AITX: no close stored
        stale=[sndk.name(edge=NOV19), scni.name(edge=NOV18)],
        listed={sndk: [SNDK_8_FOR_1], scni: [SCNI_1_FOR_4]},
    )
    assert tc.quiet_line(out) == (
        "Massive.com cross-check · Nov 25: 4 compared, 0 disagree; Massive.com traded 1 we have no close "
        "for: AITX 0.0002"
    )
    assert tc.report_lines(out) == [
        tc.quiet_line(out),
        "SCNI: no close stored since 2026-11-18, but Massive.com shows it traded tonight (1.69) — a vendor "
        "gap, not a rename; the 1-for-4 reverse split of 2026-11-19 is on the list",
        "SNDK: no close stored since 2026-11-19, but Massive.com shows it traded tonight (73.61) — a vendor "
        "gap, not a rename; the 8-for-1 forward split of 2026-11-20 is on the list",
    ]
    (gap,) = out.vendor_gap
    assert gap.otc is True  # Massive.com's own venue flag rides the row


def test_a_disagreement_and_the_other_classes_are_named_on_the_report():
    sndk, gns, nosym = Co("SNDK"), Co("GNS"), Co(None)
    out = _judge(
        NOV20,
        [sndk, gns, nosym],
        stored={sndk: "633.92", gns: "0.4993", nosym: "2"},
        listed={sndk: [SNDK_8_FOR_1]},
    )
    assert tc.report_lines(out) == [
        "Massive.com cross-check · Nov 20: 2 compared, 1 disagree (1 split-shaped); 1 with no Massive.com "
        "symbol",
        "Massive.com cross-check · disagrees: SNDK 633.92 stored vs 79.24 (x8, split-shaped: 8-for-1 forward "
        "split of 2026-11-20)",
    ]


def test_an_EMPTY_Massive_day_judges_nothing():
    mara = Co("MARA")
    empty = GroupedDay(session=date(2026, 11, 21), status="OK", bars={}, skipped=0)
    out = tc.judge(
        session=empty.session,
        asof=empty.session,
        judged=[mara.name()],
        stale=[],
        stored={mara.sid: Decimal("5")},
        massive=empty,
        listed={},
        tolerance=TOL,
    )
    assert out.evaluated and out.judged == 0 and out.no_trade == ()
    assert tc.quiet_line(out) == (
        "Massive.com cross-check · Nov 21 not judged — Massive.com lists no bars for it"
    )


# --- (2) the fetch: statuses, never errors ---------------------------------------------------------------


class _Source:
    def __init__(self, *, day=None, raises=None):
        self.asked: list[tuple[date, bool]] = []
        self._day, self._raises = day, raises

    def day(self, session, *, allow_live):
        self.asked.append((session, allow_live))
        if self._raises is not None:
            raise self._raises
        return self._day


def _never():
    raise AssertionError("no connection may open")


def _run(grouped, results=(), *, allow_live=True, connect=_never):
    return tc.run_tape_crosscheck(
        results=list(results),
        asof=grouped.session,
        allow_live=allow_live,
        grouped=grouped,
        tenant_id=DEFAULT_TENANT_ID,
        connect=connect,
        clock=lambda: 0.0,
        tolerance=TOL,
    )


def test_the_fetch_reads_tonights_session_once():
    src = _Source(day=_day(NOV25))
    got = tc.fetch_grouped(asof=NOV25, allow_live=True, enabled=True, source=src)
    assert got.status == tc.STATUS_ON and got.day is not None and src.asked == [(NOV25, True)]


def test_NOT_SERVED_YET_is_a_STATUS_only_and_judges_nothing():
    src = _Source(raises=NotServedYet("Massive.com had not served 2026-11-26 yet"))
    got = tc.fetch_grouped(asof=NOV26, allow_live=True, enabled=True, source=src)
    assert got.status == tc.STATUS_NOT_SERVED and got.day is None
    out = _run(got, [_thesis(Co("MARA").walk(NOV25))])
    assert (out.evaluated, out.error, out.judged, out.status) == (False, None, 0, "not_served")
    assert tc.report_lines(out) == [
        "Massive.com cross-check · not run — Massive.com had not served Nov 26 yet (it answers after "
        "the session's end of day)"
    ]


def test_NO_KEY_is_the_off_switch_and_so_is_the_setting(monkeypatch):
    monkeypatch.setattr(tc, "default_bar_source", lambda: None)
    got = tc.fetch_grouped(asof=NOV25, allow_live=True, enabled=True)
    assert (got.status, got.note) == (tc.STATUS_OFF, "no MASSIVE_API_KEY")
    src = _Source(day=_day(NOV25))
    off = tc.fetch_grouped(asof=NOV25, allow_live=True, enabled=False, source=src)
    assert (off.status, off.note, src.asked) == (
        tc.STATUS_OFF,
        "ALPHADECK_TAPE_CROSSCHECK=false",
        [],
    )
    out = _run(off, [_thesis(Co("MARA").walk(NOV25))])
    assert out.evaluated is False and tc.report_lines(out) == [
        "Massive.com cross-check · off (ALPHADECK_TAPE_CROSSCHECK=false)"
    ]


def test_the_setting_is_read_at_the_pass_and_a_settings_fault_is_a_status(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(tc, "get_settings", lambda: SimpleNamespace(tape_crosscheck=False))
    src = _Source(day=_day(NOV25))
    assert tc.fetch_grouped(asof=NOV25, allow_live=True, source=src).status == tc.STATUS_OFF
    monkeypatch.setattr(tc, "get_settings", lambda: SimpleNamespace())  # a stub without the field
    got = tc.fetch_grouped(asof=NOV25, allow_live=True, source=src)
    assert got.status == tc.STATUS_FAILED and (got.note or "").startswith("AttributeError")
    assert src.asked == []


def test_a_FAILED_or_UNCACHED_read_is_a_status_never_an_exception():
    boom = tc.fetch_grouped(
        asof=NOV25,
        allow_live=True,
        enabled=True,
        source=_Source(raises=MassiveBarsError("Massive.com grouped bars: HTTP 500 for <url>")),
    )
    assert boom.status == tc.STATUS_FAILED and "HTTP 500" in (boom.note or "")
    weird = tc.fetch_grouped(
        asof=NOV25, allow_live=True, enabled=True, source=_Source(raises=ValueError("odd"))
    )
    assert (weird.status, weird.note) == (tc.STATUS_FAILED, "ValueError: odd")
    cold = tc.fetch_grouped(
        asof=NOV25, allow_live=False, enabled=True, source=_Source(raises=CacheMiss("cold"))
    )
    assert cold.status == tc.STATUS_UNCACHED


def test_a_NO_LIVE_pass_never_evaluates():
    got = tc.GroupedFetch(session=NOV25, status=tc.STATUS_ON, day=_day(NOV25))
    out = _run(got, [_thesis(Co("MARA").walk(NOV25))], allow_live=False)
    assert out.evaluated is False and out.status == tc.STATUS_NOT_RUN and tc.report_lines(out) == []


def test_a_FAULT_is_reported_never_raised():
    def _boom():
        raise RuntimeError("db down")

    got = tc.GroupedFetch(session=NOV25, status=tc.STATUS_ON, day=_day(NOV25))
    out = _run(got, [_thesis(Co("MARA").walk(NOV25))], connect=_boom)
    assert out.evaluated is False and out.error == "RuntimeError: db down"
    assert tc.report_lines(out) == [
        "TAPE CROSS-CHECK FAILED — RuntimeError: db down (tonight's tape was not cross-checked)"
    ]


def test_nothing_to_judge_opens_no_connection():
    got = tc.GroupedFetch(session=NOV25, status=tc.STATUS_ON, day=_day(NOV25))
    out = _run(got, [])
    assert out.evaluated is True and out.judged == 0 and out.error is None


# --- (3) the names off the pass's results, and the artifact ----------------------------------------------


def _thesis(*names: NameResult, stale: tuple[StaleTape, ...] = ()) -> daily.ThesisRunResult:
    return daily.ThesisRunResult(
        thesis_id=uuid.uuid4(), name="T", ingested=list(names), tape_stale=stale
    )


def test_the_judged_set_is_the_missing_close_checks_and_stale_rows_are_the_open_price_rows():
    mara, alias, sndk, closed, onb = (
        Co("MARA"),
        Co("OLDCO", symbol="NEWCO"),
        Co("SNDK"),
        Co("XOMA"),
        Co("ONB"),
    )
    results = [
        _thesis(
            mara.walk(NOV25),
            alias.walk(NOV25),
            sndk.walk(NOV19),
            closed.walk(date(2026, 7, 20)),
            onb.walk(NOV25, onboarding="queued"),
            stale=(
                StaleTape("SNDK", sndk.sid, NOV19),
                StaleTape("XOMA", closed.sid, date(2026, 7, 20), closed_at=date(2026, 7, 27)),
                StaleTape(
                    "MARA", mara.sid, None, kind="fund_shares"
                ),  # another feed: not a price row
            ),
        ),
        _thesis(mara.walk(NOV25)),  # a name in two theses is one company
    ]
    judged, stale, excluded = tc.names_of(results)
    assert sorted((n.ticker, n.symbol) for n in judged) == [("MARA", None), ("OLDCO", "NEWCO")]
    assert [(n.ticker, n.edge) for n in stale] == [("SNDK", NOV19)]  # the closed row is not a gap
    assert excluded == {"stale": 2, "onboarding": 1, "no_tape": 0}


ARTIFACT_KEYS = {
    "evaluated",
    "status",
    "note",
    "error",
    "session",
    "massive_status",
    "massive_rows",
    "massive_skipped",
    "live",
    "tolerance",
    "judged",
    "compared",
    "excluded",
    "disagree",
    "vendor_gap",
    "no_trade",
    "unmapped",
    "stale_traded",
    "duration_s",
}


def test_the_ARTIFACT_carries_the_block_and_the_lines_read_the_same_from_it():
    sndk, aitx, scni = Co("SNDK"), Co("AITX"), Co("SCNI")
    out = _judge(
        NOV25,
        [sndk, aitx],
        stored={sndk: "588.88"},
        stale=[scni.name(edge=NOV18)],
        listed={sndk: [SNDK_8_FOR_1], scni: [SCNI_1_FOR_4]},
    )
    at = datetime(2026, 11, 26, 3, 30, tzinfo=timezone.utc)
    payload = build_run_payload(
        [], asof=NOV25, allow_live=True, started_at=at, finished_at=at, tape_crosscheck=out
    )
    block = payload["tape_crosscheck"]
    assert set(block) == ARTIFACT_KEYS
    assert block["session"] == "2026-11-25" and block["massive_status"] == "DELAYED"
    assert block["disagree"][0]["split"] == "8-for-1 forward split of 2026-11-20"
    assert tc.quiet_line(block) == tc.quiet_line(out)
    assert tc.stale_lines(block) == [s.line for s in out.stale_traded]
    assert tc.disagree_line(block) == tc.disagree_line(out)
    # an artifact written without the check carries the not-run block
    bare = build_run_payload([], asof=NOV25, allow_live=True, started_at=at, finished_at=at)
    assert bare["tape_crosscheck"]["evaluated"] is False


def test_the_key_never_reaches_the_artifact(monkeypatch):
    got = tc.GroupedFetch(
        session=NOV26, status=tc.STATUS_FAILED, note="Massive.com: HTTP 401 for <url>"
    )
    out = _run(got)
    assert "apiKey" not in str(out.payload())


# --- (4) the DB read (this EXECUTES against the per-worktree test DB) ----------------------------------------


def _security(db, ticker):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, f"{uuid.uuid4().int % 10**10:010d}", "2025-01-01"),
        )
    db.commit()
    return sid


def _bar(db, sid, d, close, recorded_at):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (DEFAULT_TENANT_ID, sid, d, close, 1000, d, recorded_at),
        )
    db.commit()


def _count(db, table):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        n = cur.fetchone()["n"]
    db.commit()
    return n


def test_run_tape_crosscheck_reads_the_NEWEST_stored_version_and_the_split_list_READ_ONLY(db):
    mara, sndk, scni = _security(db, "MARA"), _security(db, "SNDK"), _security(db, "SCNI")
    night = datetime(2026, 11, 26, 3, 30, tzinfo=timezone.utc)
    _bar(db, mara, NOV25, "6.10", night)  # the first version disagreed ...
    _bar(
        db, mara, NOV25, "5.42", night + timedelta(hours=24)
    )  # ... the newest agrees: the newest wins
    _bar(db, mara, date(2026, 11, 24), "5.41", night)
    _bar(db, sndk, NOV19, "94.395", night)
    split_repo.append_version(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sndk,
        key_date=NOV20,
        values={
            "effective_date": NOV20,
            "ratio_from": Decimal(1),
            "ratio_to": Decimal(8),
            "status": STATUS_CONFIRMED,
            "confirmed_via": "auto:sec+polygon",
            "value_source": "both",
            "valid_from": date(2026, 11, 13),
        },
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()
    results = [
        _thesis(
            NameResult("MARA", mara, 0, 1, tape_edge=NOV25),
            NameResult("SNDK", sndk, 0, 0, tape_edge=NOV19),
            NameResult("SCNI", scni, 0, 0, tape_edge=NOV18),
            stale=(StaleTape("SNDK", sndk, NOV19),),
        )
    ]
    opened = []

    def _connect():
        c = connect()
        opened.append(c)
        return c

    tables = ("fact_price_eod", "security_split", "security_master")
    before = {t: _count(db, t) for t in tables}
    got = tc.GroupedFetch(session=NOV25, status=tc.STATUS_ON, day=_day(NOV25))
    out = _run(got, results, connect=_connect)
    assert {t: _count(db, t) for t in tables} == before  # count the tables: nothing written
    assert len(opened) == 1 and opened[0].read_only is True and opened[0].closed
    assert out.evaluated and out.error is None and (out.judged, out.compared) == (2, 1)
    assert out.disagree == ()  # MARA's newest version (5.42) against Massive.com's 5.42
    assert _tickers(out.vendor_gap) == ["SCNI"]
    (sndk_row,) = out.stale_traded
    assert sndk_row.split == "8-for-1 forward split of 2026-11-20"
    assert sndk_row.line.startswith(
        "SNDK: no close stored since 2026-11-19, but Massive.com shows it"
    )

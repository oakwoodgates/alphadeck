"""The split-night hold's escalation leg (``pipeline/split_jumps.py``, split plan S5 rule 3): the nightly unit is
fail-open and quiet. The read itself is golden on GAME's real released step in
``tests/signals/test_split_hold.py`` (``test_the_escalation_three_sessions_on``)."""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.split_hold import SplitStep
from pipeline import split_jumps as sj
from tests import split_s5_fixtures as fx


def _jump(tk: str, d: date, k: float) -> sj.UnlistedJump:
    step = SplitStep(date(2026, 10, 9), d, 1.0, k, 10_000, 500)
    return sj.UnlistedJump(uuid.uuid4(), tk, step, 3, ())


def test_one_quiet_line_only_when_there_is_something_to_ask():
    assert sj.report_lines(sj.JumpScan()) == []  # not scanned (--no-live)
    assert sj.report_lines(sj.JumpScan(evaluated=True)) == []  # a clean night: nothing at all
    (line,) = sj.report_lines(
        sj.JumpScan(evaluated=True, jumps=(_jump("BTOC", date(2026, 10, 15), 15.8),))
    )
    assert line == (
        "1 split-shaped jump(s) past the split-night hold with no split listed — is this real? "
        "(Admin → Splits): BTOC stepped x16 on 10-15"
    )
    assert line[0].islower() or line[0].isdigit()  # the quiet casing (no LOUD header, never a page)


def test_the_line_names_five_then_counts():
    jumps = tuple(_jump(f"T{i}", date(2026, 10, 15), 4.0) for i in range(7))
    (line,) = sj.report_lines(sj.JumpScan(evaluated=True, jumps=jumps))
    assert line.startswith("7 split-shaped jump(s)") and line.endswith("(+2 more)")


def test_a_failed_read_is_one_line_never_a_failed_pass():
    def boom():
        raise RuntimeError("db down")

    scan = sj.run_jump_scan(
        asof=date(2026, 10, 22), allow_live=True, tenant_id=DEFAULT_TENANT_ID, connect=boom
    )
    assert not scan.evaluated and scan.error == "RuntimeError: db down"
    assert sj.report_lines(scan) == [
        "split-shaped jump check failed — RuntimeError: db down (the Admin Splits panel reads it live)"
    ]


def test_a_no_live_pass_does_not_scan():
    def never():
        raise AssertionError("a --no-live pass opened a connection")

    scan = sj.run_jump_scan(
        asof=date(2026, 10, 22), allow_live=False, tenant_id=DEFAULT_TENANT_ID, connect=never
    )
    assert scan == sj.JumpScan()


def test_the_nightly_unit_reads_through_its_own_read_only_connection(db):
    from db.session import connect

    sid = fx.seed(db, "GAME", listed=False)
    fx.seed_thesis(db, [("GAME", sid)])
    seen = {}

    def conn():
        c = connect()
        seen["c"] = c
        return c

    scan = sj.run_jump_scan(
        asof=date(2026, 8, 28), allow_live=True, tenant_id=DEFAULT_TENANT_ID, connect=conn
    )
    assert scan.evaluated and [j.ticker for j in scan.jumps] == ["GAME"]
    assert seen["c"].closed  # its own connection, closed after the read


def test_an_archived_thesis_is_out_of_the_union(db):
    sid = fx.seed(db, "GAME", listed=False)
    tid = fx.seed_thesis(db, [("GAME", sid)])
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET archived_at = now() WHERE id = %s", (tid,))
    db.commit()
    assert sj.scan_unlisted_jumps(db, asof=date(2026, 8, 28), tenant_id=DEFAULT_TENANT_ID) == ()

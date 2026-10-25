"""The data flag's pure model (split plan S3, ``domain/data_flags.py``): which cells a marked date reaches, the
windows mirrored from the display members, and the structural bound — nothing on the call path can read a flag.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

from domain import data_flags as m
from domain.call import CallCard
from domain.data_flags import BAR, STEP, Mark, cells_for
from signals.display import price_path, rvol, sma, trailing_returns

BACKEND = Path(__file__).resolve().parents[2]


def _weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


TAPE = _weekdays(date(2025, 6, 2), 300)  # 300 sessions; the newest bar is the as-of bar
ASOF = TAPE[-1]


def test_the_cell_windows_mirror_the_display_members():
    # a widened member widens the cell it reaches — never a second copy drifting
    for n in trailing_returns.WINDOWS:
        key = trailing_returns._KEY.get(n, f"ret_{n}d")
        assert m.BAR_WINDOWS[key] == n
    assert m.BAR_WINDOWS["sma"] == sma.SMA_SLOW + sma.SLOPE_BARS
    assert m.BAR_WINDOWS["path"] == price_path.BARS - 1
    assert m.BAR_WINDOWS["rvol8"] == rvol.BASELINE_BARS
    assert m.BAR_WINDOWS["rvol20"] == rvol.BASELINE_BARS_20
    assert set(m.BAR_WINDOWS) == set(m.PRICE_CELLS) | set(m.VOLUME_CELLS)


def test_a_step_on_the_newest_bar_reaches_every_price_and_volume_cell():
    cells = cells_for([Mark(ASOF, STEP)], TAPE, ASOF, (m.PRICE, m.VOLUME))
    assert cells == m.PRICE_CELLS + m.VOLUME_CELLS


def test_a_step_reaches_only_the_windows_that_straddle_it():
    # 10 sessions back: the 1d and 7d returns and RVOL 8D read only bars on one side of it
    d = TAPE[-11]
    cells = cells_for([Mark(d, STEP)], TAPE, ASOF, (m.PRICE, m.VOLUME))
    assert cells == ("sma", "ret_30d", "ret_90d", "ret_1y", "path", "rvol20")
    # the window's own first bar on the new side straddles nothing (the 7d return from TAPE[-8])
    assert "ret_7d" not in cells_for([Mark(TAPE[-8], STEP)], TAPE, ASOF, (m.PRICE,))
    assert "ret_7d" in cells_for([Mark(TAPE[-7], STEP)], TAPE, ASOF, (m.PRICE,))


def test_a_bar_mark_reaches_the_windows_that_read_that_bar():
    d = TAPE[-8]  # the 7d return's base bar
    assert "ret_7d" in cells_for([Mark(d, BAR)], TAPE, ASOF, (m.PRICE,))
    assert "ret_1d" not in cells_for([Mark(d, BAR)], TAPE, ASOF, (m.PRICE,))


def test_a_mark_older_than_every_window_reaches_nothing():
    assert cells_for([Mark(TAPE[0], STEP)], TAPE, ASOF, m.GROUPS) == ()
    assert cells_for([Mark(date(2020, 1, 2), BAR)], TAPE, ASOF, m.GROUPS) == ()


def test_the_insider_cells_read_calendar_windows():
    assert cells_for([Mark(ASOF - timedelta(days=29), STEP)], TAPE, ASOF, (m.INSIDER,)) == (
        "ins_30d",
        "ins_90d",
    )
    assert cells_for([Mark(ASOF - timedelta(days=30), STEP)], TAPE, ASOF, (m.INSIDER,)) == (
        "ins_90d",
    )
    assert cells_for([Mark(ASOF - timedelta(days=90), STEP)], TAPE, ASOF, (m.INSIDER,)) == ()


def test_market_cap_is_never_reached_by_a_date_and_an_empty_tape_reaches_no_price_cell():
    assert "mktcap" not in cells_for([Mark(ASOF, STEP)], TAPE, ASOF, m.GROUPS)
    assert cells_for([Mark(ASOF, STEP)], [], ASOF, (m.PRICE, m.VOLUME)) == ()


def test_known_wrong_sorts_first_and_a_row_is_marked_only_when_a_cell_is_reached():
    doubt = m.DataFlag(kind=m.SPLIT_REVIEW, level=m.IN_DOUBT, reason="r", clears="c")
    wrong = m.DataFlag(kind=m.FEED_STOPPED, level=m.KNOWN_WRONG, reason="r", clears="c")
    assert m.sort_flags([doubt, wrong]) == [wrong, doubt]
    assert not m.MemberFlags(security_id="x", ticker="X", flags=(doubt,)).marked
    reached = m.DataFlag(
        kind=m.SPLIT_REVIEW, level=m.IN_DOUBT, reason="r", clears="c", columns=("ret_1d",)
    )
    assert m.MemberFlags(security_id="x", ticker="X", flags=(reached,)).marked


def test_snex_marked_only_by_its_reaching_flags_which_sort_first():
    """SNEX on prod's 2026-10-22 read: its 2025-03-24 needs-review split reaches no cell (columns []), its
    2026-03-23 / 2026-07-20 ones and the market cap do. The row is marked by THOSE, and they lead the list, so a
    reader of the first flag never sees a history-only one under a mark; the history-only flag alone marks
    nothing (the HIST shape)."""
    hist = m.DataFlag(
        kind=m.SPLIT_REVIEW, level=m.IN_DOUBT, reason="r", clears="c", since=date(2025, 3, 24)
    )
    mar = m.DataFlag(
        kind=m.SPLIT_REVIEW,
        level=m.IN_DOUBT,
        reason="r",
        clears="c",
        since=date(2026, 3, 23),
        columns=("sma", "ret_1y"),
    )
    jul = m.DataFlag(
        kind=m.SPLIT_REVIEW,
        level=m.IN_DOUBT,
        reason="r",
        clears="c",
        since=date(2026, 7, 20),
        columns=("sma", "ret_90d", "ret_1y", "path"),
    )
    cap = m.DataFlag(
        kind=m.MKTCAP_SHARES,
        level=m.IN_DOUBT,
        reason="r",
        clears="c",
        since=date(2026, 7, 20),
        columns=("mktcap",),
    )
    ordered = m.sort_flags([hist, mar, jul, cap])
    assert ordered == [mar, jul, cap, hist]
    assert m.MemberFlags(security_id="s", ticker="SNEX", flags=tuple(ordered)).marked
    assert not m.MemberFlags(security_id="s", ticker="HIST", flags=(hist,)).marked


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_nothing_on_the_call_path_reads_a_flag():
    """The flag is display-only (#3/#4): no module under ``calls/`` or ``signals/`` (the detectors AND the
    display seam) imports the flag model or its reader, and the recorded CallCard has no flag field — a
    day-varying field there would re-record every card nightly."""
    forbidden = (
        "domain.data_flags",
        "pipeline.data_flags",
        "pipeline.fundamentals_lag",  # the fundamentals reading (data hygiene, 2026-10-24)
        "app.routers.data_flags",
    )
    for pkg in ("calls", "signals"):
        for src in sorted((BACKEND / pkg).rglob("*.py")):
            for mod in _imports(src):
                assert not any(
                    mod == f or mod.startswith(f + ".") for f in forbidden
                ), f"{src} imports {mod!r} — a data flag is never a call input"
    assert not {f for f in CallCard.model_fields if "flag" in f or "note" in f}


# --- the quiet marker (data hygiene, 2026-10-24): a price history under a year -----------------------------


def test_a_tape_under_a_year_carries_the_short_history_marker_and_says_when_it_ends():
    first = date(2026, 2, 11)  # GSUI's first stored bar (MEASURED, prod 2026-10-24)
    note = m.short_history_note([first, date(2026, 10, 23)], date(2026, 10, 24))
    assert note is not None and note.kind == m.SHORT_HISTORY and note.label == "short price history"
    assert note.reason == (
        "Price history starts 2026-02-11; the 52-week checks can't run before 2027-02-11."
    )
    assert (note.since, note.until) == (first, date(2027, 2, 11))


def test_a_year_of_tape_or_none_at_all_carries_no_marker():
    asof = date(2026, 10, 24)
    year_ago = asof - timedelta(days=m.SHORT_HISTORY_DAYS)
    assert m.short_history_note([year_ago, asof], asof) is None
    assert m.short_history_note([year_ago + timedelta(days=1), asof], asof) is not None
    assert (
        m.short_history_note([], asof) is None
    )  # no tape at all is the stopped-feed flag's to say
    assert m.plus_one_year(date(2024, 2, 29)) == date(2025, 3, 1)


def test_a_marker_never_marks_the_row():
    note = m.short_history_note([date(2026, 6, 1)], date(2026, 10, 24))
    assert not m.MemberFlags(security_id="s", ticker="NEW", notes=(note,)).marked

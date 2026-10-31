"""``pipeline.annotate_no_session_rows`` (F10) — rows dated a day with no market session, COUNTING THE TABLE.

THE FIXTURE IS THE MEASURED SHAPE, not a fitted one: the daytime hand runs before the September fix recorded
the card under the date they ran, so honest ``calls`` rows sit on a Saturday and a Sunday (dev: 49 rows,
2026-08-16 .. 2026-09-05). Each test writes such rows through the Scoreboard helpers (``record_day``: the
real assembler's card, appended to the immutable log) beside ordinary weekday rows, then runs the CLI exactly
as it would on prod and counts ``calls`` / ``call_annotation`` before and after.

The calendar is ``domain.trading_days.is_scheduled_day`` ALONE: today it is Mon-Fri, so the real fixture is the
weekend. Labor Day (a Monday) is covered by a DIRECT-INPUT contract test that swaps the predicate for a
holiday-aware one — it proves the CLI follows the one calendar and keeps none of its own.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path

import pytest

from domain.settings import get_settings
from pipeline import annotate_no_session_rows as ans
from repositories import call_annotations_repo, thesis_repo
from scoreboard.assemble import assemble_scoreboard
from scoreboard.record import derive_thesis_record
from tests.calls.factories import make_thesis
from tests.scoreboard.helpers import keys_fired, persist_thesis, record_day

_BACKEND = Path(__file__).resolve().parents[2]

_FRI = date(2026, 8, 14)
_SAT = date(2026, 8, 15)
_SUN = date(2026, 8, 16)
_MON = date(2026, 8, 17)
_LABOR_DAY = date(
    2026, 9, 7
)  # a Monday: a weekday today, a market holiday under the holiday-aware calendar
_READ = date(2026, 8, 20)


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-local literal
        return cur.fetchone()["n"]


def _thesis(db, name: str):
    thesis = make_thesis(id=uuid.uuid4(), name=name, basket=[])  # basket=[] avoids the master FK
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Keep the provenance artifact in the test's tmp dir."""
    monkeypatch.setattr(ans, "_DEFAULT_LOG_DIR", tmp_path / "annotations")
    return tmp_path / "annotations"


@pytest.fixture
def ledger(db):
    """Two theses. A: Fri, Sat, Sun (TWICE — a re-recorded night), Mon. B: Sat honest, Sun RECONSTRUCTED.
    The no-session honest rows are A's Sat, A's two Sun rows and B's Sat; everything else must stay untouched.
    """
    a, b = _thesis(db, "Alpha Card"), _thesis(db, "Beta Card")
    ids = {
        "a_fri": record_day(db, a, [], _FRI, run_kind="cron"),
        "a_sat": record_day(db, a, [], _SAT, run_kind="manual"),
        "a_sun1": record_day(db, a, [], _SUN, run_kind="manual"),
        "a_sun2": record_day(db, a, [], _SUN, run_kind="manual"),
        "a_mon": record_day(db, a, [], _MON, run_kind="cron"),
        "b_sat": record_day(db, b, [], _SAT, run_kind="manual"),
        "b_sun_reconstructed": record_day(db, b, [], _SUN, reconstructed=True),
    }
    return a, b, ids


# --- finding the rows --------------------------------------------------------------------------------------


def test_finds_the_weekend_dated_honest_rows_and_nothing_else(db, ledger):
    a, b, ids = ledger
    rows = ans.find_rows(db)
    assert {r.call_id for r in rows} == {ids["a_sat"], ids["a_sun1"], ids["a_sun2"], ids["b_sat"]}
    assert [(r.thesis_name, r.asof, r.weekday) for r in rows] == [
        ("Alpha Card", _SAT, "Saturday"),
        ("Alpha Card", _SUN, "Sunday"),
        ("Alpha Card", _SUN, "Sunday"),
        ("Beta Card", _SAT, "Saturday"),
    ]
    assert {r.run_kind for r in rows} == {"manual"}  # the daytime hand runs

    # scoped reads
    assert {r.call_id for r in ans.find_rows(db, thesis_id=b.id)} == {ids["b_sat"]}
    assert {r.call_id for r in ans.find_rows(db, asof_from=_SUN)} == {ids["a_sun1"], ids["a_sun2"]}
    assert ans.find_rows(db, asof_from=_MON) == []


def test_the_cli_follows_the_one_session_calendar_and_keeps_none_of_its_own(db, monkeypatch):
    """DIRECT-INPUT contract: swap ``is_scheduled_day`` for a holiday-aware predicate (what the sibling
    calendar fix delivers) and the Labor Day row is found with no change to the CLI."""
    a = _thesis(db, "Alpha Card")
    friday = record_day(db, a, [], date(2026, 9, 4))
    labor_day = record_day(db, a, [], _LABOR_DAY, run_kind="manual")
    assert ans.find_rows(db) == []  # Mon-Fri today: a holiday is a session day
    monkeypatch.setattr(ans, "is_scheduled_day", lambda d: d.weekday() < 5 and d != _LABOR_DAY)
    (row,) = ans.find_rows(db)
    assert row.call_id == labor_day and row.weekday == "Monday"
    assert row.call_id != friday


# --- the CLI: dry run, apply, re-apply — counting the table -------------------------------------------------


def test_dry_run_writes_nothing_apply_writes_exactly_the_rows_and_a_rerun_adds_zero(
    db, ledger, log_dir, tmp_path, capsys
):
    a, b, ids = ledger
    calls_before = _count(db, "calls")
    assert _count(db, "call_annotation") == 0

    # DRY RUN (the CLI default): read-only connection, nothing written but the asked-for report
    report = tmp_path / "report.json"
    ans.main(["--report-json", str(report)])
    db.rollback()
    out = capsys.readouterr().out
    assert _count(db, "call_annotation") == 0 and _count(db, "calls") == calls_before
    assert "TOTAL: 4 honest row(s)" in out and "DRY-RUN" in out
    assert out.count("run manual") == 4  # every row printed with its run kind ...
    assert "(Saturday)" in out and "(Sunday)" in out  # ... and its weekday
    assert report.exists() and '"rows": 4' in report.read_text(encoding="utf-8")
    assert not log_dir.exists()  # a dry run writes no provenance artifact

    # --apply: exactly the four no-session rows, as REVERSIBLE excludes carrying the evidence
    ans.main(["--apply"])
    db.rollback()
    assert _count(db, "call_annotation") == 4
    anns = call_annotations_repo.active_for_thesis(
        db, a.id
    ) + call_annotations_repo.active_for_thesis(db, b.id)
    assert {a_.call_id for a_ in anns} == {ids["a_sat"], ids["a_sun1"], ids["a_sun2"], ids["b_sat"]}
    for ann in anns:
        assert ann.action == "exclude" and ann.reason == "no_session_day"
        assert ann.rule.startswith("no-session-night@")
        assert (
            call_annotations_repo.rule_family(ann.rule)
            == call_annotations_repo.RULE_FAMILY_NO_SESSION
        )
        assert ann.detail["weekday"] in ("Saturday", "Sunday")
        assert ann.detail["run_kind"] == "manual"
        assert (
            "affected_security_ids" not in ann.detail
        )  # the NIGHT is the judgment: every member's caption
    # the weekday rows and the reconstructed row carry nothing
    assert {x.call_id for x in anns}.isdisjoint(
        {ids["a_fri"], ids["a_mon"], ids["b_sun_reconstructed"]}
    )
    assert len(list(log_dir.glob("no-session-*.json"))) == 1

    # a second --apply appends NOTHING (count the table)
    ans.main(["--apply"])
    db.rollback()
    assert _count(db, "call_annotation") == 4
    assert _count(db, "calls") == calls_before  # the immutable log is untouched


def test_apply_with_nothing_to_annotate_appends_nothing(db, log_dir, capsys):
    a = _thesis(db, "Alpha Card")
    record_day(db, a, [], _FRI)
    record_day(db, a, [], _MON)
    ans.main(["--apply"])
    db.rollback()
    assert _count(db, "call_annotation") == 0
    assert "TOTAL: 0 honest row(s)" in capsys.readouterr().out


def test_a_revoke_sticks_across_a_rerun_and_revoke_all_touches_only_this_rule(
    db, ledger, log_dir, capsys
):
    a, b, ids = ledger
    # an UNRELATED active exclude (D3b's family) on a weekday row — revoke-all must leave it alone
    call_annotations_repo.append_annotation(
        db,
        ids["a_mon"],
        action="exclude",
        reason="stale_comparative_revenue",
        rule="d3a-first-knowability@t",
    )
    db.commit()
    ans.main(["--apply"])
    db.rollback()
    assert _count(db, "call_annotation") == 5
    first = call_annotations_repo.active_for_rule_family(
        db, call_annotations_repo.RULE_FAMILY_NO_SESSION
    )[0]

    # a DRY-RUN revoke writes nothing
    ans.main(["--revoke", str(first.id), "--reason", "operator: score this night"])
    db.rollback()
    assert _count(db, "call_annotation") == 5

    ans.main(["--revoke", str(first.id), "--reason", "operator: score this night", "--apply"])
    db.rollback()
    assert _count(db, "call_annotation") == 6
    active = call_annotations_repo.active_for_rule_family(
        db, call_annotations_repo.RULE_FAMILY_NO_SESSION
    )
    assert len(active) == 3 and first.id not in {x.id for x in active}

    # the family already judged that row: a re-run neither re-annotates nor re-activates it
    ans.main(["--apply"])
    db.rollback()
    assert _count(db, "call_annotation") == 6
    assert (
        len(
            call_annotations_repo.active_for_rule_family(
                db, call_annotations_repo.RULE_FAMILY_NO_SESSION
            )
        )
        == 3
    )

    ans.main(["--revoke-all", "--reason", "operator: score them all", "--apply"])
    db.rollback()
    assert (
        call_annotations_repo.active_for_rule_family(
            db, call_annotations_repo.RULE_FAMILY_NO_SESSION
        )
        == []
    )
    # D3b's annotation on the weekday row is still active
    assert [x.call_id for x in call_annotations_repo.active_for_thesis(db, a.id)] == [ids["a_mon"]]
    assert _count(db, "call_annotation") == 5 + 1 + 3  # + the first revoke + three revoke-all rows


# --- the Scoreboard sets the rows aside, ONE reading covers them, the drawer names them --------------------


def _arm_scenario(db, security_id):
    """Thu warming, then ARMED on Sat, Sun and Mon. The Sat/Sun rows are the no-session ones. Returns the
    thesis and the Sat / Sun row ids."""
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 8, 13), conv_liveness=60, conf_liveness=60)
    record_day(db, thesis, [conv], _FRI)  # warming
    sat = record_day(db, thesis, [conv, conf], _SAT, run_kind="manual")  # armed on a Saturday
    sun = record_day(db, thesis, [conv, conf], _SUN, run_kind="manual")
    record_day(db, thesis, [conv, conf], _MON)  # armed, a real session
    return thesis, sat, sun


def _arms(record) -> list[date]:
    return [e.episode.arm_date for e in record.episodes]


def test_the_default_reading_sets_the_nights_aside_and_the_include_reading_reverses_exactly(
    db, security_id, log_dir
):
    thesis, _, _ = _arm_scenario(db, security_id)
    before, _ = derive_thesis_record(db, thesis, _READ, exclude_annotated=True, exclude_late=False)
    assert _arms(before) == [_SAT]  # the Saturday row opens the episode: the bug F10 sets aside

    ans.main(["--apply"])
    db.rollback()

    default, _ = derive_thesis_record(db, thesis, _READ, exclude_annotated=True, exclude_late=False)
    assert _arms(default) == [_MON]  # the weekend is a gap; the next real session opens the episode
    assert default.episodes[0].annotation_note is None  # the default reading carries no caption

    included, _ = derive_thesis_record(
        db, thesis, _READ, exclude_annotated=False, exclude_late=False
    )
    assert _arms(included) == [
        _SAT
    ]  # the ONE include_annotated reading covers these exclusions too
    strip = {"annotation_note"}
    assert [e.model_dump(exclude=strip) for e in included.episodes] == [
        e.model_dump(exclude=strip) for e in before.episodes
    ]  # byte-for-byte the pre-annotation record

    summary = assemble_scoreboard(db, asof=_READ, exclude_late=False).summary
    assert summary.annotated_policy == "excluded"
    assert [(n.asof, n.reasons, n.n_rows) for n in summary.annotated_nights] == [
        (_SAT, ["no_session_day"], 1),
        (_SUN, ["no_session_day"], 1),
    ]
    assert all(n.rules[0].startswith("no-session-night@") for n in summary.annotated_nights)


_D3A, _NO_SESSION = "d3a-first-knowability@t", "no-session-night@t"


@pytest.mark.parametrize(
    ("rule", "words", "absent"),
    [
        (_NO_SESSION, "no-session night(s) 2026-08-15, 2026-08-16", "phantom"),
        (_D3A, "phantom-arm night(s) 2026-08-15, 2026-08-16", "no-session"),
        ("something-else@t", "set-aside night(s) 2026-08-15, 2026-08-16", "phantom"),
    ],
)
def test_the_drawer_caption_reads_the_rule_of_the_annotation(db, security_id, rule, words, absent):
    thesis, sat, sun = _arm_scenario(db, security_id)
    for call_id in (sat, sun):
        call_annotations_repo.append_annotation(
            db, call_id, action="exclude", reason="whatever_reason", rule=rule
        )
    db.commit()
    (ep,) = derive_thesis_record(db, thesis, _READ, exclude_annotated=False, exclude_late=False)[
        0
    ].episodes
    assert ep.annotation_note is not None
    assert (
        f"{words} — annotated exclude; scored here only because this reading includes annotated nights"
        in ep.annotation_note
    )
    assert absent not in ep.annotation_note


def test_two_kinds_of_night_in_one_episode_are_named_separately_in_a_fixed_order(db, security_id):
    thesis, sat, sun = _arm_scenario(db, security_id)
    call_annotations_repo.append_annotation(
        db, sat, action="exclude", reason="no_session_day", rule=_NO_SESSION
    )
    call_annotations_repo.append_annotation(
        db, sun, action="exclude", reason="stale_comparative_revenue", rule=_D3A
    )
    db.commit()
    (ep,) = derive_thesis_record(db, thesis, _READ, exclude_annotated=False, exclude_late=False)[
        0
    ].episodes
    note = ep.annotation_note or ""
    assert note.startswith("annotated · phantom-arm night(s) 2026-08-16 — ")
    assert " · no-session night(s) 2026-08-15 — " in note
    assert note.index("phantom-arm") < note.index("no-session")


def test_a_night_set_aside_by_the_setting_off_scores_as_before(
    db, security_id, log_dir, monkeypatch
):
    """The reading is ONE policy over every exclude: the setting OFF scores the no-session nights again."""
    thesis, _, _ = _arm_scenario(db, security_id)
    ans.main(["--apply"])
    db.rollback()
    monkeypatch.setenv("ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED", "false")
    get_settings.cache_clear()
    try:
        off, _ = derive_thesis_record(db, thesis, _READ, exclude_late=False)
    finally:
        monkeypatch.delenv("ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED")
        get_settings.cache_clear()
    assert _arms(off) == [_SAT]


# --- structural: a recompute-free annotator -----------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods


def test_the_annotator_cannot_ingest_notify_or_assemble():
    """It reads rows and appends annotations, nothing else: no fetch/refresh/notify surface, no call assembly,
    and not pipeline.daily (which would drag them all in). The session calendar comes from ONE place.
    """
    mods = _imports(_BACKEND / "pipeline" / "annotate_no_session_rows.py")
    forbidden = (
        "ingest",
        "notify",
        "radar",
        "signals",
        "calls",
        "pipeline.daily",
        "pipeline.ingest_thesis",
        "pipeline.core",
        "pipeline.call_for_thesis",
    )
    assert [m for m in mods if m.split(".")[0] in forbidden or m.startswith(forbidden)] == []
    assert "domain.trading_days" in mods

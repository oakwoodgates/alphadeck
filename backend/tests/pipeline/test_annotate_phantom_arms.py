"""``pipeline.annotate_phantom_arms`` (D3b) — on the REAL phantom arm it exists for, COUNTING THE TABLE.

THE FIXTURE IS A REAL NIGHT, not a fitted one. Nuclear Buildout's 2026-10-14 call-of-record armed CEG on two
keys: a core revenue re-acceleration whose quarter (Q1-2025, $6.79B → first filed 2025-05-06) the pre-D3a
read dated at its 2026-05-11 comparative, and a volume-backed breakout on 2026-10-06. Both inputs are what
prod held that night: CEG's every stored revenue version (``tests/fundamentals_fixtures.CEG``, D3a's own
fixture) and CEG's as-of EOD bars 2025-07-01..2026-10-15 (``tests/fixtures/prices/CEG_2025-07_2026-10.csv``,
read-only from prod: the latest version per bar at the 2026-10-15 row's ``recorded_at``). A single-name
thesis over exactly those facts arms CEG under the pre-D3a dating and does not under D3a — the phantom arm,
reproduced on real data.

THE RECORDED ROW is written the way the pre-D3a cron wrote it, through an INDEPENDENT restatement of the old
read (``_Pre390PointInTimeData``: the literal pre-#390 ``DISTINCT ON`` SQL, rows dated by their picked
version) — never through the CLI's own ``PreD3aDatingView``, so "the D3a-off re-assembly reproduces the
recorded row" is a real cross-check, not a tautology. Then the CLI runs exactly as it would on prod.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import State
from domain.market_time import known_at_for_asof
from domain.thesis import BasketMember, Thesis
from ingest.prices.eod_loader import ingest_prices_backfill, parse_stooq_csv
from pipeline import annotate_phantom_arms as apa
from pipeline.annotate_phantom_arms import CallShape, PreD3aDatingView, classify
from pipeline.call_for_thesis import call_for_thesis, load_call_inputs
from pipeline.core import assemble_from_pit
from repositories import call_annotations_repo, calls_repo, thesis_repo
from scoreboard.assemble import assemble_scoreboard
from signals import revenue_acceleration as ra
from signals.base import PointInTimeData
from signals.horizons import call_bounds
from tests.fundamentals_fixtures import CEG, store_versions

_BACKEND = Path(__file__).resolve().parents[2]
_CSV = _BACKEND / "tests" / "fixtures" / "prices" / "CEG_2025-07_2026-10.csv"
_ARM_NIGHTS = (date(2026, 10, 14), date(2026, 10, 15))  # the two real phantom nights


# --- the fixture: real CEG facts + the row the pre-D3a cron wrote ---------------------------------------


class _Pre390PointInTimeData(PointInTimeData):
    """The PRE-#390 fundamentals read, restated independently of the CLI: the literal old SQL (latest
    version per natural key, ``recorded_at DESC, id DESC``) with each row dated by its PICKED version —
    what ``revenue_acceleration`` / ``share_creep`` read as ``filed`` / ``accession`` before D3a (today's
    detectors read those values off the ``knowable_*`` keys, so the old values are handed over there).
    """

    def fundamentals_facts(self, security_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (security_id, metric_key, period_end) * FROM fact_fundamentals "
                "WHERE tenant_id = %s AND security_id = %s AND valid_from <= %s AND recorded_at <= %s "
                "ORDER BY security_id, metric_key, period_end, recorded_at DESC, id DESC",
                (self.tenant_id, security_id, self.asof, self.known_at),
            )
            rows = cur.fetchall()
        return [
            {**r, "knowable_from": r["valid_from"], "knowable_accession": r["accession"]}
            for r in rows
        ]


def _ceg(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, 'CEG', '0001868275', 'Constellation Energy Corp', '2020-01-01')",
            (sid, DEFAULT_TENANT_ID),
        )
    db.commit()
    store_versions(db, sid, CEG)  # every real revenue version, the prod stamp
    ingest_prices_backfill(db, sid, parse_stooq_csv(_CSV.read_text(encoding="utf-8")))
    db.commit()
    return sid


def _thesis(db, sid: uuid.UUID) -> Thesis:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Nuclear (CEG only)",
        narrative="The nuclear power build-out; CEG as the operator of the largest US fleet.",
        ticker="CEG",
        basket=[BasketMember(ticker="CEG", role="Fleet operator", security_id=sid)],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis


def _record_pre_d3a(db, thesis: Thesis, asof: date, cfg: CallConfig = DEFAULT_CONFIG) -> None:
    """One nightly-shaped row exactly as the pre-D3a cron wrote it (assemble, then record_if_changed)."""
    basket = {m.security_id for m in thesis.basket}
    pit = _Pre390PointInTimeData(
        db, asof=asof, tenant_id=DEFAULT_TENANT_ID, basket=basket, bounds=call_bounds(cfg)
    )
    card = assemble_from_pit(pit, thesis_repo.get_asof(db, thesis.id, None), asof, cfg)
    assert calls_repo.record_if_changed(
        db, card, DEFAULT_TENANT_ID, run_kind="cron", ingest_fresh=True
    )
    db.commit()


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-local literal
        return cur.fetchone()["n"]


@pytest.fixture
def phantom(db):
    """CEG's real facts, a CEG-only thesis, and the two real phantom nights recorded pre-D3a."""
    sid = _ceg(db)
    thesis = _thesis(db, sid)
    for night in _ARM_NIGHTS:
        _record_pre_d3a(db, thesis, night)
    return thesis, sid


# --- the fixture is genuinely a phantom (stated before anything is classified) -------------------------


def test_the_recorded_nights_are_real_phantom_arms(db, phantom):
    """Pre-D3a the CEG thesis ARMED on both nights, on the stale-dated Q1-2025 revenue quarter + the
    2026-10-06 breakout; with D3a's dating the same facts only WARM it. Nothing here is fitted: real
    revenue versions, real bars, the shipped detectors."""
    thesis, sid = phantom
    for night, recorded in zip(_ARM_NIGHTS, calls_repo.list_for_thesis(db, thesis.id)):
        assert recorded.state is State.ARMED and recorded.armed_security_id == sid
        rev = next(t for t in recorded.armed_members[0].triggers if t.sources[0].source == "xbrl")
        assert rev.event_date == date(2026, 5, 11)  # Q1-2025 dated at its 2026 comparative ...
        assert rev.sources[0].detail["period_end"] == "2025-03-31"
        assert rev.sources[0].ref == "0001868275-26-000067"
        now = call_for_thesis(db, thesis.id, night, record=False)  # ... D3a dates it 2025-05-06
        assert now.state is State.WARMING and now.armed_members == []


def test_the_d3a_off_view_restates_the_pre_d3a_reading_and_nothing_else(db, phantom):
    _, sid = phantom
    pit = PointInTimeData(db, asof=date(2026, 10, 14), tenant_id=DEFAULT_TENANT_ID, basket={sid})
    view = PreD3aDatingView(pit)
    on, off = pit.fundamentals_facts(sid), view.fundamentals_facts(sid)
    assert [r["id"] for r in on] == [r["id"] for r in off]  # the SAME picks
    q1 = next(r for r in off if r["period_end"] == date(2025, 3, 31))
    assert (q1["knowable_from"], q1["knowable_accession"]) == (
        date(2026, 5, 11),
        "0001868275-26-000067",
    )
    q1_on = next(r for r in on if r["period_end"] == date(2025, 3, 31))
    assert (q1_on["knowable_from"], q1_on["knowable_accession"]) == (
        date(2025, 5, 6),
        "0001868275-25-000044",
    )  # the shared memo row is untouched (the view copies)
    assert view.asof == pit.asof and view.price_history(sid) == pit.price_history(sid)  # delegation
    # the revenue detector fires off the stale date through the view, and not through D3a's read
    assert ra.detect(view, sid, date(2026, 10, 14)) is not None
    assert ra.detect(pit, sid, date(2026, 10, 14)) is None


# --- the CLI: dry run, apply, re-apply — counting the table ---------------------------------------------


def test_dry_run_writes_nothing_apply_writes_exactly_the_candidates_and_a_rerun_adds_zero(
    db, phantom, tmp_path, capsys
):
    thesis, sid = phantom
    calls_before = _count(db, "calls")
    assert _count(db, "call_annotation") == 0

    # the D3a-off re-assembly reproduces each recorded row; D3a un-arms it -> exclude, both nights
    results = apa.classify_rows(db, thesis_id=thesis.id)
    assert [(r.asof, r.classification) for r in results] == [(n, "exclude") for n in _ARM_NIGHTS]
    for r in results:
        assert r.pre == r.recorded and r.recorded.state == "armed"
        assert r.post is not None and r.post.state == "warming" and r.post.armed == frozenset()
        # re-assembled on the PUBLIC clock at the night's end: the fixture thesis (and its first snapshot)
        # was created today, after the nights, so the roster by the as-of falls back to the live one — the
        # same single name — and the result says so
        assert r.roster.startswith("LIVE roster")
        assert r.known_at == known_at_for_asof(r.asof)
        (stale,) = r.stale
        assert stale["detector"] == "revenue_acceleration" and stale["d3a"] is None
        assert stale["pre_d3a"]["cited_filing"] == "0001868275-26-000067"
        assert stale["first_knowable"]["first_filing"] == "0001868275-25-000044"
        assert r.affected == [str(sid)]

    # DRY RUN (the CLI default): read-only connection, nothing written anywhere but the asked-for report
    report = tmp_path / "report.json"
    apa.main(["--thesis", str(thesis.id), "--quiet", "--report-json", str(report)])
    db.rollback()
    assert _count(db, "call_annotation") == 0 and _count(db, "calls") == calls_before
    assert report.exists() and '"exclude": 2' in report.read_text(encoding="utf-8")
    assert "TOTAL: 2 honest rows · 2 exclude" in capsys.readouterr().out

    # --apply: exactly the two excludes, carrying the evidence
    log_dir = tmp_path / "annotations"
    apa._DEFAULT_LOG_DIR = log_dir  # keep the provenance artifact in the test's tmp dir
    try:
        apa.main(["--thesis", str(thesis.id), "--quiet", "--apply"])
        db.rollback()
        assert _count(db, "call_annotation") == 2
        anns = call_annotations_repo.active_for_thesis(db, thesis.id)
        assert {(a.asof, a.action, a.reason) for a in anns} == {
            (n, "exclude", "stale_comparative_revenue") for n in _ARM_NIGHTS
        }
        assert all(a.rule.startswith("d3a-first-knowability@") for a in anns)
        assert all(a.detail["recorded"]["armed"] == ["CEG"] for a in anns)
        assert all(a.detail["d3a_on"]["state"] == "warming" for a in anns)
        assert len(list(log_dir.glob("*.json"))) == 1

        # a second --apply with the same rule appends NOTHING
        apa.main(["--thesis", str(thesis.id), "--quiet", "--apply"])
        db.rollback()
        assert _count(db, "call_annotation") == 2
    finally:
        apa._DEFAULT_LOG_DIR = _BACKEND.parent / "data" / "annotations"
    assert _count(db, "calls") == calls_before  # COUNT THE TABLE: the immutable log is untouched


def test_a_revoke_sticks_across_a_rerun(db, phantom, tmp_path):
    thesis, _ = phantom
    apa._DEFAULT_LOG_DIR = tmp_path
    try:
        apa.main(["--thesis", str(thesis.id), "--quiet", "--apply"])
        db.rollback()
        first, _ = call_annotations_repo.active_for_thesis(db, thesis.id)
        apa.main(["--revoke", str(first.id), "--reason", "operator: score this night", "--apply"])
        db.rollback()
        assert len(call_annotations_repo.active_for_thesis(db, thesis.id)) == 1
        apa.main(["--thesis", str(thesis.id), "--quiet", "--apply"])  # the family already judged it
        db.rollback()
        assert len(call_annotations_repo.active_for_thesis(db, thesis.id)) == 1
        assert _count(db, "call_annotation") == 3  # 2 excludes + 1 revoke, nothing re-appended
        apa.main(["--revoke-all", "--reason", "operator: score them all", "--apply"])
        db.rollback()
        assert call_annotations_repo.active_for_thesis(db, thesis.id) == []
    finally:
        apa._DEFAULT_LOG_DIR = _BACKEND.parent / "data" / "annotations"


def test_a_drift_row_is_never_annotated(db):
    """A row recorded under a DIFFERENT policy (here: the pre-D3a read with the breakout's return thrust
    raised out of reach — no confirmation, so it recorded WARMING) is not reproduced by today's D3a-off
    re-assembly (which arms): the change is the policy's, not D3a's, so it is reported as drift and never
    annotated — even though D3a changes today's reading of that night."""
    sid = _ceg(db)
    thesis = _thesis(db, sid)
    other_policy = DEFAULT_CONFIG.model_copy(update={"breakout_min_return": 0.5})
    _record_pre_d3a(db, thesis, _ARM_NIGHTS[0], cfg=other_policy)
    assert calls_repo.list_for_thesis(db, thesis.id)[0].state is not State.ARMED

    (r,) = apa.classify_rows(db, thesis_id=thesis.id)
    assert r.classification == "drift" and r.pre is not None and r.pre.state == "armed"
    assert r.config_matches is None  # the fixture row carries no stamp, like every legacy row
    apa.apply_annotations(db, [r], rule="d3a-first-knowability@test")
    db.commit()
    assert _count(db, "call_annotation") == 0


# --- the Scoreboard reads the annotations as a REVERSIBLE policy -----------------------------------------


def _scoreboard(db, **kw):
    """The Scoreboard with the L1 LATE reading pinned OFF. The fixture's rows are written by the real
    pre-D3a writer at TEST time — after their 2026-10-14/15 nights closed — so every one of them is a LATE
    row (recorded after the next EDGAR open), which the default reading sets aside before the annotation
    policy is ever reached. These tests isolate the ANNOTATED reading; the late reading (and how the two
    compose) is ``tests/scoreboard/test_late_rows.py``'s."""
    return assemble_scoreboard(db, exclude_late=False, **kw)


def _episodes(result) -> list[tuple[str, date]]:
    return [(t.name, e.episode.arm_date) for t in result.theses for e in t.episodes]


def test_scoreboard_sets_the_phantom_nights_aside_and_both_readings_reverse_exactly(db, phantom):
    """Before any annotation the two phantom nights open an episode. After the CLI annotates them: the
    default reading (setting on) opens NO episode and the banner names both nights; the include reading
    (the param) and the setting OFF both score them again, IDENTICALLY to the pre-annotation result.
    """
    thesis, _ = phantom
    asof = _ARM_NIGHTS[-1] + timedelta(days=1)
    before = _scoreboard(db, asof=asof, exclude_annotated=True)  # nothing annotated yet
    assert _episodes(before) == [(thesis.name, _ARM_NIGHTS[0])]
    assert before.summary.annotated_nights == []

    results = apa.classify_rows(db, thesis_id=thesis.id)
    apa.apply_annotations(db, results, rule="d3a-first-knowability@test")
    db.commit()

    excluded = _scoreboard(db, asof=asof)  # the setting's default: set aside
    assert excluded.summary.annotated_policy == "excluded"
    assert _episodes(excluded) == []
    assert [(n.thesis_name, n.asof, n.reasons) for n in excluded.summary.annotated_nights] == [
        (thesis.name, n, ["stale_comparative_revenue"]) for n in _ARM_NIGHTS
    ]

    included = _scoreboard(db, asof=asof, exclude_annotated=False)  # the include_annotated param
    assert included.summary.annotated_policy == "included"
    _same_record(included, before)
    (ep,) = included.theses[0].episodes
    assert (
        ep.annotation_note is not None
        and "phantom-arm night(s) 2026-10-14, 2026-10-15" in ep.annotation_note
    )


def test_the_setting_off_scores_the_annotated_nights_as_before(db, phantom, monkeypatch):
    from domain.settings import get_settings

    thesis, _ = phantom
    asof = _ARM_NIGHTS[-1] + timedelta(days=1)
    before = _scoreboard(db, asof=asof, exclude_annotated=False)
    apa.apply_annotations(
        db, apa.classify_rows(db, thesis_id=thesis.id), rule="d3a-first-knowability@t"
    )
    db.commit()
    monkeypatch.setenv("ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED", "false")
    get_settings.cache_clear()
    try:
        off = _scoreboard(db, asof=asof)
    finally:
        monkeypatch.delenv("ALPHADECK_SCOREBOARD_EXCLUDE_ANNOTATED")
        get_settings.cache_clear()
    assert off.summary.annotated_policy == "included"
    _same_record(off, before)


def _same_record(a, b) -> None:
    """Every scored field identical — the annotation reading changes nothing but the policy fields and
    the drawer caption."""
    strip = {"annotation_note"}
    da = [e.model_dump(exclude=strip) for t in a.theses for e in t.episodes]
    db_ = [e.model_dump(exclude=strip) for t in b.theses for e in t.episodes]
    assert da == db_
    keep = a.summary.model_dump(exclude={"annotated_policy", "annotated_nights"})
    assert keep == b.summary.model_dump(exclude={"annotated_policy", "annotated_nights"})


def test_an_unannotated_night_is_unaffected_by_the_policy(db, phantom):
    """A third thesis-night (a separate thesis, nothing annotated) scores identically under both readings."""
    sid = phantom[1]  # the same real facts
    other = _thesis(db, sid)
    _record_pre_d3a(db, other, _ARM_NIGHTS[0])
    apa.apply_annotations(
        db, apa.classify_rows(db, thesis_id=phantom[0].id), rule="d3a-first-knowability@t"
    )
    db.commit()
    asof = _ARM_NIGHTS[-1] + timedelta(days=1)
    ex = {t.name + str(t.thesis_id): t for t in _scoreboard(db, asof=asof).theses}
    inc = {
        t.name + str(t.thesis_id): t
        for t in _scoreboard(db, asof=asof, exclude_annotated=False).theses
    }
    key = other.name + str(other.id)
    assert [e.model_dump() for e in ex[key].episodes] == [e.model_dump() for e in inc[key].episodes]
    assert len(ex[key].episodes) == 1  # the un-annotated arm night still opens its episode


# --- pure contract: the classification -----------------------------------------------------------------


def _shape(state="armed", verdict="starter_entry", headline="a", armed=("a",)):
    return CallShape(state=state, verdict=verdict, headline=headline, armed=frozenset(armed))


def test_classify_contract():
    rec = _shape()
    # exclude: reproduced, armed, D3a un-arms
    assert classify(rec, rec, _shape("warming", "not_yet", None, ()))[0] == "exclude"
    # note: reproduced, D3a changes the composition but the arm stands
    assert classify(rec, rec, _shape(armed=("a", "b")))[0] == "note"
    assert classify(rec, rec, _shape(verdict="core_entry"))[0] == "note"
    assert classify(rec, rec, _shape(headline="b", armed=("a", "b")))[0] == "note"
    # a non-armed night D3a moves is a note, never an exclusion
    warm = _shape("warming", "not_yet", None, ())
    assert classify(warm, warm, _shape("incubating", "watching", None, ()))[0] == "note"
    # drift: (i) does not reproduce state, verdict, or the armed set
    assert classify(rec, _shape("warming", "not_yet", None, ()), warm)[0] == "drift"
    assert classify(rec, _shape(verdict="core_entry"), warm)[0] == "drift"
    assert classify(rec, _shape(armed=("a", "b")), warm)[0] == "drift"
    # unchanged
    assert classify(rec, rec, rec) == ("unchanged", "")


def test_the_fundamentals_readers_are_registry_derived():
    assert {d.name for d in apa.fundamentals_detectors(DEFAULT_CONFIG)} == {
        "revenue_acceleration",
        "share_creep",
    }


# --- structural: the D3a-off switch cannot reach production ---------------------------------------------


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


def test_no_production_module_imports_the_annotator_or_its_d3a_off_view():
    """The D3a-off view lives ONLY in the diagnostic CLI; no other non-test module imports it (or names
    it), so the cron, the API, the backfill and every call-of-record writer cannot switch D3a off.
    """
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND).as_posix()
        if rel.startswith(("tests/", ".venv/")) or rel == "pipeline/annotate_phantom_arms.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "PreD3aDatingView" in text or any("annotate_phantom_arms" in m for m in _imports(path)):
            offenders.append(rel)
    assert offenders == []


def test_the_annotator_cannot_ingest_or_notify():
    """A recompute-and-annotate, nothing else (the backfill's import guard): no fetch, refresh, sweep, or
    notify surface — and not pipeline.daily, which would drag them all in."""
    mods = _imports(_BACKEND / "pipeline" / "annotate_phantom_arms.py")
    forbidden = ("ingest", "notify", "radar", "pipeline.daily", "pipeline.ingest_thesis")
    assert [m for m in mods if m.split(".")[0] in forbidden or m.startswith(forbidden)] == []


def test_load_call_inputs_is_the_funnel_call_for_thesis_runs(db, phantom):
    """``call_for_thesis(record=False)`` == ``assemble_from_pit`` over ``load_call_inputs`` — the D3a-on
    re-assembly IS the live call."""
    thesis, _ = phantom
    night = _ARM_NIGHTS[0]
    thesis_l, pit = load_call_inputs(db, thesis.id, night)
    assert assemble_from_pit(pit, thesis_l, night, DEFAULT_CONFIG) == call_for_thesis(
        db, thesis.id, night, record=False
    )

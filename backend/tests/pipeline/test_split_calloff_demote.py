"""F10 P9: the demote switch (``split_calloff_demote``, shipped OFF). When on, an announced or confirmed split whose
same-ratio call-off was filed from its announcement to its first session goes to review: at decision time
(``domain.split_calloffs.refuse_called_off``, after ``backfill_splits.decide_polygon_group``, after each
``with_tape`` in ``detect_splits.detect_security`` and after ``plan_against``'s late ``decide``), and, over the list,
ONE needs-review version over a live split the system wrote (``plan_against``). The steward's rows are never touched.
Off, every decision is today's.

The call-off evidence here is SYNTHETIC (``SecEvidence.calloff`` as P2 defined it): the reader that sets it on real
filings is P4's, not in this base. The AIRI and ATCH shapes are ``tests/domain/test_split_calloffs``'s.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from domain.split_calloffs import refuse_called_off
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_AUTO,
    VIA_SEC_TAPE,
    CallOff,
    SecEvidence,
    decide,
)
from ingest.edgar.form8k import filing_index_url
from pipeline import backfill_splits as bf
from pipeline import detect_splits as ds
from pipeline import split_history
from repositories import split_repo
from tests.domain.test_split_calloffs import (
    AIRI_CALLED_OFF,
    AIRI_ROW,
    ATCH_ROW,
    FORMS,
    STEP_TAPE,
    airi_8k,
    airi_announcing,
    atch_8k,
)
from tests.pipeline.test_detect_splits import _backfill_scco, _latest, _run, _seed
from tests.pipeline.test_split_history import _fill
from tests.split_801_fixtures import seed_enha, seed_member
from tests.split_s4_fixtures import CIKS

DEMOTE_VAR = "ALPHADECK_SPLIT_CALLOFF_DEMOTE"
AIRI_CIK = "0001009891"
NIGHT = date(2026, 11, 20)  # AIRI's first session


@pytest.fixture
def demote(monkeypatch):
    """Turn the switch on (``on()``) or off (``off()``) for what reads the settings next."""

    def set_(value: str | None):
        if value is None:
            monkeypatch.delenv(DEMOTE_VAR, raising=False)
        else:
            monkeypatch.setenv(DEMOTE_VAR, value)
        get_settings.cache_clear()

    set_(None)
    yield set_
    monkeypatch.delenv(DEMOTE_VAR, raising=False)
    get_settings.cache_clear()


class _Reader:
    """A ``SecReader`` stand-in: in each window asked, the filings filed in it (no fetch, no database)."""

    def __init__(self, cik: str | None, *filings: SecEvidence) -> None:
        self.filings = list(filings)
        self.submissions = {cik: {"read": True}} if cik else {}
        self.evidence: dict[str, SecEvidence | None] = {}
        self.read_here: set[str] = set()

    def index(self, m):
        return self.submissions.get(m.cik)

    def refs(self, conn, m, lo, hi):
        return {s.accession: s for s in self.filings if lo <= s.filed <= hi}

    def wide_refs(self, m, lo, hi):
        return {}

    def read(self, m, refs, *, confirmed=()):
        out = [refs[a] for a in sorted(refs)]
        for s in out:
            self.evidence[s.accession] = s
            self.read_here.add(s.accession)
        return out, 0


def _member(ticker: str = "AIRI", cik: str | None = AIRI_CIK) -> bf.Member:
    return bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), ticker, None, cik)


def _detect(monkeypatch, m, rows, filings, *, asof=NIGHT, tape=None, demote_forms=None):
    """``detect_security`` over these Massive.com rows and filings, the tape answering ``tape`` on its session."""
    monkeypatch.setattr(
        ds,
        "tape_at",
        lambda conn, *, tenant_id, security_id, session: (
            tape if tape is not None and tape.session == session else None
        ),
    )
    kw = {} if demote_forms is None else {"demote_forms": demote_forms}
    return ds.detect_security(
        None,
        _Reader(m.cik, *filings),
        m,
        rows,
        asof=asof,
        sec_since=asof - timedelta(days=ds.SEC_LOOKBACK_DAYS),
        distribution_max_factor=None,
        label_overrides_abstain=False,
        confirmed=(),
        **kw,
    )


def _plan(det, stored, *, asof=NIGHT, demote_forms=None):
    kw = {} if demote_forms is None else {"demote_forms": demote_forms}
    return ds.plan_against(
        det,
        list(stored),
        asof=asof,
        distribution_max_factor=None,
        label_overrides_abstain=False,
        **kw,
    )


def _listed(m, d, *, author=split_repo.AUTHOR_DETECT):
    """A stored version of decision ``d`` written by ``author``."""
    v = ds._as_version(m, d)
    if author == split_repo.AUTHOR_OPERATOR:
        return v.model_copy(update={"author": author, "action": split_repo.ACTION_CONFIRM})
    if author == split_repo.AUTHOR_BACKFILL:
        return v.model_copy(update={"author": author, "action": split_repo.ACTION_BACKFILL})
    return v


# --- at decision time ----------------------------------------------------------------------------------------


def test_on_the_airi_shape_decided_on_its_first_session_night_goes_to_review_with_the_note(
    monkeypatch,
):
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    today = decide(AIRI_ROW, filings, asof=NIGHT)
    assert (today.status, today.confirmed_via) == (STATUS_CONFIRMED, VIA_AUTO)
    off = _detect(monkeypatch, m, [AIRI_ROW], filings)
    assert off.decisions == [today]  # off: today's decision
    on = _detect(monkeypatch, m, [AIRI_ROW], filings, demote_forms=FORMS)
    (d,) = on.decisions
    assert (d.status, d.confirmed_via) == (STATUS_NEEDS_REVIEW, None)
    assert d.note == f"{today.note} {AIRI_CALLED_OFF}"
    assert (
        replace(d, status=today.status, confirmed_via=today.confirmed_via, note=today.note) == today
    )
    # everything else the check says is the same
    assert (on.unplaced, on.filings_read, on.filings_unread) == (
        off.unplaced,
        off.filings_read,
        off.filings_unread,
    )


def test_on_the_backfills_decision_is_refused_too(monkeypatch):
    """``decide_polygon_group`` (the S1 backfill, the newcomer fill and the nightly) refuses before it returns."""
    monkeypatch.setattr(bf, "confirmed_splits", lambda conn, m: ())
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    g_off = bf.decide_polygon_group(
        None,
        _Reader(m.cik, *filings),
        m,
        [AIRI_ROW],
        asof=NIGHT,
        distribution_max_factor=None,
        label_overrides_abstain=False,
    )
    assert g_off.decision == decide(AIRI_ROW, filings, asof=NIGHT)
    g_on = bf.decide_polygon_group(
        None,
        _Reader(m.cik, *filings),
        m,
        [AIRI_ROW],
        asof=NIGHT,
        distribution_max_factor=None,
        label_overrides_abstain=False,
        demote_forms=FORMS,
    )
    assert g_on.decision == refuse_called_off(g_off.decision, filings, FORMS)
    assert g_on.decision.status == STATUS_NEEDS_REVIEW
    (p,) = bf.plan_members(
        None,
        _Reader(m.cik, *filings),
        {m: [AIRI_ROW]},
        asof=NIGHT,
        distribution_max_factor=None,
        label_overrides_abstain=False,
        demote_forms=FORMS,
    )
    assert p.decision == g_on.decision


def test_on_the_atch_shape_is_not_demoted(monkeypatch):
    """A failed vote stating no ratio, filed six days after its first session: confirmed, on or off."""
    m = _member("ATCH", "0001000001")
    night = date(2025, 1, 8)
    off = _detect(monkeypatch, m, [ATCH_ROW], [atch_8k()], asof=night)
    on = _detect(monkeypatch, m, [ATCH_ROW], [atch_8k()], asof=night, demote_forms=FORMS)
    assert on.decisions == off.decisions
    assert [d.status for d in on.decisions] == [STATUS_CONFIRMED]
    stored = _listed(m, off.decisions[0])
    assert [(p.action, p.reason) for p in _plan(on, [stored], asof=night, demote_forms=FORMS)] == [
        (ds.UNCHANGED, "")
    ]


def test_a_massive_row_its_only_filing_calls_off_never_reaches_the_tape_off_or_on(monkeypatch):
    """Massive.com + tape, the call-off the row's only filing: P4's tape guard (``detect_security``: a filing in the
    row's window calls off its exact ratio) keeps it from the tape, so it waits for review off; on, the demote has
    nothing to refuse (``refuse_called_off`` returns a needs-review decision as it is)."""
    m = _member()
    off = _detect(monkeypatch, m, [AIRI_ROW], [airi_8k()], tape=STEP_TAPE)
    (d,) = off.decisions
    assert (d.status, d.confirmed_via) == (STATUS_NEEDS_REVIEW, None)
    on = _detect(monkeypatch, m, [AIRI_ROW], [airi_8k()], tape=STEP_TAPE, demote_forms=FORMS)
    assert on.decisions == off.decisions == [refuse_called_off(d, [airi_8k()], FORMS)]
    assert not on.decisions[0].note.endswith(AIRI_CALLED_OFF)


def test_on_the_with_tape_routes_are_refused(monkeypatch):
    """SEC + tape (the announcing 8-K, no Massive.com row): confirmed by the tape off, sent to review on. (The
    Massive.com + tape route with its call-off in the window never reaches the tape: the test above.)
    """
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    off = _detect(monkeypatch, m, [], filings, tape=STEP_TAPE)
    (d,) = off.decisions
    assert (d.status, d.confirmed_via) == (STATUS_CONFIRMED, VIA_SEC_TAPE)
    on = _detect(monkeypatch, m, [], filings, tape=STEP_TAPE, demote_forms=FORMS)
    assert on.decisions == [refuse_called_off(d, filings, FORMS)]
    assert on.decisions[0].status == STATUS_NEEDS_REVIEW


def test_the_check_keeps_every_filing_it_read_once(monkeypatch):
    m = _member()
    det = _detect(monkeypatch, m, [AIRI_ROW], [airi_announcing(), airi_8k()])
    assert det.read == [airi_announcing(), airi_8k()]


# --- over the list (``plan_against``) ------------------------------------------------------------------------


def test_on_a_system_announced_row_is_demoted_once_by_a_call_off_filed_before_its_first_session(
    monkeypatch,
):
    m = _member()
    listed = _listed(m, decide(AIRI_ROW, [airi_announcing()], asof=date(2026, 11, 10)))
    assert listed.status == STATUS_ANNOUNCED
    night = date(2026, 11, 19)  # the call-off's own night, a day before the first session
    filings = [airi_announcing(), airi_8k()]
    off = _detect(monkeypatch, m, [AIRI_ROW], filings, asof=night)
    assert [(p.action, p.reason) for p in _plan(off, [listed], asof=night)] == [(ds.UNCHANGED, "")]
    on = _detect(monkeypatch, m, [AIRI_ROW], filings, asof=night, demote_forms=FORMS)
    (p,) = _plan(on, [listed], asof=night, demote_forms=FORMS)
    assert (p.action, p.reason, p.stored) == (ds.APPEND, ds.CALLED_OFF, listed)
    assert (p.values["status"], p.values["confirmed_via"]) == (STATUS_NEEDS_REVIEW, None)
    assert p.decision.key_date == listed.key_date and p.values["note"].endswith(AIRI_CALLED_OFF)
    # the next night it is open and its own: nothing new to write
    again = _plan(on, [_listed(m, p.decision)], asof=night, demote_forms=FORMS)
    assert [(q.action, q.reason) for q in again] == [(ds.UNCHANGED, "")]


@pytest.mark.parametrize("author", [split_repo.AUTHOR_DETECT, split_repo.AUTHOR_BACKFILL])
def test_on_a_system_confirmed_row_gets_one_needs_review_version(monkeypatch, author):
    """The one exception to "a confirmed split is never touched": a live split the system wrote."""
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    listed = _listed(m, decide(AIRI_ROW, filings, asof=NIGHT), author=author)
    off = _detect(monkeypatch, m, [AIRI_ROW], filings)
    assert [(p.action, p.reason) for p in _plan(off, [listed])] == [(ds.UNCHANGED, "")]
    on = _detect(monkeypatch, m, [AIRI_ROW], filings, demote_forms=FORMS)
    (p,) = _plan(on, [listed], demote_forms=FORMS)
    assert (p.action, p.reason, p.values["status"]) == (
        ds.APPEND,
        ds.CALLED_OFF,
        STATUS_NEEDS_REVIEW,
    )
    again = _plan(on, [_listed(m, p.decision)], demote_forms=FORMS)
    assert [(q.action, q.reason) for q in again] == [(ds.UNCHANGED, "")]


def test_on_an_operator_row_is_never_touched(monkeypatch):
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    listed = _listed(m, decide(AIRI_ROW, filings, asof=NIGHT), author=split_repo.AUTHOR_OPERATOR)
    on = _detect(monkeypatch, m, [AIRI_ROW], filings, demote_forms=FORMS)
    (p,) = _plan(on, [listed], demote_forms=FORMS)
    assert (p.action, p.reason) == (ds.UNCHANGED, "the operator's decision stands")


def test_on_the_late_decide_is_refused(monkeypatch):
    """Massive.com's row has left the 30-day pull: the announcing 8-K, re-read in the 60-day lookback, meets the
    row the listed split carries (the late ``decide``), which confirms it; on, refused there too."""
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    listed = _listed(m, decide(AIRI_ROW, [airi_announcing()], asof=NIGHT))
    night = date(2026, 12, 28)
    off = _detect(monkeypatch, m, [], filings, asof=night)
    assert [d.status for d in off.decisions] == [STATUS_NEEDS_REVIEW]  # SEC only, no tape bar
    assert [(p.action, p.decision.status) for p in _plan(off, [listed], asof=night)] == [
        (ds.UNCHANGED, STATUS_CONFIRMED)
    ]
    on = _detect(monkeypatch, m, [], filings, asof=night, demote_forms=FORMS)
    assert on.decisions == off.decisions  # nothing live to refuse yet
    (p,) = _plan(on, [listed], asof=night, demote_forms=FORMS)
    assert (p.action, p.reason, p.decision.status) == (
        ds.APPEND,
        ds.CALLED_OFF,
        STATUS_NEEDS_REVIEW,
    )
    assert p.decision.note.endswith(AIRI_CALLED_OFF) and p.decision.polygon == AIRI_ROW


def test_off_the_plan_is_todays_even_with_call_offs_read(monkeypatch):
    m = _member()
    filings = [airi_announcing(), airi_8k()]
    det = _detect(monkeypatch, m, [AIRI_ROW], filings)
    listed = _listed(m, det.decisions[0])
    assert _plan(det, [listed]) == _plan(det, [listed], demote_forms=None)
    assert [(p.action, p.reason) for p in _plan(det, [listed])] == [(ds.UNCHANGED, "")]


def test_a_demoted_row_reports_the_call_off_on_its_line():
    d = refuse_called_off(
        decide(AIRI_ROW, [airi_announcing(), airi_8k()], asof=NIGHT),
        [airi_announcing(), airi_8k()],
        FORMS,
    )
    line = ds.Detected(
        uuid.uuid4(),
        "AIRI",
        d.key_date,
        d.effective_date,
        "1-for-5 reverse split",
        d.status,
        d.confirmed_via,
        ds._sources(d),
        ds.CALLED_OFF,
        d.note,
    ).line
    assert line == "AIRI 1-for-5 reverse split needs review (8-K + Massive.com; called off)"


# --- the switch: read once a run by each edge, passed down -----------------------------------------------------


def test_the_switch_ships_off_and_on_names_the_warnings_forms(demote, monkeypatch):
    assert bf.calloff_demote_forms() is None
    demote("true")
    assert bf.calloff_demote_forms() == ds.calloff_rule().forms == FORMS
    monkeypatch.setenv("ALPHADECK_SPLIT_CALLOFF_FORMS", "6-K")
    get_settings.cache_clear()
    assert bf.calloff_demote_forms() == ("6-K",)
    demote("false")
    assert bf.calloff_demote_forms() is None


def test_the_detector_reads_the_switch_once_a_pass_and_passes_it_down(demote, monkeypatch):
    seen: list[tuple] = []

    def detect(conn, reader, m, rows, **kw):
        seen.append(("detect", kw.get("demote_forms")))
        return ds.SecurityDetection(member=m)

    def plan(detection, stored, **kw):
        seen.append(("plan", kw.get("demote_forms")))
        return []

    monkeypatch.setattr(ds, "detect_security", detect)
    monkeypatch.setattr(ds, "plan_against", plan)
    monkeypatch.setattr(split_repo, "latest_versions", lambda conn, **kw: [])
    for value, forms in ((None, None), ("true", FORMS)):
        demote(value)
        det = ds.SplitDetector(asof=NIGHT, polygon_rows=[], polygon_status="on", apply=False)
        det._former = {}
        assert det.calloff_demote_forms == forms
        demote(None)  # read once a pass: a later change waits for the next pass
        sec = ds._MasterRow(uuid.uuid4(), "AIRI", None, None)
        det.check(None, None, sec, tenant_id=DEFAULT_TENANT_ID)
    assert seen == [("detect", None), ("plan", None), ("detect", FORMS), ("plan", FORMS)]


def _plan_spy(monkeypatch, module) -> list:
    seen: list = []
    plan = bf.plan_members

    def spy(*a, **kw):
        seen.append(kw.get("demote_forms"))
        return plan(*a, **kw)

    monkeypatch.setattr(module, "plan_members", spy)
    return seen


def test_the_backfill_passes_the_switch_to_its_plan(db, tmp_path, demote, monkeypatch):
    """The three edges that decide a Massive.com row (``distribution_cap``'s) read it the same way."""
    seen = _plan_spy(monkeypatch, bf)
    seed_member(db, "SCCO", None, [])
    _backfill_scco(db, tmp_path, asof=date(2026, 11, 5))
    demote("true")
    _backfill_scco(db, tmp_path, asof=date(2026, 11, 6))
    assert seen == [None, FORMS]


def test_the_newcomer_fill_passes_the_switch_to_its_plan(db, tmp_path, demote, monkeypatch):
    seen = _plan_spy(monkeypatch, split_history)
    seed_enha(db)
    _fill(db, tmp_path, apply=False)
    demote("true")
    _fill(db, tmp_path, apply=False)
    assert seen == [None, FORMS]


# --- the nightly on a real database (GAME's 1-for-8 and a SYNTHETIC call-off 8-K) ------------------------------
#
# GAME's 8-K 0001493152-26-039213 (filed 2026-08-19) announces its 1-for-8, first session 2026-08-24, and Massive.com
# lists it: the nightly confirms it on 09-01 (``test_detect_splits``). The call-off below is SYNTHETIC: an 8-K (items
# 8.01) the stored corporate events hold for GAME, filed 08-21, read (no fetch) as stating it would not effect the
# 1-for-8. The table is counted.

GAME_CALLOFF = "0001493152-26-039500"
GAME_PASSAGE = (
    "The Company announced that it would not be effecting the 1-for-8 reverse stock split."
)


def _game_calloff(db, sid, monkeypatch) -> None:
    filed = date(2026, 8, 21)
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "form": "8-K",
            "items": ["8.01", "9.01"],
            "accession": GAME_CALLOFF,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/{int(CIKS['GAME'])}/{GAME_CALLOFF}",
            "valid_from": filed,
        },
    )
    db.commit()
    real = bf.read_filing

    def read_filing(edgar, cik, ref, **kw):
        if ref.accession != GAME_CALLOFF:
            return real(edgar, cik, ref, **kw)
        return SecEvidence(
            accession=ref.accession,
            form=ref.form,
            filed=ref.filed,
            url=filing_index_url(cik, ref.accession),
            abstain_reason="called off: decision (1-for-8 stated)",
            about_a_split=True,
            calloff=CallOff(
                kind="decision",
                ratio_from=Decimal(8),
                ratio_to=Decimal(1),
                passage=GAME_PASSAGE,
                document="8-K",
            ),
        )

    monkeypatch.setattr(bf, "read_filing", read_filing)


GAME_NOTE = f"Called off: 8-K {GAME_CALLOFF} (filed 2026-08-21) \u2014 {GAME_PASSAGE}"


def test_on_the_nightly_lists_a_called_off_split_for_review_once(db, tmp_path, demote, monkeypatch):
    ids = _seed(db, ["GAME"])
    _game_calloff(db, ids["GAME"], monkeypatch)
    demote("true")
    det = _run(db, tmp_path, asof=date(2026, 9, 1))
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.confirmed_via, v.author) == (
        STATUS_NEEDS_REVIEW,
        None,
        split_repo.AUTHOR_DETECT,
    )
    assert v.note.endswith(GAME_NOTE) and v.polygon_id and v.sec_accession == "0001493152-26-039213"
    assert [a.line for a in det.result.appended] == [
        "GAME 1-for-8 reverse split needs review (8-K + Massive.com)"
    ]
    _run(db, tmp_path, asof=date(2026, 9, 2))
    assert split_repo.count_rows(db) == 1


def test_on_a_confirmed_row_is_demoted_once_off_again_re_confirms_it(
    db, tmp_path, demote, monkeypatch
):
    """The listed split confirmed (the switch off, as today), the switch turned on: ONE needs-review version, the
    steward's to settle. Turned off again, the open system row is re-decided and re-confirmed: settle first.
    """
    ids = _seed(db, ["GAME"])
    _game_calloff(db, ids["GAME"], monkeypatch)
    _run(db, tmp_path, asof=date(2026, 9, 1))
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.confirmed_via) == (STATUS_CONFIRMED, VIA_AUTO)  # off: today's
    demote("true")
    det = _run(db, tmp_path, asof=date(2026, 9, 2))
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, v.author, v.action) == (
        STATUS_NEEDS_REVIEW,
        split_repo.AUTHOR_DETECT,
        split_repo.ACTION_DETECT,
    )
    assert v.note.endswith(GAME_NOTE) and split_repo.count_rows(db) == 2
    assert [a.line for a in det.result.appended] == [
        "GAME 1-for-8 reverse split needs review (8-K + Massive.com; called off)"
    ]
    # the steward can act on it: confirm a source's values, or reject
    assert split_repo.allowed_actions(v) == ["confirm:sec", split_repo.ACTION_REJECT]
    _run(db, tmp_path, asof=date(2026, 9, 3))
    assert split_repo.count_rows(db) == 2
    demote(None)
    det = _run(db, tmp_path, asof=date(2026, 9, 4))
    (v,) = _latest(db, ids["GAME"])
    assert (v.status, det.result.appended[0].verb) == (STATUS_CONFIRMED, "updated")


def test_on_the_stewards_confirm_of_a_demoted_row_stands(db, tmp_path, demote, monkeypatch):
    ids = _seed(db, ["GAME"])
    _game_calloff(db, ids["GAME"], monkeypatch)
    demote("true")
    _run(db, tmp_path, asof=date(2026, 9, 1))
    (v,) = _latest(db, ids["GAME"])
    split_repo.confirm(db, v.id, source="sec", tenant_id=DEFAULT_TENANT_ID, today=date(2026, 9, 2))
    db.commit()
    det = _run(db, tmp_path, asof=date(2026, 9, 2))
    (now,) = _latest(db, ids["GAME"])
    assert (now.status, now.author) == (STATUS_CONFIRMED, split_repo.AUTHOR_OPERATOR)
    assert det.result.appended == [] and split_repo.count_rows(db) == 2

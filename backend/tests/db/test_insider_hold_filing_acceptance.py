"""Rule ``2026-11-held-row-reads-its-filings-acceptance`` — a held Form 4 row with no acceptance of its own reads
its filing's stored acceptance (``db.public_clock.hold_accepted_sql``), at every seam: the insider read's loader
(both clocks), the steward's queue and its decision verbs, and the backtest mirror.

The REAL instance (``tests/held_fixtures.py``, ``NO_ACCEPTANCE_*``): one Form 4 stored under two securities of
its issuer with NO acceptance on its rows and its hold (prod's state on 2026-11-12), its row 1 held for the year
rule. ``pipeline.backfill_accepted`` (its own ``run_backfill``, fed SEC's listing of that acceptance through the
suite's fake submissions client) then appends the acceptance to the stored rows; the hold is never updated.
Every write is checked by COUNTING the tables, never only through the read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from psycopg import sql

from db.insider_resolution import load_inputs
from db.public_clock import aux_public_sql, hold_accepted_sql, public_params
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from ingest.edgar.form4 import ingest_form4
from pipeline.backfill_accepted import run_backfill
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import insider_hold_repo as repo
from signals.base import PointInTimeData
from tests import held_fixtures as H
from tests.db.test_insider_holds_read import _assert_equal
from tests.pipeline.test_backfill_accepted import _FakeClient, _subs

T = DEFAULT_TENANT_ID
ACC = H.NO_ACCEPTANCE_ACCESSION
SEQ = H.NO_ACCEPTANCE_HELD_SEQ
ACCEPTED = H.NO_ACCEPTANCE_ACCEPTED
HELD_AT = H.NO_ACCEPTANCE_HELD_AT
MARKET_DAY = date(2025, 7, 25)
FAR = H.FAR
NO_ACCEPTANCE = "no acceptance time is known for the filing"
YEAR_RULE = (
    "the period of report as filed (0025-07-25) fails the year rule (before 1934, or after the "
    "filing's year)"
)


def _cap(asof: date) -> datetime:
    """The end of night ``asof`` — what a history view of that night reads."""
    return known_at_for_asof(asof, FAR)


def _seed(db) -> tuple[UUID, UUID]:
    """The issuer's two securities, the filing stored under each with no acceptance (prod's state)."""
    a = H.make_issuer_security(db, "HLDNA", H.NO_ACCEPTANCE_CIK)
    b = H.make_issuer_security(db, "HLDNB", H.NO_ACCEPTANCE_CIK)
    H.ingest_without_acceptance(db, a)
    H.ingest_without_acceptance(db, b)
    return a, b


def _fill(db, tickers: list[str] | None = None):
    """``backfill_accepted``'s NULL-only ``--execute`` itself, fed SEC's listing of the acceptance."""
    client = _FakeClient({str(int(H.NO_ACCEPTANCE_CIK)): _subs([(ACC, H.NO_ACCEPTANCE_LISTED)])})
    res = run_backfill(db, client=client, execute=True, tickers=tickers, log=lambda *_: None)
    db.commit()
    return res


def _counts(db) -> dict[str, int]:
    out = {}
    with db.cursor() as cur:
        for t in ("fact_insider_txn", "insider_txn_hold", "insider_txn_hold_decision"):
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — fixed names
            out[t] = cur.fetchone()["n"]
    return out


def _fact_rows(db, sid: UUID) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT txn_seq, accepted, recorded_at FROM fact_insider_txn WHERE security_id = %s "
            "ORDER BY recorded_at, txn_seq",
            (sid,),
        )
        return cur.fetchall()


def _hold_rows(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM insider_txn_hold ORDER BY security_id, accession, txn_seq")
        return cur.fetchall()


def _item(db, sid: UUID) -> repo.HoldItem:
    (it,) = [i for i in repo.items(db, tenant_id=T) if i.hold["security_id"] == sid]
    return it


def _ratified(db, sid: UUID, asof: date, known_at: datetime, clock: str) -> list[tuple]:
    rows = PointInTimeData(db, asof=asof, known_at=known_at, clock=clock, workspace=T).insider_txns(
        sid
    )
    return sorted(
        (r["valid_from"], r["txn_code"], float(r["shares"]), r.get("date_basis"))
        for r in rows
        if r.get("date_basis") is not None
    )


def _public_holds(db, sid: UUID, known_at: datetime, clock: str = "public") -> list[dict]:
    return list(
        load_inputs(db, [sid], known_at=known_at, tenant_id=T, clock=clock).holds.get(str(sid), ())
    )


# --- 1. today's state rebuilt ----------------------------------------------------------------------------------


def test_with_no_acceptance_stored_anywhere_the_steward_cannot_date_the_row(db):
    a, b = _seed(db)
    counts = _counts(db)
    assert counts["insider_txn_hold"] == 2  # one held row per security
    rows_a, rows_b = _fact_rows(db, a), _fact_rows(db, b)
    assert len(rows_a) == len(rows_b) > 0 and counts["fact_insider_txn"] == 2 * len(rows_a)
    assert SEQ not in {r["txn_seq"] for r in rows_a}  # the held row is not a stored fact
    assert {r["accepted"] for r in rows_a + rows_b} == {None}
    for h in _hold_rows(db):
        assert (h["accepted"], h["filed"], h["txn_seq"], h["reason"]) == (
            None,
            None,
            SEQ,
            "year_before_1934",
        )
        assert h["period_of_report_as_filed"] == "0025-07-25"

    it = _item(db, a)
    assert (it.bucket, it.market_date, it.owed_why) == ("owed", None, NO_ACCEPTANCE)
    assert (it.suggested, it.agrees) == (None, False)
    hid = it.id
    with pytest.raises(ValueError, match="no acceptance and no filing date"):
        repo.approve(db, hid, MARKET_DAY, None, tenant_id=T)
    db.rollback()
    assert _counts(db) == counts


# --- 2. after the fill -----------------------------------------------------------------------------------------


def test_after_the_fill_the_hold_reads_its_filings_acceptance_and_can_be_ratified(db):
    a, b = _seed(db)
    holds_before = _hold_rows(db)
    before = _counts(db)
    per_security = len(_fact_rows(db, a))

    res = _fill(db)

    after = _counts(db)
    # the stored rows gained one acceptance version each; the hold table is untouched (never updated)
    assert res.rows_corrected == 2 * per_security
    assert after == {
        **before,
        "fact_insider_txn": before["fact_insider_txn"] + 2 * per_security,
    }
    assert _hold_rows(db) == holds_before
    assert {h["accepted"] for h in _hold_rows(db)} == {None}  # the hold's own column stays NULL

    it = _item(db, a)
    assert it.hold["accepted"] == ACCEPTED  # the RESOLVED acceptance
    assert (it.bucket, it.market_date, it.owed_why) == ("owed", MARKET_DAY, YEAR_RULE)
    # a suggestion of the filing's market date that does NOT agree (no readable period of report)
    assert (it.suggested, it.agrees) == (MARKET_DAY, False)

    hid = it.id
    with pytest.raises(ValueError, match="does not agree with the filing's period of report"):
        repo.approve(db, hid, None, None, tenant_id=T)
    db.rollback()
    assert _counts(db) == after
    with pytest.raises(ValueError, match="is after the filing's market date 2025-07-25"):
        repo.approve(db, hid, date(2025, 7, 26), None, tenant_id=T)
    db.rollback()
    assert _counts(db) == after

    dec = repo.approve(db, hid, MARKET_DAY, "dated by the steward", tenant_id=T)
    db.commit()
    assert _counts(db)["insider_txn_hold_decision"] == after["insider_txn_hold_decision"] + 1
    assert datetime.fromisoformat(dec["detail"]["accepted"]) == ACCEPTED
    assert dec["detail"]["filing_market_date"] == "2025-07-25"
    decided = datetime.now(timezone.utc) + timedelta(seconds=5)
    expected = [(MARKET_DAY, "S", 2076.0, "ratified")]  # the held sale (row 1 of the filing)
    asof = date(2025, 8, 1)
    assert _ratified(db, a, asof, decided, "record") == expected
    assert _ratified(db, a, asof, FAR, "public") == expected
    assert _ratified(db, b, asof, FAR, "public") == []  # the other security's hold is its own
    assert _item(db, b).bucket == "owed"
    assert _item(db, a).bucket == "decided"

    repo.revoke(db, dec["id"], None, tenant_id=T)  # Undo
    db.commit()
    assert _counts(db)["insider_txn_hold_decision"] == after["insider_txn_hold_decision"] + 2
    assert _ratified(db, a, asof, FAR, "public") == []
    assert _ratified(db, a, asof, datetime.now(timezone.utc) + timedelta(seconds=5), "record") == []
    back = _item(db, a)
    assert (back.bucket, back.market_date, back.owed_why) == ("owed", MARKET_DAY, YEAR_RULE)


# --- 3. the public clock ---------------------------------------------------------------------------------------


def test_the_hold_counts_from_when_we_held_it_before_the_fill_and_from_its_acceptance_after(db):
    a, _ = _seed(db)
    # before: public from recorded_at (we held it 2025-09-02) — never before, and no acceptance
    assert _public_holds(db, a, _cap(MARKET_DAY)) == []
    assert _public_holds(db, a, HELD_AT - timedelta(seconds=1)) == []
    (h,) = _public_holds(db, a, HELD_AT)
    assert h["accepted"] is None

    _fill(db)
    # after: public from SEC's acceptance (17:06 New York on 2025-07-25) — not a second earlier
    assert _public_holds(db, a, ACCEPTED - timedelta(seconds=1)) == []
    assert _public_holds(db, a, _cap(date(2025, 7, 24))) == []
    (h,) = _public_holds(db, a, ACCEPTED)
    assert h["accepted"] == ACCEPTED
    (h,) = _public_holds(db, a, _cap(MARKET_DAY))
    assert h["accepted"] == ACCEPTED
    # the record clock still gates on recorded_at (bookkeeping), reading the resolved value once it can
    assert _public_holds(db, a, _cap(MARKET_DAY), clock="record") == []
    (h,) = _public_holds(db, a, HELD_AT, clock="record")
    assert h["accepted"] == ACCEPTED


def test_a_dated_row_reaches_a_view_from_its_acceptance_not_from_when_we_held_it(db):
    """End to end through the view. The steward decision is a DIRECT INPUT (``H.decide``, recorded 2025-07-28 —
    a test choice, not a real decision's time) so the held row is returned at all; the filing, its hold and the
    fill are the real ones."""
    a, _ = _seed(db)
    H.decide(
        db,
        a,
        ACC,
        SEQ,
        "approve",
        at=datetime(2025, 7, 28, 14, tzinfo=timezone.utc),
        ratified=MARKET_DAY,
    )
    row = [(MARKET_DAY, "S", 2076.0, "ratified")]
    # before the fill the hold is public from when we held it: no view before 2025-09-02 has it
    assert _ratified(db, a, date(2025, 7, 28), _cap(date(2025, 7, 28)), "public") == []
    assert _ratified(db, a, date(2025, 9, 1), _cap(date(2025, 9, 1)), "public") == []
    assert _ratified(db, a, date(2025, 9, 2), _cap(date(2025, 9, 2)), "public") == row
    _fill(db)
    # after it, from its acceptance (here the decision, made 07-28, is the later gate)
    assert _ratified(db, a, date(2025, 7, 25), _cap(date(2025, 7, 25)), "public") == []
    assert _ratified(db, a, date(2025, 7, 28), _cap(date(2025, 7, 28)), "public") == row


# --- 4. replay parity ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("clock", ["record", "public"])
@pytest.mark.parametrize("filled", [False, True], ids=["before_fill", "after_fill"])
def test_the_mirror_reads_the_resolved_acceptance_exactly_like_postgres(
    db, tmp_path, clock, filled
):
    a, b = _seed(db)
    H.decide(
        db,
        a,
        ACC,
        SEQ,
        "approve",
        at=datetime(2025, 7, 28, 14, tzinfo=timezone.utc),
        ratified=MARKET_DAY,
    )  # a DIRECT-INPUT decision (see above), so the held row is returned and compared
    if filled:
        _fill(db)
    export_snapshot(db, tmp_path, clock=clock, workspace=T)
    con = connect_mirror(tmp_path)
    mirrored = con.execute(
        "SELECT accepted FROM insider_txn_hold WHERE accession = ?", [ACC]
    ).fetchall()
    want = ACCEPTED if filled else None
    assert len(mirrored) == 2
    for (acc,) in mirrored:
        got = acc if acc is None or acc.tzinfo else acc.replace(tzinfo=timezone.utc)
        assert got == want
    checked = 0
    for asof in (
        date(2025, 7, 24),
        MARKET_DAY,
        date(2025, 7, 28),
        date(2025, 9, 2),
        date(2026, 1, 5),
    ):
        for known_at in (_cap(asof), HELD_AT, FAR):
            live = PointInTimeData(db, asof=asof, known_at=known_at, clock=clock, workspace=T)
            rep = ReplayPointInTimeData(con, asof=asof, known_at=known_at, workspace=T)
            for sid in (a, b):
                lr, rr = live.insider_txns(sid), rep.insider_txns(sid)
                if clock == "public" and not filled:
                    # the public EXPORT's own standing rule (``db.public_clock.public_time_sql``,
                    # ``serve=False``): a stored fact with no acceptance on any version is excluded from the
                    # mirror and counted, while the serve path counts it from when we held it. Not this
                    # rule's; so before the fill only the held row is compared on the public clock
                    lr = [r for r in lr if r.get("hold_id")]
                    rr = [r for r in rr if r.get("hold_id")]
                _assert_equal(lr, rr)
                checked += any(r.get("date_basis") for r in lr)
    assert checked > 0  # the held row itself was compared, not only the stored rows


# --- 6. the precedence contract --------------------------------------------------------------------------------


_SAMPLE = (
    H.HELD_DIR.parent / "form4_sample.xml"
)  # a generic sample ownership document (issuer 0001234567)


def _hold(
    db, sid: UUID, accession: str, *, form: str = "4", accepted: datetime | None = None
) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            'INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "row", '
            "accepted, recorded_at) VALUES (%s, %s, %s, 0, %s, 'year_before_1934', '{}'::jsonb, %s, %s) "
            "RETURNING id",
            (T, sid, accession, form, accepted, HELD_AT),
        )
        hid = cur.fetchone()["id"]
    db.commit()
    return hid


def _store(db, sid: UUID, accession: str, accepted: datetime | None, at: datetime) -> None:
    ingest_form4(
        db, sid, _SAMPLE.read_text(encoding="utf-8"), accession, accepted=accepted, recorded_at=at
    )
    db.commit()


def test_the_precedence_contract_on_direct_inputs(db):
    """A DIRECT-INPUT contract (no real filing is claimed): hand-made holds and a sample ownership document stored
    under made-up accessions, each case pinning one step of the precedence — own acceptance wins; else the
    NEWEST stored acceptance of the filing on the SAME security (a newer version without one does not erase
    it); else none; another security's rows never count; a Form 4/A hold keeps its own."""
    s = H.make_issuer_security(db, "PRECA", "0001234567")
    other = H.make_issuer_security(db, "PRECB", "0001234567")
    utc = timezone.utc
    # every acceptance after the sample's trade dates (2026-05-15 / 06-01), so its rows store as facts
    own, stored = datetime(2026, 7, 1, 15, tzinfo=utc), datetime(2026, 7, 1, 16, tzinfo=utc)
    old, new = datetime(2026, 7, 2, 15, tzinfo=utc), datetime(2026, 7, 2, 17, tzinfo=utc)
    r1, r2, r3 = (datetime(2026, 8, d, tzinfo=utc) for d in (1, 2, 3))

    _store(db, s, "case-own", stored, r1)
    h_own = _hold(db, s, "case-own", accepted=own)
    _store(db, s, "case-newest", old, r1)
    _store(db, s, "case-newest", new, r2)
    _store(db, s, "case-newest", None, r3)  # a newer version that carries none
    h_newest = _hold(db, s, "case-newest")
    _store(db, s, "case-none", None, r1)
    h_none = _hold(db, s, "case-none")
    _store(
        db, other, "case-other", stored, r1
    )  # the filing's acceptance stored on ANOTHER security only
    h_other = _hold(db, s, "case-other")
    _store(db, s, "case-4a", stored, r1)
    h_4a = _hold(db, s, "case-4a", form="4/A")
    want = {h_own: own, h_newest: new, h_none: None, h_other: None, h_4a: None}

    with db.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT h.id, {acc} AS acc, {pub} AS pub FROM insider_txn_hold h "
                "WHERE h.security_id = %(s)s"
            ).format(acc=hold_accepted_sql("h"), pub=aux_public_sql("insider_txn_hold", alias="h")),
            {"s": s, **public_params()},
        )
        got = {r["id"]: (r["acc"], r["pub"]) for r in cur.fetchall()}
    assert {k: v[0] for k, v in got.items()} == want
    # the public instant: the resolved acceptance, else when we held it
    assert {k: v[1] for k, v in got.items()} == {k: (v or HELD_AT) for k, v in want.items()}
    # the loaders see the same value (the read: Form 4 holds only; the queue: every hold)
    loaded = {h["id"]: h["accepted"] for h in _public_holds(db, s, FAR, clock="record")}
    assert loaded == {k: v for k, v in want.items() if k != h_4a}
    queued = {it.id: it.hold["accepted"] for it in repo.items(db, tenant_id=T, security_ids=[s])}
    assert queued == want

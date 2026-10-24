"""The company-list refresh (``securities/master_refresh.py``) — one record per company; a rename updates in place.

Every test seeds the master the way the August populate left it (each CIK's ONE row flagged primary), hands the
refresh a SEC file in which the company's ticker CHANGED, and counts the TABLES before and after (the CLAUDE.md
idempotency rule: a duplicate row hides behind a correct read, so the table is what is counted).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.price_feed_basis import BASIS_RENAME_PIN
from repositories import price_feed_repo
from securities import coherence, master, master_refresh

_CIK = "0001234567"
_OTHER = "0007654321"


def _row(db, *, cik, ticker, name="Co", exchange="Nasdaq", primary=True, price_symbol=None):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, exchange, is_primary, "
            "price_symbol, price_symbol_basis, valid_from) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                cik,
                ticker,
                name,
                exchange,
                primary,
                price_symbol,
                "operator:set" if price_symbol else None,
                date(2026, 8, 16),
            ),
        )
    db.commit()
    return sid


def _bar(db, sid, d=date(2026, 10, 1), close=10.0):
    append_fact(
        db,
        "fact_price_eod",
        dict(
            tenant_id=DEFAULT_TENANT_ID,
            security_id=sid,
            d=d,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1000,
            valid_from=d,
        ),
    )
    db.commit()


def _form4(db, sid):
    append_fact(
        db,
        "fact_insider_txn",
        dict(
            tenant_id=DEFAULT_TENANT_ID,
            security_id=sid,
            insider_name="Jane Doe",
            insider_role="director",
            txn_code="P",
            shares=100,
            price=10,
            usd=1000,
            accession="0000000000-26-000001",
            valid_from=date(2026, 9, 1),
        ),
    )
    db.commit()


def _thesis(db, members):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "t", "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _count(db, table, where="TRUE", args=()):
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE {where}", args
        )  # noqa: S608 — test SQL
        return cur.fetchone()["n"]


def _master(db, cik=None):
    db.rollback()
    with db.cursor() as cur:
        if cik is None:
            cur.execute("SELECT * FROM security_master ORDER BY cik, ticker")
        else:
            cur.execute("SELECT * FROM security_master WHERE cik = %s ORDER BY ticker", (cik,))
        return cur.fetchall()


def _refresh(db, rows):
    plan = master_refresh.plan_refresh(db, rows)
    written = master_refresh.apply_plan(db, plan)
    db.commit()
    return plan, written


def _tables(db):
    return {
        t: _count(db, t)
        for t in (
            "security_master",
            "security_ticker_history",
            "price_symbol_change",
            "fact_price_eod",
            "fact_insider_txn",
        )
    }


# --- 1. a rename updates the row in place ---------------------------------------------------------------------


def test_a_rename_updates_in_place_and_every_fact_stays_attached(db):
    sid = _row(db, cik=_CIK, ticker="SVAC", name="Starboard Value Acquisition")
    _bar(db, sid)
    _form4(db, sid)
    before = _tables(db)

    plan, written = _refresh(db, [(_CIK, "GFUZ", "General Fusion Inc.", "Nasdaq")])

    assert [(r.old_ticker, r.new_ticker) for r in plan.renames] == [("SVAC", "GFUZ")]
    rows = _master(db, _CIK)
    assert [(r["id"], r["ticker"], r["is_primary"]) for r in rows] == [(sid, "GFUZ", True)]
    assert rows[0]["name"] == "General Fusion Inc."
    after = _tables(db)
    assert after["security_master"] == before["security_master"]  # NOT a second row
    assert after["fact_price_eod"] == before["fact_price_eod"] == 1  # the facts, untouched...
    assert after["fact_insider_txn"] == before["fact_insider_txn"] == 1
    assert (
        _count(db, "fact_price_eod", "security_id = %s", (sid,)) == 1
    )  # ...and still on the SAME id
    assert _count(db, "fact_insider_txn", "security_id = %s", (sid,)) == 1
    with db.cursor() as cur:
        cur.execute(
            "SELECT security_id, old_ticker, new_ticker, source FROM security_ticker_history"
        )
        hist = cur.fetchall()
    assert [(h["security_id"], h["old_ticker"], h["new_ticker"]) for h in hist] == [
        (sid, "SVAC", "GFUZ")
    ]
    assert hist[0]["source"] == master_refresh.SOURCE
    assert written["renamed"] == 1 and written["inserted"] == 0


def test_canonicalize_after_a_rename_re_points_nothing(db):
    """The bug this replaces: the old keying inserted GFUZ as a SECOND primary and the next Save re-pointed the
    member to that empty row. In place, the member's id IS the canonical one."""
    sid = _row(db, cik=_CIK, ticker="SVAC")
    _refresh(db, [(_CIK, "GFUZ", "General Fusion Inc.", "Nasdaq")])
    assert master.canonicalize_ids(db, [sid]) == {}
    assert master.ids_for_ciks(db, [_CIK]) == {_CIK: sid}


def test_the_history_is_append_only(db):
    sid = _row(db, cik=_CIK, ticker="SVAC")
    _refresh(db, [(_CIK, "GFUZ", "General Fusion Inc.", "Nasdaq")])
    with pytest.raises(Exception, match="append-only"):
        with db.cursor() as cur:
            cur.execute(
                "UPDATE security_ticker_history SET new_ticker = 'X' WHERE security_id = %s", (sid,)
            )
    db.rollback()


# --- 2. primary flags: at most one per CIK ---------------------------------------------------------------------


def test_at_most_one_primary_per_cik_and_a_vanished_row_is_demoted(db):
    """A CIK whose old primary SEC no longer lists while the file's primary is a row we ALREADY hold with its own
    history (the remaining class, priced): primacy moves to that row and the vanished one is DEMOTED — the old
    "keeps its last flag" is how two primaries arose."""
    old = _row(db, cik=_CIK, ticker="ABC", primary=True)
    kept = _row(db, cik=_CIK, ticker="ABCB", primary=False)
    _bar(db, kept)
    plan, _ = _refresh(db, [(_CIK, "ABCB", "Co", "Nasdaq")])
    assert plan.renames == []  # the file's primary is an existing row, so this is not a rename
    flags = {r["id"]: r["is_primary"] for r in _master(db, _CIK)}
    assert flags == {old: False, kept: True}
    assert master_refresh.duplicate_primaries(db) == []


def test_two_stored_primaries_are_repaired_to_one(db):
    a = _row(db, cik=_CIK, ticker="AAA", primary=True)
    b = _row(db, cik=_CIK, ticker="AAAW", primary=True)
    assert master_refresh.duplicate_primaries(db) == [_CIK]
    _refresh(db, [(_CIK, "AAA", "Co", "Nasdaq"), (_CIK, "AAAW", "Co", "Nasdaq")])
    assert {r["id"]: r["is_primary"] for r in _master(db, _CIK)} == {a: True, b: False}
    assert master_refresh.duplicate_primaries(db) == []


def test_a_new_class_is_inserted_non_primary(db):
    sid = _row(db, cik=_CIK, ticker="ABC")
    plan, _ = _refresh(db, [(_CIK, "ABC", "Co", "Nasdaq"), (_CIK, "ABCW", "Co", "Nasdaq")])
    rows = {r["ticker"]: r for r in _master(db, _CIK)}
    assert rows["ABC"]["id"] == sid and rows["ABC"]["is_primary"] is True
    assert rows["ABCW"]["is_primary"] is False
    assert [i.kind for i in plan.inserts] == [master_refresh.KIND_NEW_CLASS]


def test_several_old_and_several_new_tickers(db):
    """The SPAC-close shape: common + warrant both re-ticker. The company's (primary) row is renamed in place;
    the old warrant row stays as it is; the new warrant is inserted NON-primary — it never takes primacy.
    """
    common = _row(db, cik=_CIK, ticker="SPAC", primary=True)
    warrant = _row(db, cik=_CIK, ticker="SPACW", primary=False)
    plan, _ = _refresh(
        db, [(_CIK, "NEWCO", "NewCo", "Nasdaq"), (_CIK, "NEWCOW", "NewCo", "Nasdaq")]
    )
    rows = {r["ticker"]: r for r in _master(db, _CIK)}
    assert set(rows) == {"NEWCO", "SPACW", "NEWCOW"}
    assert rows["NEWCO"]["id"] == common and rows["NEWCO"]["is_primary"] is True
    assert rows["SPACW"]["id"] == warrant and rows["SPACW"]["is_primary"] is False
    assert rows["NEWCOW"]["is_primary"] is False
    assert [(r.old_ticker, r.new_ticker) for r in plan.renames] == [("SPAC", "NEWCO")]
    assert master_refresh.duplicate_primaries(db) == []


def test_a_rename_over_an_EMPTY_placeholder_row_of_the_same_company_is_still_in_place(db):
    """The VREOD shape (MEASURED on dev 2026-10-24): an earlier load inserted VREOF as a separate, EMPTY row beside
    the company's VREOD row (the one with the tape and the basket member). SEC now lists only VREOF. Moving
    primacy to the empty row would re-point the member at its next Save and strand every fact — so the company's
    row is renamed in place; the placeholder stays as it is, non-primary (never deleted)."""
    company = _row(db, cik=_CIK, ticker="VREOD", primary=True, price_symbol="VREOF")
    _bar(db, company)
    _thesis(db, [("VREOD", company)])
    placeholder = _row(db, cik=_CIK, ticker="VREOF", primary=False)
    plan, _ = _refresh(db, [(_CIK, "VREOF", "Vireo", "OTC")])
    assert [(r.id, r.old_ticker, r.new_ticker) for r in plan.renames] == [
        (company, "VREOD", "VREOF")
    ]
    assert plan.placeholders == {company: (placeholder,)} and plan.basket_repoints == []
    rows = {r["id"]: r for r in _master(db, _CIK)}
    assert (rows[company]["ticker"], rows[company]["is_primary"]) == ("VREOF", True)
    assert (rows[placeholder]["ticker"], rows[placeholder]["is_primary"]) == ("VREOF", False)
    assert master.canonicalize_ids(db, [company]) == {}
    assert rows[company]["price_symbol"] == "VREOF"  # a manual symbol is never touched
    _refresh(db, [(_CIK, "VREOF", "Vireo", "OTC")])  # idempotent over the duplicate pair
    assert _count(db, "security_ticker_history") == 1


def test_a_sibling_WITH_its_own_history_takes_primacy_and_the_move_is_listed(db):
    """GOOGL delisted, GOOG (a tracked line with its own tape) remains: not a rename — primacy moves to the real
    line, and a basket member on the old row is LISTED as re-pointing (never silent)."""
    a = _row(db, cik=_CIK, ticker="GOOGL", primary=True)
    c = _row(db, cik=_CIK, ticker="GOOG", primary=False)
    _bar(db, c)
    _thesis(db, [("GOOGL", a)])
    plan, _ = _refresh(db, [(_CIK, "GOOG", "Alphabet", "Nasdaq")])
    assert plan.renames == []
    assert {r["id"]: r["is_primary"] for r in _master(db, _CIK)} == {a: False, c: True}
    assert [(p["from_ticker"], p["to_ticker"]) for p in plan.basket_repoints] == [("GOOGL", "GOOG")]
    assert "will RE-POINT" in "\n".join(plan.report_lines())


def test_primacy_is_sticky_when_SEC_prefers_another_line_only_by_file_order(db):
    """The LRHC / SRTM shape (MEASURED on dev 2026-10-24): SEC lists the company's old ticker AND a new one side
    by side on the same venue, the new one first. Nothing structural prefers it, so the company's row keeps
    primacy (no member re-point onto an empty row); the new ticker is inserted non-primary — and the week the old
    one drops off the file, the company's row is renamed in place over that empty placeholder."""
    company = _row(db, cik=_CIK, ticker="LRHC", primary=True)
    _bar(db, company)
    _thesis(db, [("LRHC", company)])
    plan, _ = _refresh(
        db, [(_CIK, "SRTM", "La Rosa", "Nasdaq"), (_CIK, "LRHC", "La Rosa", "Nasdaq")]
    )
    assert plan.basket_repoints == [] and plan.renames == []
    flags = {r["ticker"]: (r["id"], r["is_primary"]) for r in _master(db, _CIK)}
    assert flags["LRHC"] == (company, True) and flags["SRTM"][1] is False
    plan, _ = _refresh(db, [(_CIK, "SRTM", "La Rosa", "Nasdaq")])  # the old ticker is gone
    assert [(r.id, r.old_ticker, r.new_ticker) for r in plan.renames] == [(company, "LRHC", "SRTM")]
    assert master.ids_for_ciks(db, [_CIK]) == {_CIK: company}


def test_a_structurally_better_line_still_takes_primacy(db):
    """A pre-separation SPAC held only as its UNIT: once SEC lists the common, the common is the instrument."""
    unit = _row(db, cik=_CIK, ticker="ABCDU", primary=True)
    plan, _ = _refresh(db, [(_CIK, "ABCDU", "Co", "Nasdaq"), (_CIK, "ABCD", "Co", "Nasdaq")])
    flags = {r["ticker"]: r["is_primary"] for r in _master(db, _CIK)}
    assert flags == {"ABCDU": False, "ABCD": True}
    assert [i.kind for i in plan.inserts] == [master_refresh.KIND_NEW_PRIMARY]
    assert unit not in {r.id for r in plan.renames}


def test_an_unflagged_company_renames_the_row_its_history_is_on(db):
    """The PRTC shape (seeded rows, no flags): the row with the tape / basket member is the company's row."""
    _row(db, cik=_CIK, ticker="PTCHF", primary=None)
    adr = _row(db, cik=_CIK, ticker="PRTC", primary=None, price_symbol="PRTCY")
    _bar(db, adr)
    _row(db, cik=_CIK, ticker="PRTCY", primary=None)
    plan, _ = _refresh(db, [(_CIK, "PRTCY", "PureTech", "OTC"), (_CIK, "PTCHF", "PureTech", "OTC")])
    assert [(r.id, r.old_ticker) for r in plan.renames] == [(adr, "PRTC")]
    assert master.ids_for_ciks(db, [_CIK]) == {_CIK: adr}
    assert master_refresh.duplicate_primaries(db) == []


# --- 3. conflicts and delistings -------------------------------------------------------------------------------


def test_a_ticker_reuse_conflict_is_surfaced_not_applied(db):
    """SEC lists our company under a ticker ANOTHER company's master row still holds (reuse after a delisting):
    never re-keyed — reported as a conflict, the row keeps its old ticker and flag."""
    ours = _row(db, cik=_CIK, ticker="OLD")
    theirs = _row(db, cik=_OTHER, ticker="NEW", name="Dead Co")
    before = _tables(db)
    plan, written = _refresh(db, [(_CIK, "NEW", "Our Co", "Nasdaq")])
    assert plan.renames == [] and written["renamed"] == 0
    [c] = plan.conflicts
    assert (c.kind, c.ticker, c.old_ticker, c.security_id) == (
        master_refresh.KIND_RENAME,
        "NEW",
        "OLD",
        ours,
    )
    assert [h.security_id for h in c.holders] == [theirs]
    assert {r["ticker"]: r["id"] for r in _master(db)} == {"OLD": ours, "NEW": theirs}
    assert _tables(db) == before
    assert plan.counts()["conflicts"] == 1 and "1 conflicts" in plan.admin_line(date(2026, 10, 25))


def test_a_new_company_on_a_live_basket_members_ticker_is_a_conflict(db):
    """A NEW CIK whose ticker a live-basket row of another CIK holds may be that member's SUCCESSOR issuer
    (T7): inserting it would block the operator's Adopt, so it is a conflict, not an insert."""
    member = _row(db, cik=_OTHER, ticker="UROY")
    _thesis(db, [("UROY", member)])
    plan, _ = _refresh(db, [(_CIK, "UROY", "Successor Co", "Nasdaq")])
    assert plan.inserts == []
    assert [(c.kind, c.ticker, c.in_basket) for c in plan.conflicts] == [
        (master_refresh.KIND_NEW_COMPANY, "UROY", True)
    ]
    assert _count(db, "security_master") == 1


def test_a_new_company_sharing_a_non_basket_ticker_is_inserted_and_listed(db):
    _row(db, cik=_OTHER, ticker="DEAD")
    plan, _ = _refresh(db, [(_CIK, "DEAD", "New Co", "Nasdaq")])
    assert plan.conflicts == []
    assert [i.ticker for i, _ in plan.shared] == ["DEAD"]
    assert (
        _count(db, "security_master") == 2
    )  # recall (#9): a new company is never silently left out


def test_a_delisted_cik_is_left_untouched(db):
    gone = _row(db, cik=_OTHER, ticker="GONE", primary=True)
    snapshot = _master(db, _OTHER)
    _refresh(db, [(_CIK, "LIVE", "Live Co", "Nasdaq")])
    assert _master(db, _OTHER) == snapshot  # same row, same ticker, same flag, same recorded_at
    assert snapshot[0]["id"] == gone


# --- 4. the price feed follows a rename only when safe ---------------------------------------------------------


def test_a_renamed_row_with_stored_bars_is_PINNED_to_its_old_ticker(db):
    sid = _row(db, cik=_CIK, ticker="PSKY")
    _bar(db, sid)
    plan, written = _refresh(db, [(_CIK, "SKYD", "Skydance", "Nasdaq")])
    [r] = plan.renames
    assert r.pin == "PSKY" and r.price_action == "pin" and written["pinned"] == 1
    row = _master(db, _CIK)[0]
    assert (row["ticker"], row["price_symbol"], row["price_symbol_basis"]) == (
        "SKYD",
        "PSKY",
        BASIS_RENAME_PIN,
    )
    # the same record the Admin Set writes — visible, and its Undo restores "priced under the ticker"
    change = price_feed_repo.standing_change_for(db, sid)
    assert change is not None and change.action == "set" and change.basis == BASIS_RENAME_PIN
    assert (change.prior_symbol, change.new_symbol) == (None, "PSKY")
    price_feed_repo.undo_change(db, change.id, basis="operator:undo")
    db.commit()
    assert _master(db, _CIK)[0]["price_symbol"] is None


def test_a_renamed_row_without_bars_is_not_pinned(db):
    sid = _row(db, cik=_CIK, ticker="PWCM_OLD")
    plan, written = _refresh(db, [(_CIK, "PWCM", "Co", "Nasdaq")])
    assert plan.renames[0].price_action == "follows" and written["pinned"] == 0
    row = _master(db, _CIK)[0]
    assert (row["id"], row["ticker"], row["price_symbol"]) == (sid, "PWCM", None)
    assert _count(db, "price_symbol_change") == 0


def test_a_manual_price_symbol_is_never_touched(db):
    sid = _row(db, cik=_CIK, ticker="GGRP", price_symbol="BTLN")
    _bar(db, sid)
    plan, written = _refresh(db, [(_CIK, "BTLN", "Co", "Nasdaq")])
    assert plan.renames[0].price_action == "kept" and written["pinned"] == 0
    row = _master(db, _CIK)[0]
    assert (row["ticker"], row["price_symbol"], row["price_symbol_basis"]) == (
        "BTLN",
        "BTLN",
        "operator:set",
    )
    assert _count(db, "price_symbol_change") == 0


# --- 5. idempotency, the coherence guard, the search ------------------------------------------------------------


def test_a_second_apply_changes_nothing(db):
    sid = _row(db, cik=_CIK, ticker="PSKY")
    _bar(db, sid)
    _row(db, cik=_OTHER, ticker="KEEP")
    rows = [
        (_CIK, "SKYD", "Skydance", "Nasdaq"),
        (_CIK, "SKYDW", "Skydance", "Nasdaq"),
        ("0001111111", "NEWC", "New Co", "NYSE"),
        (_OTHER, "KEEP", "Co", "Nasdaq"),
    ]
    _refresh(db, rows)
    first = _tables(db)
    flags = {r["id"]: (r["ticker"], r["is_primary"], r["price_symbol"]) for r in _master(db)}
    plan, written = _refresh(db, rows)
    assert plan.empty and plan.conflicts == []
    assert written == {"inserted": 0, "updated": 0, "renamed": 0, "pinned": 0}
    assert _tables(db) == first
    assert {
        r["id"]: (r["ticker"], r["is_primary"], r["price_symbol"]) for r in _master(db)
    } == flags


def test_the_promote_guard_reads_a_former_ticker_as_RENAMED_not_drift(db):
    """A basket stored before the rename still labels the member SVAC; the guard must ALIGN it (the same row),
    never 422 it as label drift."""
    sid = _row(db, cik=_CIK, ticker="SVAC")
    _refresh(db, [(_CIK, "GFUZ", "General Fusion Inc.", "Nasdaq")])
    [f] = coherence.classify_members(db, [("SVAC", sid)])
    assert f.kind is coherence.CoherenceKind.RENAMED and f.bound_ticker == "GFUZ"


def test_the_company_search_matches_a_former_ticker(db):
    sid = _row(db, cik=_CIK, ticker="SVAC", name="General Fusion Inc.")
    _refresh(db, [(_CIK, "GFUZ", "General Fusion Inc.", "Nasdaq")])
    assert [s.id for s in master.search(db, "SVAC", include_former=True)] == [sid]
    assert master.search(db, "SVAC") == []  # the default net is unchanged (the chain drafter's)


def test_a_stale_plan_refuses_to_apply(db):
    """The master moved between the plan and the apply: nothing is written."""
    sid = _row(db, cik=_CIK, ticker="OLD")
    plan = master_refresh.plan_refresh(db, [(_CIK, "NEW", "Co", "Nasdaq")])
    db.rollback()
    with db.cursor() as cur:
        cur.execute("UPDATE security_master SET ticker = 'MOVED' WHERE id = %s", (sid,))
    db.commit()
    with pytest.raises(master_refresh.RefreshConflict):
        master_refresh.apply_plan(db, plan)
    db.rollback()
    assert _count(db, "security_ticker_history") == 0


def test_the_plan_reports_in_plain_english(db):
    sid = _row(db, cik=_CIK, ticker="PSKY")
    _bar(db, sid)
    _thesis(db, [("PSKY", sid)])
    plan = master_refresh.plan_refresh(
        db, [(_CIK, "SKYD", "Skydance", "Nasdaq"), ("0001111111", "NEWC", "New", "NYSE")]
    )
    text = "\n".join(plan.report_lines())
    assert "1 new companies" in text and "1 renames (1 in a live basket)" in text
    assert "[basket] PSKY -> SKYD" in text and "PINNED to PSKY" in text
    assert plan.admin_line(date(2026, 10, 25)) == (
        "Company list · refreshed Oct 25 · 1 new · 1 renames · 0 conflicts"
    )
    assert _count(db, "security_ticker_history") == 0  # a plan is a look: nothing applied

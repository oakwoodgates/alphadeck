"""Step 2 of ``domain.insider_holds.resolve_holds``: a held row leaves the read when its filing is WITHDRAWN (a
void, applied or still pending) or a DUPLICATE copy, or when its row is REPLACED (``suppress_rows``) — exactly the
suppression the filing's stored rows get (``domain.insider_amendments.suppression_of``).

DIRECT-INPUT CONTRACT TESTS, not real filings: measured on the dev copy of prod (2026-11-12), no hold sits in a
voided or duplicate filing today, so there is no real instance to commit. The inputs below are the shapes the
engines hand the resolver (a hold row, a link row, a decision row), built by hand, and every case runs through
both the pure function and the shared seam both engines call (``ResolutionInputs.resolve``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from domain.insider_amendments import ResolutionInputs, resolve_insider_rows
from domain.insider_holds import resolve_holds

SID = str(uuid.uuid4())
TENANT = str(uuid.uuid4())
EARLIER = "0000000001-24-000001"
LATER = "0000000001-24-000002"
AMEND = "0000000001-24-000009"
ACCEPTED = datetime(2024, 5, 28, 23, 30, tzinfo=timezone.utc)  # 19:30 New York, 2024-05-28
NIGHT = date(2024, 5, 28)
ASOF = date(2024, 6, 30)
T0 = datetime(2024, 5, 29, tzinfo=timezone.utc)


def _row(code: str = "P", shares: float = 100.0, price: float = 10.0) -> dict:
    return {
        "insider_name": "Doe Jane",
        "rpt_owner_cik": "0000000777",
        "insider_role": "Director",
        "txn_code": code,
        "shares": shares,
        "price": price,
        "usd": shares * price,
        "txn_date": "2028-05-24",
        "txn_date_as_filed": "2028-05-24",
    }


def _hold(accession: str, seq: int = 0, **row) -> dict:
    """An ON-TIME held row (period of report 4 days before the acceptance): placed at NIGHT unless suppressed."""
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": TENANT,
        "security_id": SID,
        "accession": accession,
        "txn_seq": seq,
        "form": "4",
        "reason": "year_after_filing_year",
        "row": _row(**row),
        "period_of_report_as_filed": "2024-05-24",
        "signature_date_as_filed": "2024-05-28",
        "filed": date(2024, 5, 28),
        "accepted": ACCEPTED,
        "recorded_at": T0,
    }


def _stored(accession: str, seq: int) -> dict:
    """A stored fact row of the same filing (another row number), the shape the as-of read returns."""
    return {
        "id": str(uuid.uuid4()),
        "accession": accession,
        "txn_seq": seq,
        "insider_name": "Doe Jane",
        "txn_code": "P",
        "shares": 50.0,
        "price": 10.0,
        "valid_from": NIGHT,
        "issuer_cik": None,
    }


def _link(disposition: str, originals=(), voided=()) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "security_id": SID,
        "amendment_accession": AMEND,
        "original_accessions": list(originals),
        "voided_accessions": list(voided),
        "candidate_accessions": [],
        "method": "orig_date_owner",
        "disposition": disposition,
        "status": "pending",
        "accepted": T0,
        "recorded_at": T0,
    }


def _verdict(action: str, replaced_rows=None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "security_id": SID,
        "amendment_accession": AMEND,
        "action": action,
        "disposition": None,
        "original_accessions": [],
        "voided_accessions": [],
        "replaced_rows": replaced_rows,
        "revokes": None,
        "basis": "system:filer-correction",
        "recorded_at": T0,
    }


def _both(rows, holds, links, decisions, amendments=()) -> tuple[list[dict], list[dict]]:
    """The pure step over the 4/A-resolved rows, and the shared seam both engines call (4/A resolution, holds,
    then the joint-report collapse and the transfer screen)."""
    resolved = resolve_insider_rows(
        rows, amendments=amendments, links=links, decisions=decisions, asof=ASOF
    )
    pure = resolve_holds(
        resolved,
        holds=holds,
        hold_decisions=[],
        amendments=amendments,
        links=links,
        decisions=decisions,
        asof=ASOF,
        on_time_days=5,
    )
    inputs = ResolutionInputs(
        amendments={SID: list(amendments)},
        links={SID: list(links)},
        decisions={SID: list(decisions)},
        holds={SID: list(holds)},
    )
    seam = inputs.resolve(SID, rows, asof=ASOF, lower=None)
    return pure, seam


def _held(rows) -> list[tuple[str, int]]:
    return sorted((r["accession"], r["txn_seq"]) for r in rows if r.get("hold_id"))


def _stored_keys(rows) -> list[tuple[str, int]]:
    return sorted((r["accession"], r["txn_seq"]) for r in rows if not r.get("hold_id"))


def test_with_nothing_visible_the_on_time_held_row_is_placed():
    pure, seam = _both([_stored(EARLIER, 1)], [_hold(EARLIER)], [], [])
    for out in (pure, seam):
        assert _held(out) == [(EARLIER, 0)]
        assert [r["valid_from"] for r in out if r.get("hold_id")] == [NIGHT]


def test_a_pending_void_of_the_filing_drops_its_held_row_as_it_drops_its_stored_row():
    """A void suppresses its original while pending (the cancellation holds) and emits nothing."""
    links = [_link("void", originals=[EARLIER])]
    pure, seam = _both([_stored(EARLIER, 1)], [_hold(EARLIER)], links, [])
    for out in (pure, seam):
        assert _held(out) == [] and _stored_keys(out) == []


def test_an_applied_void_drops_the_held_row():
    links = [_link("void", originals=[EARLIER])]
    pure, seam = _both([_stored(EARLIER, 1)], [_hold(EARLIER)], links, [_verdict("apply")])
    for out in (pure, seam):
        assert _held(out) == [] and _stored_keys(out) == []


def test_a_kept_void_leaves_the_held_row_placed():
    """The filer's words did not retract it (a ``keep``): nothing is suppressed, the row stands."""
    links = [_link("void", originals=[EARLIER])]
    pure, seam = _both([_stored(EARLIER, 1)], [_hold(EARLIER)], links, [_verdict("keep")])
    for out in (pure, seam):
        assert _held(out) == [(EARLIER, 0)] and _stored_keys(out) == [(EARLIER, 1)]


def test_a_duplicates_set_counts_the_bad_date_trade_once():
    """Two copies of one filing, each with the same held row: the later copy is suppressed, the earliest stands —
    exactly one held row is returned."""
    links = [_link("duplicates", originals=[EARLIER], voided=[LATER])]
    holds = [_hold(EARLIER), _hold(LATER)]
    rows = [_stored(EARLIER, 1), _stored(LATER, 1)]
    pure, seam = _both(rows, holds, links, [])
    for out in (pure, seam):
        assert _held(out) == [(EARLIER, 0)]
        assert _stored_keys(out) == [(EARLIER, 1)]


def test_a_replaced_row_key_drops_that_held_row_only():
    """An applied matched amendment that replaces ``EARLIER#1`` (``suppress_rows``) and emits for the filing: the
    held row 1 is replaced and leaves; the held row 0, which it neither replaces nor restates, stays (step 3
    keeps an unmatched held row under an emitting amendment)."""
    links = [_link("partial", originals=[EARLIER])]
    decisions = [_verdict("apply", replaced_rows=[f"{EARLIER}#1"])]
    holds = [_hold(EARLIER, 0), _hold(EARLIER, 1, shares=200.0)]
    pure, seam = _both([], holds, links, decisions)
    for out in (pure, seam):
        assert _held(out) == [(EARLIER, 0)]


def test_a_correction_emitting_for_the_filing_without_the_trade_keeps_the_held_row():
    """The deliberate gap (step 3): the linker does not see held rows yet, so an emitting whole correction that
    carries no matching row does not drop the held row — dropping it could lose a real trade."""
    links = [_link("correction", originals=[EARLIER])]
    amendment = {
        "id": str(uuid.uuid4()),
        "tenant_id": TENANT,
        "security_id": SID,
        "accession": AMEND,
        "rows": [{**_row(shares=50.0), "txn_seq": 0, "txn_date": "2024-05-24"}],
        "accepted": T0,
        "filed": date(2024, 5, 29),
        "remarks": None,
        "footnotes": [],
        "recorded_at": T0,
    }
    pure, seam = _both([_stored(EARLIER, 1)], [_hold(EARLIER)], links, [], [amendment])
    for out in (pure, seam):
        assert _held(out) == [(EARLIER, 0)]
        assert [r["accession"] for r in out if not r.get("hold_id")] == [AMEND]

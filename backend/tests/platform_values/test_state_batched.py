"""V2 W4a: the BATCHED state read (``platform_values.state.read_states``) answers exactly what the per-company read
answers, for a mixed set of companies read together.

The steward's queue reads the state of every company with an open flag; one read per company would be about four
queries each (risk R1 of the W4 plan). ``read_states`` reads a whole set in a fixed number of queries and derives
each state with the same ``derive_state``. This test pins that the batch changes nothing: over companies in every
state of the derivation table (``docs/PLATFORM_VALUES.md``), plus the edge cases (a workspace row, a value dated
after today, an undone approve, an undone dismiss, a reopened OLDER candidate, a pin then an unpin), the batch equals

- ``read_state`` for each company, and
- an independent reference built here from the per-company repository reads (the W2a body of ``read_state``),

for both value types. It also pins that the batch issues the same number of statements for 2 companies as for 12.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from itertools import count
from uuid import UUID, uuid4

import psycopg
import pytest

from db.bitemporal import append_fact
from domain.platform_values import ValueState, derive_state, pick_current
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import ingest_shares_outstanding
from platform_values import state
from repositories import platform_value_repo as repo
from tests.workspaces import new_workspace

STEWARD = "steward@example.test"
REF1 = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
REF2 = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
REF3 = "https://www.sec.gov/Archives/edgar/data/7/0003-q3.htm"
D0, D1, D2 = date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)
ASOF = date(2026, 8, 3)
T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
FACT_TYPES = ("shares_outstanding", "cash_burn")
# a store key is (tenant, filing, recorded_at): every write here gets its own second
_TICK = count()


def _company(db, ticker: str) -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", "0000000007", date(2026, 1, 1)),
        )
    return sid


def _value(db, sid: UUID, fact_type: str, ref: str, d: date, by: str | None = "auto", at: int = 0):
    common = dict(
        source_ref=ref,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
        recorded_at=T0 + timedelta(hours=at, seconds=next(_TICK)),
    )
    if fact_type == "shares_outstanding":
        return ingest_shares_outstanding(db, sid, shares=10_000_000, source="cover", **common)
    return ingest_cash_burn(
        db, sid, cash_usd=5_000_000, quarterly_burn_usd=1_000_000, source="10-Q", **common
    )


def _flag(db, sid: UUID, fact_type: str, d: date, ref: str = REF2) -> UUID:
    values = (
        {"shares": 30_000_000}
        if fact_type == "shares_outstanding"
        else {"cash_usd": 9_000_000, "quarterly_burn_usd": 1_500_000}
    )
    return repo.append_candidate(
        db,
        security_id=sid,
        fact_type=fact_type,
        outcome="flag",
        flags=["dual-class"],
        source="cover",
        source_ref=ref,
        event_date=d,
        located_passages=[{"text": "Class A common stock", "url": ref}],
        **values,
    )


def _decide(db, sid: UUID, fact_type: str, action: str, **kw) -> UUID:
    return repo.append_decision(
        db, security_id=sid, fact_type=fact_type, action=action, decided_by=STEWARD, **kw
    )


def _build(db, fact_type: str) -> dict[str, UUID]:
    """One company per case, for ``fact_type``. Each commit is a later transaction (``now()`` is per transaction),
    so a later candidate or decision is recorded after an earlier one."""
    ft = fact_type
    ids: dict[str, UUID] = {}

    def co(label: str) -> UUID:
        ids[label] = _company(db, label[:8].upper())
        return ids[label]

    co("none")
    _value(db, co("machine"), ft, REF1, D1)
    _value(db, co("v1null"), ft, REF1, D1, by=None)
    _value(db, co("carried"), ft, REF1, D1, by="operator")
    _value(db, co("steward"), ft, REF1, D1, by=STEWARD)
    _flag(db, co("awaiting"), ft, D2)
    sid = co("pending")
    _value(db, sid, ft, REF1, D1)
    _flag(db, sid, ft, D2)
    sid = co("stale")  # a flag older than the value shown: not pending
    _value(db, sid, ft, REF2, D2)
    _flag(db, sid, ft, D1, ref=REF1)
    repo.append_candidate(
        db, security_id=co("nothing"), fact_type=ft, outcome="nothing", reason="no-annual-filing"
    )
    sid = co("dismissed")
    _decide(db, sid, ft, "dismiss", candidate_id=_flag(db, sid, ft, D2), reason="no")
    sid = co("pinned")
    _value(db, sid, ft, REF1, D1)
    repo.append_pin(db, security_id=sid, fact_type=ft, action="pin", decided_by=STEWARD, note="x")
    sid = co("tie")  # two filings on one date: the newer write wins
    _value(db, sid, ft, REF2, D1, at=0)
    _value(db, sid, ft, REF1, D1, at=5)
    sid = co("future")  # a value dated after today is never picked
    _value(db, sid, ft, REF1, D1)
    _value(db, sid, ft, REF3, ASOF + timedelta(days=30))
    sid = co("workspace")  # a row under a workspace is never read
    append_fact(
        db,
        "fact_shares_outstanding" if ft == "shares_outstanding" else "fact_cash_burn",
        {
            "tenant_id": new_workspace(db, f"stray {ft}"),
            "security_id": sid,
            "source": "cover",
            "source_ref": REF1,
            "valid_from": D1,
            "ratified_by": "operator",
            **(
                {"shares": 99}
                if ft == "shares_outstanding"
                else {"cash_usd": 99, "quarterly_burn_usd": 1}
            ),
        },
    )
    db.commit()

    # an approve, then its undo: the value falls back and the candidate is pending again
    sid = co("undone_approve")
    _value(db, sid, ft, REF1, D1)
    cid = _flag(db, sid, ft, D2)
    fid = _value(db, sid, ft, REF2, D2, by=STEWARD, at=24)
    approve = _decide(db, sid, ft, "approve", candidate_id=cid, fact_id=fid)
    # a dismiss, then its undo: awaiting again
    sid_d = co("undone_dismiss")
    dismiss = _decide(db, sid_d, ft, "dismiss", candidate_id=_flag(db, sid_d, ft, D2), reason="x")
    # a reopened OLDER candidate: c1 dismissed, c2 (newer) dismissed, then c1's dismiss undone. The state reads
    # only the newest candidate (c2, dismissed), so c1 is open but waits nowhere (W4 plan correction 7).
    sid_r = co("reopened_older")
    old_dismiss = _decide(
        db, sid_r, ft, "dismiss", candidate_id=_flag(db, sid_r, ft, D1, ref=REF1), reason="x"
    )
    # pinned, then unpinned
    sid_u = co("unpinned")
    repo.append_pin(db, security_id=sid_u, fact_type=ft, action="pin", decided_by=STEWARD)
    db.commit()
    _decide(db, sid, ft, "undo", revokes=approve)
    _decide(db, sid_d, ft, "undo", revokes=dismiss)
    _decide(db, sid_r, ft, "dismiss", candidate_id=_flag(db, sid_r, ft, D2), reason="y")
    repo.append_pin(db, security_id=sid_u, fact_type=ft, action="unpin", decided_by=STEWARD)
    db.commit()
    _decide(db, sid_r, ft, "undo", revokes=old_dismiss)
    db.commit()
    return ids


def _reference(db, sid: UUID, fact_type: str):
    """The W2a body of ``read_state``, from the PER-COMPANY repository reads: the independent oracle."""
    picked = pick_current(state.winners(db, sid, fact_type, asof=ASOF), not_after=ASOF)
    cand = repo.latest_candidate(db, sid, fact_type)
    standing = repo.standing_decision_for_candidate(db, cand["id"]) if cand else None
    return derive_state(
        picked=picked,
        candidate=cand,
        candidate_decision=standing["action"] if standing else None,
        pinned=repo.is_pinned(db, sid, fact_type),
    )


@pytest.mark.parametrize("fact_type", FACT_TYPES)
def test_the_batch_equals_the_per_company_read_in_every_state(db, fact_type):
    ids = _build(db, fact_type)
    batch = state.read_states(db, list(ids.values()), fact_type, asof=ASOF)
    assert set(batch) == set(ids.values())
    for label, sid in ids.items():
        one = state.read_state(db, sid, fact_type, asof=ASOF)
        assert batch[sid] == one, label
        assert batch[sid] == _reference(db, sid, fact_type), label
    # the set really is mixed: every state, and the edge cases read as the rules say
    by = {label: batch[sid] for label, sid in ids.items()}
    assert {s.state for s in batch.values()} == set(ValueState)
    assert (by["none"].state, by["none"].reason) == (ValueState.NO_VALUE, "not-extracted")
    assert (by["workspace"].state, by["workspace"].reason) == (ValueState.NO_VALUE, "not-extracted")
    assert by["nothing"].reason == "no-annual-filing"
    assert by["dismissed"].reason == "dismissed"
    assert by["pending"].pending is not None and by["stale"].pending is None
    assert by["carried"].ratifier == "carried" and by["steward"].ratifier == "steward"
    assert by["v1null"].ratifier == "machine"
    assert by["tie"].value["source_ref"] == REF1  # the newer write on the tied date
    assert by["future"].value["source_ref"] == REF1
    assert by["undone_approve"].value["source_ref"] == REF1 and by["undone_approve"].pending
    assert by["undone_dismiss"].state is ValueState.AWAITING_STEWARD
    assert (by["reopened_older"].state, by["reopened_older"].reason) == (
        ValueState.NO_VALUE,
        "dismissed",
    )
    assert by["pinned"].pinned and not by["unpinned"].pinned


def test_an_unknown_id_reads_not_extracted_and_an_empty_set_reads_nothing(db):
    nobody = uuid4()
    got = state.read_states(db, [nobody], "shares_outstanding", asof=ASOF)
    assert (got[nobody].state, got[nobody].reason) == (ValueState.NO_VALUE, "not-extracted")
    assert state.read_states(db, [], "cash_burn", asof=ASOF) == {}


def _statements(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = psycopg.Cursor.execute

    def counting(self, query, *args, **kwargs):
        seen.append(str(query)[:60])
        return real(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", counting)
    return seen


@pytest.mark.parametrize("fact_type", FACT_TYPES)
def test_the_batch_issues_a_fixed_number_of_statements(db, monkeypatch, fact_type):
    by_label = _build(db, fact_type)
    ids = list(by_label.values())
    seen = _statements(monkeypatch)
    # two companies with a candidate, a value and a pin between them, so every kind of read has rows
    state.read_states(db, [by_label["pending"], by_label["pinned"]], fact_type, asof=ASOF)
    few = len(seen)
    seen.clear()
    state.read_states(db, ids, fact_type, asof=ASOF)
    assert len(ids) >= 12
    assert len(seen) == few, seen

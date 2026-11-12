"""Held insider rows (``insider_txn_hold``, migration 0082) — how the READ counts them, and the WORDS the operator
is told them in. Pure: no I/O, no clock.

A Form 4 / Form 4/A row whose transaction date cannot be true is held by the ingest instead of being stored as
a fact (``ingest.edgar.form4.write_hold``): a year before 1934, a year after the accession's filing year, or —
since rule ``2026-11-insider-rows-dated-after-filing`` — a date after its own filing. The nightly counts the hold
table and names each NEW held row once (``pipeline/insider_hold_scan.py``); the wording helpers at the bottom are
the one place that wording is built, so the health page, the Admin run row and the printed report cannot drift.

THE READ (``resolve_holds``, rule ``2026-11-insider-rows-dated-after-filing``). Every as-of read of
``fact_insider_txn`` passes through one seam on both engines (``domain.insider_amendments.ResolutionInputs
.resolve``: ``db.insider_resolution`` on Postgres, ``replay.pit`` on the mirror). After the 4/A resolution and
BEFORE the joint-report collapse and the internal-transfer screen, each Form 4 hold of the security is resolved:

1. a stored fact row that has a hold (same security, accession, row number; form '4') is never returned at its
   written date (a row an older ingest stored before the after-filing condition existed);
2. THE FILING IS WITHDRAWN OR A DUPLICATE, OR THE ROW IS REPLACED: the held row is not returned when a visible
   amendment state SUPPRESSES its filing and emits nothing for it (a void — applied, or pending, which already
   suppresses —, a later copy of a duplicates set), or replaces its row (its ``accession#seq`` key in a state's
   ``suppress_rows``) — exactly the suppression the stored rows of that filing get
   (``domain.insider_amendments.suppression_of``, one reading for both), so a held row and a stored row of one
   filing never disagree;
3. THE FILER'S CORRECTION WINS: when an amendment applied and public at the read EMITS for the hold's filing and
   carries a row with the same reporting person, transaction code, shares and price, the held row is not
   returned — the amendment's row already is, so the trade counts once. A correction that emits for the filing
   but carries no such row does NOT drop the held row: the linker does not see held rows yet, so its "whole
   correction" reading can be wrong for a filing with a held row (one 4/A restating only the held row was linked
   as a whole correction of the filing's other, stored row), and dropping an unmatched held row there could lose
   a real trade;
4. otherwise the newest STANDING steward decision (``insider_txn_hold_decision``, migration 0083) decides:
   ``approve`` returns the row dated at the ratified date (``date_basis`` 'ratified'), ``dismiss`` does not;
5. otherwise THE PLACEMENT RULE: a hold with an acceptance and a readable, possible period of report whose
   acceptance market date is 0..N days after it (``CallConfig.insider_hold_on_time_days``) — an ON-TIME
   filing — is returned dated at its filing night, ``domain.market_time.edgar_night(accepted)``
   (``date_basis`` 'filing_night'): the first night anyone could read it;
6. otherwise it is not returned: a late filing waits for the steward.

A returned row has the keys a resolved fact row has (``INSIDER_TXN_COLUMNS``, as an emitted 4/A row does) plus
``date_basis``, ``txn_date_as_filed`` and ``hold_id``, and obeys the same ``asof`` / floor gates as an emitted
amendment row. A security with no Form 4 hold reads exactly as before (the same list is returned). No corrected
date is invented (#3): a filing night is when the filing became public, never a claimed trade date, and the
price screens keyed on the trade day skip a filing-night row (``is_filing_night``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from domain.insider_amendments import (
    AMENDS_ACCESSION,
    INSIDER_TXN_COLUMNS,
    STATUS_APPLIED,
    Suppression,
    _as_date,
    _same_owner,
    _same_value,
    effective_states,
    is_other_issuer,
    latest_by_accession,
    norm_cik,
    row_key,
    suppression_of,
)
from domain.market_time import edgar_night, market_date_of

# --- the vocabulary of a returned held row ---------------------------------------------------------------

DATE_BASIS = "date_basis"  # how a returned held row is dated
TXN_DATE_AS_FILED = "txn_date_as_filed"  # the filing's own <transactionDate> text
HOLD_ID = "hold_id"  # the insider_txn_hold row it came from
BASIS_FILING_NIGHT = "filing_night"  # the placement rule: the night the filing became public
BASIS_RATIFIED = "ratified"  # a steward's approve: the date the steward ratified

HOLD_FORM_4 = "4"  # the read resolves Form 4 holds only (a held 4/A row is not applied)

ACTION_APPROVE = "approve"
ACTION_DISMISS = "dismiss"
ACTION_REVOKE = "revoke"

# the year conditions of the rule (``ingest.edgar.form4.implausible_txn_date_code`` states the whole rule; these
# are the pure pieces the read needs to judge a period of report, here so the domain never imports the ingest)
SECTION_16_EPOCH_YEAR = 1934
_SHARES_TOL = 1e-6  # the system's pick's tolerances (``domain.insider_amendments``)
_PRICE_TOL = 1e-4


_EDGAR_ACCESSION = re.compile(r"^\d{10}-(\d{2})-\d{6}$")


def accession_filing_year(accession: str) -> int | None:
    """The filing YEAR encoded in an EDGAR accession (``0001628280-24-010030`` -> 2024), or ``None`` when
    ``accession`` is not EDGAR-shaped (a seed/test label like ``"acc-planned"``).

    EDGAR's electronic era starts in 1993, so a two-digit ``93``–``99`` is the 1990s and everything else is
    2000+ (unambiguous through 2092). ``None`` means "no upper anchor" and the caller ABSTAINS from the
    upper bound rather than guessing — erring toward keeping a row (#9). (Moved here from
    ``ingest.edgar.form4``, which imports it.)"""
    m = _EDGAR_ACCESSION.match(accession.strip())
    if m is None:
        return None
    yy = int(m.group(1))
    return (1900 if 93 <= yy <= 99 else 2000) + yy


def year_is_possible(d: date, accession: str) -> bool:
    """The rule's two YEAR conditions, negated: not before 1934 and not after the accession's filing year."""
    if d.year < SECTION_16_EPOCH_YEAR:
        return False
    filing_year = accession_filing_year(accession)
    return filing_year is None or d.year <= filing_year


def is_filing_night(row: Mapping[str, Any]) -> bool:
    """A returned held row dated at its FILING NIGHT, not at a trade day: the "price below that day's low"
    screens must not judge it (the day is not its trade day) — they skip that one screen and keep it counted.
    """
    return row.get(DATE_BASIS) == BASIS_FILING_NIGHT


def held_detail(rows: Sequence[Mapping[str, Any]], accession: str) -> dict[str, Any]:
    """The held-row work for one cited filing (#6): how its held rows are dated (``date_basis``: 'filing_night'
    / 'ratified') and the dates the filing itself wrote (``txn_date_as_filed``), both sorted lists. Empty for a
    filing with no held row, so a card without one keeps its exact provenance (no record churn)."""
    held = [t for t in rows if str(t.get("accession")) == accession and t.get(DATE_BASIS)]
    if not held:
        return {}
    return {
        DATE_BASIS: sorted({str(t[DATE_BASIS]) for t in held}),
        TXN_DATE_AS_FILED: sorted({str(t.get(TXN_DATE_AS_FILED)) for t in held}),
    }


def filing_night_clause(rows: Sequence[Mapping[str, Any]]) -> str:
    """The label clause for the rows counted from their FILING NIGHT (#6), or ``""`` when there are none."""
    n = sum(1 for t in rows if is_filing_night(t))
    if not n:
        return ""
    return f"{n} row(s) counted from the filing night because the date in the filing cannot be true"


def _aware(at: Any) -> datetime | None:
    if not isinstance(at, datetime):
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _readable_date(text: object) -> date | None:
    """A filed date text as a date (its leading ``YYYY-MM-DD``), or ``None`` when it is not readable."""
    if text is None:
        return None
    s = str(text).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _placement(hold: Mapping[str, Any], on_time_days: int) -> tuple[date | None, str | None]:
    """THE PLACEMENT RULE's one set of checks: ``(filing night, None)`` for an ON-TIME held row, else
    ``(None, why)`` — the first check it fails, in plain words. ``placement_night`` and ``placement_problem``
    both read this, so the night and the reason can never disagree."""
    at = _aware(hold.get("accepted"))
    if at is None:
        return None, "no acceptance time is known for the filing"
    text = hold.get("period_of_report_as_filed")
    por = _readable_date(text)
    if por is None:
        if text is None or not str(text).strip():
            return None, "the filing gives no period of report"
        return None, f"the period of report as filed ({str(text).strip()}) cannot be read"
    if not year_is_possible(por, str(hold["accession"])):
        return None, (
            f"the period of report as filed ({por.isoformat()}) fails the year rule (before "
            f"{SECTION_16_EPOCH_YEAR}, or after the filing's year)"
        )
    filed_on = market_date_of(at)
    lag = (filed_on - por).days
    if lag < 0:
        return None, (
            f"the period of report ({por.isoformat()}) is after the filing ({filed_on.isoformat()})"
        )
    if lag > on_time_days:
        return None, (
            f"the filing was made {lag} days after its period of report ({por.isoformat()}), more than "
            f"the on-time window of {on_time_days} days"
        )
    return edgar_night(at), None


def placement_night(hold: Mapping[str, Any], on_time_days: int) -> date | None:
    """THE PLACEMENT RULE: the filing night an ON-TIME held row counts from, or ``None`` (a late filing, or one
    the rule cannot judge). On time = the hold has an acceptance and a readable period of report that is
    possible under the year rule, and the acceptance's market date is 0..``on_time_days`` days after it.
    """
    return _placement(hold, on_time_days)[0]


def placement_problem(hold: Mapping[str, Any], on_time_days: int) -> str | None:
    """Why the placement rule does NOT place the held row, in plain words, or ``None`` when it does (exactly when
    ``placement_night`` is not ``None``): no acceptance time; a period of report that is missing, unreadable or
    fails the year rule; a period of report after the filing; or a filing made more days after its period of
    report than the on-time window (both numbers stated)."""
    return _placement(hold, on_time_days)[1]


def _ver(row: Mapping[str, Any]) -> tuple[Any, str]:
    return (row["recorded_at"], str(row["id"]))


def standing_hold_decisions(
    decisions: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, int], Mapping[str, Any]]:
    """``(accession, txn_seq) -> the newest STANDING approve / dismiss`` over the Form 4 decisions a read can
    see: a decision stands iff no visible ``revoke`` names it."""
    decisions = [d for d in decisions if str(d.get("form") or HOLD_FORM_4) == HOLD_FORM_4]
    revoked = {
        str(d["revokes"]) for d in decisions if d["action"] == ACTION_REVOKE and d.get("revokes")
    }
    out: dict[tuple[str, int], Mapping[str, Any]] = {}
    for d in sorted(decisions, key=_ver):
        if d["action"] == ACTION_REVOKE or str(d["id"]) in revoked:
            continue
        if d["action"] in (ACTION_APPROVE, ACTION_DISMISS):
            out[(str(d["accession"]), int(d["txn_seq"]))] = d
    return out


def _row_of(hold: Mapping[str, Any]) -> Mapping[str, Any]:
    row = hold.get("row") or {}
    return row if isinstance(row, Mapping) else {}


def correcting_amendment(
    hold: Mapping[str, Any],
    states: Sequence[Any],
    amendments: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Rule 3's evidence: the accession of the first APPLIED amendment visible to the read, linked to the hold's
    filing (it emits for it), that carries a row with the same reporting person, transaction code, shares and
    price as the held row — ``None`` when there is none."""
    held = _row_of(hold)
    acc = str(hold["accession"])
    for st in states:
        if st.status != STATUS_APPLIED or acc not in st.emit_for:
            continue
        am = amendments.get(st.accession)
        if am is None:
            continue
        for r in am.get("rows") or ():
            if (
                _same_owner(r, held)
                and (r.get("txn_code") or None) == (held.get("txn_code") or None)
                and _same_value(r.get("shares"), held.get("shares"), _SHARES_TOL)
                and _same_value(r.get("price"), held.get("price"), _PRICE_TOL)
            ):
                return str(st.accession)
    return None


def filer_corrected(
    hold: Mapping[str, Any],
    states: Sequence[Any],
    amendments: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Rule 3: an APPLIED amendment visible to the read, linked to the hold's filing (it emits for it), carries a
    row with the same reporting person, transaction code, shares and price as the held row."""
    return correcting_amendment(hold, states, amendments) is not None


# --- where a hold sits (the steward's queue) — ONE classification, shared with the read ----------------------

BUCKET_OWED = (
    "owed"  # no standing decision, not placed by the rule, not covered by the filer: the steward's
)
BUCKET_PLACED = "placed_by_rule"  # an on-time filing: counts from its filing night
BUCKET_FILER = "filer_corrected"  # the read's step 2 or 3: the filer's own amendment decides it
BUCKET_DECIDED = "decided"  # a standing steward approve: counts at the ratified date
BUCKET_DISMISSED = "dismissed"  # a standing steward dismiss: not counted
BUCKET_AMENDMENT_ROW = "amendment_row"  # a held Form 4/A row: listed, never applied (not built)
BUCKETS = (
    BUCKET_OWED,
    BUCKET_PLACED,
    BUCKET_FILER,
    BUCKET_DECIDED,
    BUCKET_DISMISSED,
    BUCKET_AMENDMENT_ROW,
)


def why_withdrawn(amendment: str | None) -> str:
    """``filer_corrected``'s ``why`` for rule 2, the filing withdrawn or a duplicate copy."""
    by = f" {amendment}" if amendment else ""
    return f"the filer's Form 4/A{by} withdraws this filing, or marks it a duplicate copy"


def why_row_replaced(amendment: str | None) -> str:
    """``filer_corrected``'s ``why`` for rule 2, the row replaced."""
    by = f" {amendment}" if amendment else ""
    return f"the filer's Form 4/A{by} replaces this row"


def _suppressor(states: Sequence[Any], accession: str, key: str) -> str | None:
    """The first visible amendment that suppresses the filing or replaces the row (the ``why``'s evidence)."""
    for st in states:
        if accession in st.suppress or key in st.suppress_rows:
            return str(st.accession)
    return None


def why_same_trade(amendment: str) -> str:
    """``filer_corrected``'s ``why`` for rule 3: the amendment that carries the same trade."""
    return f"the filer's Form 4/A {amendment} carries the same trade"


@dataclass(frozen=True)
class HoldState:
    """Where ONE held row sits as a read sees it: its ``bucket`` (``BUCKETS``), the date the read counts it from
    (``day`` — the filing night for ``placed_by_rule``, the ratified date for ``decided``, else ``None``) and how
    (``basis``: ``filing_night`` / ``ratified``), the standing steward ``decision`` it rests on (``decided`` /
    ``dismissed``), and a short ``why`` for ``filer_corrected``."""

    bucket: str
    day: date | None = None
    basis: str | None = None
    decision: Mapping[str, Any] | None = None
    why: str | None = None

    @property
    def counted(self) -> bool:
        """Does the read return the row (at ``day``, before its ``asof`` / floor gates)?"""
        return self.day is not None


def hold_state(
    hold: Mapping[str, Any],
    *,
    sup: Suppression,
    states: Sequence[Any],
    amendments: Mapping[str, Mapping[str, Any]],
    standing: Mapping[tuple[str, int], Mapping[str, Any]],
    on_time_days: int,
) -> HoldState:
    """PURE — the ONE decision ladder (the module docstring's steps 2–6) for one hold, given what the read can
    see: ``sup`` / ``states`` / ``amendments`` = the 4/A resolution's suppression, effective states and newest
    record per accession; ``standing`` = ``standing_hold_decisions`` over the visible steward decisions.
    ``resolve_holds`` and the steward's queue (``repositories.insider_hold_repo``) both call this, so the panel
    can never disagree with the read. A Form 4/A hold is ``amendment_row`` (the read never applies one).
    """
    if str(hold.get("form") or HOLD_FORM_4) != HOLD_FORM_4:
        return HoldState(BUCKET_AMENDMENT_ROW)
    acc, seq = str(hold["accession"]), int(hold["txn_seq"])
    # 2. the filing withdrawn / a duplicate copy, or the row replaced
    if acc in sup.withdrawn:
        return HoldState(BUCKET_FILER, why=why_withdrawn(_suppressor(states, acc, "")))
    rk = row_key({"accession": acc, "txn_seq": seq})
    if rk in sup.rows:
        return HoldState(BUCKET_FILER, why=why_row_replaced(_suppressor(states, acc, rk)))
    # 3. the filer's correction carries the same trade
    corrected_by = correcting_amendment(hold, states, amendments)
    if corrected_by is not None:
        return HoldState(BUCKET_FILER, why=why_same_trade(corrected_by))
    # 4. the steward's standing decision
    d = standing.get((acc, seq))
    if d is not None:
        if d["action"] != ACTION_APPROVE:
            return HoldState(BUCKET_DISMISSED, decision=d)
        return HoldState(
            BUCKET_DECIDED, day=_as_date(d.get("ratified_date")), basis=BASIS_RATIFIED, decision=d
        )
    # 5. the placement rule (6: else it waits for the steward)
    night = placement_night(hold, on_time_days)
    if night is not None:
        return HoldState(BUCKET_PLACED, day=night, basis=BASIS_FILING_NIGHT)
    return HoldState(BUCKET_OWED)


def hold_states(
    holds: Iterable[Mapping[str, Any]],
    *,
    hold_decisions: Iterable[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    amendments: Iterable[Mapping[str, Any]],
    on_time_days: int,
) -> dict[tuple[str, int, str], HoldState]:
    """PURE — ``(accession, txn_seq, form) -> HoldState`` for ONE security's holds over the visible inputs (the
    same inputs ``resolve_holds`` takes)."""
    states = effective_states(links, decisions)
    sup = suppression_of(states)
    by_acc = latest_by_accession(amendments, "accession")
    standing = standing_hold_decisions(hold_decisions)
    return {
        (str(h["accession"]), int(h["txn_seq"]), str(h.get("form") or HOLD_FORM_4)): hold_state(
            h,
            sup=sup,
            states=states,
            amendments=by_acc,
            standing=standing,
            on_time_days=on_time_days,
        )
        for h in holds
    }


# --- the suggested date (spec 4.5) — arithmetic on the filing's own fields, never a model ----------------------

_WRITTEN_DATE = re.compile(r"^\s*(\d{1,4})-(\d{1,2})-(\d{1,2})")


def filing_market_date(hold: Mapping[str, Any]) -> date | None:
    """The latest date a held row's trade can be: the market date of its filing's acceptance, else its filing
    date (``filed``), else ``None`` (neither known)."""
    at = _aware(hold.get("accepted"))
    if at is not None:
        return market_date_of(at)
    return _as_date(hold.get("filed"))


def _written_month_day(text: object) -> tuple[int, int] | None:
    if text is None:
        return None
    m = _WRITTEN_DATE.match(str(text))
    if m is None:
        return None
    month, day = int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return month, day


def date_as_filed(hold: Mapping[str, Any]) -> str | None:
    """The text of the filing's own transaction date (the hold's ``row``)."""
    r = _row_of(hold)
    v = r.get(TXN_DATE_AS_FILED) or r.get("txn_date")
    return None if v is None else str(v)


def suggested_date(hold: Mapping[str, Any]) -> tuple[date | None, bool]:
    """THE SUGGESTION (spec 4.5) — a recommendation only, never applied by itself: the written month and day in
    the latest year that puts it on or before the filing's market date (``filing_market_date``), tried in that
    date's year and the year before, so a suggestion is never more than a year before its filing.

    Returns ``(date, agrees)``. ``agrees`` is True only when the date is on or after a readable period of
    report that is possible under the year rule. ``(None, False)`` when the written date cannot be read, its
    month and day exist in neither year (Feb 29 outside a leap year), or no filing date is known."""
    md_ = _written_month_day(date_as_filed(hold))
    ceiling = filing_market_date(hold)
    if md_ is None or ceiling is None:
        return None, False
    month, day = md_
    found: date | None = None
    for year in (ceiling.year, ceiling.year - 1):
        try:
            cand = date(year, month, day)
        except ValueError:
            continue
        if cand <= ceiling:
            found = cand
            break
    if found is None:
        return None, False
    por = _readable_date(hold.get("period_of_report_as_filed"))
    agrees = por is not None and year_is_possible(por, str(hold["accession"])) and found >= por
    return found, agrees


def ratify_problem(hold: Mapping[str, Any], ratified: date) -> str | None:
    """Why a steward's ratified date cannot stand (``None`` = it can): it must pass the year rule (not before
    1934, not after the accession's filing year) and be on or before the filing's market date — which must be
    known."""
    if not year_is_possible(ratified, str(hold["accession"])):
        return (
            f"{ratified.isoformat()} fails the year rule (before {SECTION_16_EPOCH_YEAR}, or after the "
            "filing's year)"
        )
    ceiling = filing_market_date(hold)
    if ceiling is None:
        return "the filing has no acceptance and no filing date, so no ratified date can be checked"
    if ratified > ceiling:
        return f"{ratified.isoformat()} is after the filing's market date {ceiling.isoformat()}"
    return None


def held_fact_row(hold: Mapping[str, Any], valid_from: date, basis: str) -> dict[str, Any]:
    """One returned held row in ``fact_insider_txn``'s shape (built from the hold's parsed ``row``), dated
    ``valid_from``, plus ``date_basis`` / ``txn_date_as_filed`` / ``hold_id``."""
    r = _row_of(hold)
    out: dict[str, Any] = {c: None for c in INSIDER_TXN_COLUMNS}
    out.update({c: r.get(c) for c in INSIDER_TXN_COLUMNS if c in r})
    out.update(
        {
            "id": hold["id"],
            "tenant_id": hold["tenant_id"],
            "security_id": hold["security_id"],
            "accession": hold["accession"],
            "valid_from": valid_from,
            "valid_to": None,
            "recorded_at": hold["recorded_at"],
            "supersedes": None,
            "txn_seq": hold["txn_seq"],
            "accepted": hold.get("accepted"),
        }
    )
    out[DATE_BASIS] = basis
    out[TXN_DATE_AS_FILED] = r.get(TXN_DATE_AS_FILED) or r.get("txn_date")
    out[HOLD_ID] = hold["id"]
    return out


def resolve_holds(
    rows: Sequence[dict[str, Any]],
    *,
    holds: Iterable[Mapping[str, Any]],
    hold_decisions: Iterable[Mapping[str, Any]],
    amendments: Iterable[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    own_ciks: Iterable[str] = (),
    asof: date,
    lower: date | None = None,
    on_time_days: int,
) -> list[dict[str, Any]]:
    """PURE — ONE security's resolved insider rows (``resolve_insider_rows``'s output) with its Form 4 holds
    resolved (the module docstring's six steps). ``holds`` / ``hold_decisions`` are every row the read can see
    (the caller's gate); ``amendments`` / ``links`` / ``decisions`` the 4/A inputs the read already resolved
    with. No Form 4 hold -> ``rows`` is returned unchanged (the same list)."""
    form4 = [h for h in holds if str(h.get("form") or HOLD_FORM_4) == HOLD_FORM_4]
    if not form4:
        return rows if isinstance(rows, list) else list(rows)
    keys = {(str(h["accession"]), int(h["txn_seq"])) for h in form4}
    # 1. a stored row with a hold is never returned at its written date (an emitted 4/A row is the 4/A's own)
    kept = [
        r
        for r in rows
        if AMENDS_ACCESSION in r or (str(r.get("accession")), r.get("txn_seq")) not in keys
    ]
    states = effective_states(links, decisions)
    sup = suppression_of(states)  # the SAME suppression the stored rows got (resolve_insider_rows)
    by_acc = latest_by_accession(amendments, "accession")
    standing = standing_hold_decisions(hold_decisions)
    own = frozenset(c for c in (norm_cik(x) for x in own_ciks) if c)
    emitted: list[dict[str, Any]] = []
    for h in sorted(form4, key=lambda x: (str(x["accession"]), int(x["txn_seq"]))):
        # steps 2-6: the ONE ladder (``hold_state``) the steward's queue also reads
        st = hold_state(
            h,
            sup=sup,
            states=states,
            amendments=by_acc,
            standing=standing,
            on_time_days=on_time_days,
        )
        day, basis = st.day, st.basis
        if day is None or basis is None or day > asof or (lower is not None and day < lower):
            continue
        out = held_fact_row(h, day, basis)
        if not is_other_issuer(out, own):
            emitted.append(out)
    if not emitted and len(kept) == len(rows):
        return rows if isinstance(rows, list) else list(rows)
    return kept + emitted


# --- the words --------------------------------------------------------------------------------------------

# the hold's ``reason`` code (``ingest.edgar.form4.HOLD_*``, the rule's own) in the operator's words
REASON_WORDS = {
    "year_before_1934": "the year is before 1934, when insider reporting began",
    "year_after_filing_year": "the year is after the year the filing was made",
    "after_filing": "the date is after SEC accepted the filing",
}

# each bucket as the panel and the run report name it
BUCKET_WORDS = {
    BUCKET_OWED: "owed your decision",
    BUCKET_PLACED: "placed by rule (counted from the filing night)",
    BUCKET_FILER: "corrected by the filer",
    BUCKET_DECIDED: "decided",
    BUCKET_DISMISSED: "dismissed",
    BUCKET_AMENDMENT_ROW: "held in a Form 4/A (cannot be applied yet)",
}


def reason_words(code: str | None) -> str:
    """A hold's reason code in the operator's words (the code itself when it is not one the rule writes)."""
    return REASON_WORDS.get(code or "", code or "unknown")


# The most held rows one page NAMES; the rest are counted (the Form 4/A page's cap, the same number).
PAGE_NAME_CAP = 8


def _shares(shares: float | int | None) -> str:
    if shares is None:
        return "? sh"
    try:
        n = float(shares)
    except (TypeError, ValueError):
        return f"{shares} sh"
    return f"{n:,.0f} sh" if n == int(n) else f"{n:,.4f}".rstrip("0").rstrip(".") + " sh"


def hold_label(
    *,
    ticker: str | None,
    security_id: str,
    accession: str,
    form: str,
    code: str | None,
    shares: float | int | None,
    bucket: str | None = None,
) -> str:
    """One held row as the page names it: the ticker (else the security id's first 8 characters), the
    accession, the transaction code and the shares — ``"ABCD 0000000000-24-000001 (A, 22,500 sh)"``; a row of an
    amendment says so (``"... 4/A (S, 100 sh)"``), and a row in bucket ``amendment_row`` adds its own words
    (``AMENDMENT_ROW_WORDS``)."""
    who = ticker or security_id[:8]
    amended = " 4/A" if form == "4/A" else ""
    label = f"{who} {accession}{amended} ({code or '?'}, {_shares(shares)})"
    if bucket == BUCKET_AMENDMENT_ROW:
        label += f" {AMENDMENT_ROW_WORDS}"
    return label


# what a held Form 4/A row means for the operator: nobody can act on it in the queue yet, so it is told once
AMENDMENT_ROW_WORDS = (
    "[held in a Form 4/A: it cannot be applied yet, so the amendment's trade is not counted]"
)

# the buckets whose NEW rows page once: a row nothing counts until someone acts (owed), and a row nothing can
# count yet (a held Form 4/A row); a row placed by the rule or corrected by the filer is counted — quiet
PAGED_BUCKETS = frozenset({BUCKET_OWED, BUCKET_AMENDMENT_ROW})


def page_names(named: Sequence[str], more: int) -> str:
    """The named rows, then the count of the rest — ``"A, B (+3 more in the run artifact)"``."""
    shown = ", ".join(named)
    if not more:
        return shown
    tail = f"+{more} more in the run artifact"
    return f"{shown} ({tail})" if shown else tail


def page_bit(named: Sequence[str], more: int) -> str:
    """The health line for NEW held rows that are NOT COUNTED (``PAGED_BUCKETS``: owed a steward decision, or
    held in a Form 4/A — a held amendment row's label says so itself); a new row placed by rule or corrected by
    the filer is a quiet count, never paged. Without its closing note (each surface adds its own: the page says
    "not a cron error", the Admin run row its benign marker)."""
    n = len(named) + more
    return (
        f"{n} insider row(s) held because the filing's transaction date cannot be true, not counted: "
        f"{page_names(named, more)} — a Form 4 row is owed a steward decision on Admin (Held insider rows), "
        "and no date is invented"
    )


def quiet_new_line(by_bucket: Mapping[str, int]) -> str:
    """The printed report's ONE quiet line for NEW held rows that need no decision (placed by rule, corrected
    by the filer, a standing decision): counted, never paged. ``""`` when there are none."""
    quiet = {b: n for b, n in by_bucket.items() if n and b not in PAGED_BUCKETS}
    if not quiet:
        return ""
    parts = "; ".join(f"{n} {BUCKET_WORDS.get(b, b)}" for b, n in sorted(quiet.items()))
    return f"{sum(quiet.values())} new held insider row(s) that need no decision ({parts}) — quiet"


def owed_flag_reason(n: int, accessions: Sequence[str]) -> str:
    """The data flag's reason for a company with OWED held rows (``domain.data_flags.INSIDER_HOLD_OWED``)."""
    filings = ", ".join(accessions)
    return (
        f"{n} insider row(s) from filing {filings} are held: the filing's transaction date cannot be true, "
        "and no steward has ratified a date yet, so the insider counts leave them out."
    )


OWED_FLAG_CLEARS = (
    "Clears when a steward ratifies a date or dismisses the row on Admin (Held insider rows), or when the "
    "filer's own Form 4/A corrects it."
)

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
   but carries no such row does NOT drop the held row. Since rule ``2026-11-amendment-linker-sees-held-rows``
   the linker sees held rows (``ingest.edgar.form4a.stored_rows_by_accession``): a 4/A that corrects only the
   held row is a matched replacement NAMING it (step 2), and the filing's other rows stand. A whole correction
   still names no row, and a link written before the rule may not have seen the held row
   (``pipeline.relink_amendments`` re-links those), so an unmatched held row is kept rather than risk losing a
   real trade;
4. otherwise the newest STANDING steward decision (``insider_txn_hold_decision``, migration 0083) decides:
   ``approve`` returns the row dated at the ratified date (``date_basis`` 'ratified'), ``dismiss`` does not;
5. otherwise THE PLACEMENT RULE: a hold with an acceptance and a readable, possible period of report whose
   acceptance market date is 0..N days after it (``CallConfig.insider_hold_on_time_days``) — an ON-TIME
   filing — is returned dated at its filing night, ``domain.market_time.edgar_night(accepted)``
   (``date_basis`` 'filing_night'): the first night anyone could read it;
6. otherwise it is not returned: a late filing waits for the steward.

A hold's ``accepted`` here is always the RESOLVED acceptance (rule ``2026-11-held-row-reads-its-filings-acceptance``,
defined once in ``db.public_clock.hold_accepted_sql`` and selected by every loader): the hold's own, else — a Form
4 hold only — the newest acceptance stored on its filing's ``fact_insider_txn`` rows of the same security, else
none. A hold is never updated, so one written before its filing's acceptance was backfilled reads it this way;
a Form 4/A hold keeps its own (an amendment's rows are not stored in ``fact_insider_txn``). This module only
reads the value it is handed.

A returned row has the keys a resolved fact row has (``INSIDER_TXN_COLUMNS``, as an emitted 4/A row does) plus
``date_basis``, ``txn_date_as_filed`` and ``hold_id``, and obeys the same ``asof`` / floor gates as an emitted
amendment row. A security with no Form 4 hold reads exactly as before (the same list is returned). No corrected
date is invented (#3): a filing night is when the filing became public, never a claimed trade date, and the
price screens keyed on the trade day skip a filing-night row (``is_filing_night``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, NamedTuple

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
            f"the filing is late: SEC accepted it {lag} days after its period of report ({por.isoformat()}, "
            f"the earliest trade it reports), past the {on_time_days}-day on-time window, so its filing night "
            "is no stand-in for this row's date"
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


# --- the suggested date (spec 4.5, rule W3-1) — the filing's own dates and arithmetic, never a model ----------
#
# A RECOMMENDATION for the steward's queue only: nothing the read calls reads it (``hold_state``,
# ``resolve_holds`` and the placement rule never do), and it is applied only when a steward approves it. In order:
#
# 1. the dates the filing itself WRITES with the held row's own month and day: in a footnote the row cites, as the
#    date of a stored row of the same filing (a SIBLING), or as the period of report, a ``00YY`` year read as
#    ``20YY`` (``suggestion_period_of_report``; the placement rule keeps reading the raw text);
# 2. the filing's BALANCE CHAIN: among the stored rows of the same ownership form (direct / indirect), the one
#    whose ``shares_owned_after`` is this row's opening balance (``_balance_chain``) dates the earliest the row can
#    be, the one whose balance opens from this row's the latest — table order is not evidence (the indirect rows
#    of a filing can follow its direct ones);
# 3. the month/day arithmetic (``_arithmetic_date``), unchanged.
#
# A candidate FITS when it is on or after the period of report (when readable) and inside the chain's window, which
# ends at the filing's market date. One fitting written date is suggested, else the arithmetic when it fits;
# ``agrees`` (one click may ratify it) also needs a readable period of report. A one-row filing whose row fits
# nothing suggests its period of report, agreeing only by the steward's setting. Anything else is WITHHELD: no
# date, the failed check and the filing's own window in words.

_WRITTEN_DATE = re.compile(r"^\s*(\d{1,4})-(\d{1,2})-(\d{1,2})")

# where a suggestion comes from: the filing writes it (a footnote the row cites, a stored row of the same filing,
# its period of report with 00YY read as 20YY); the written month and day in the latest year on or before the
# filing; the period of report of a one-row filing whose row fits nothing
BASIS_FOOTNOTE = "footnote"
BASIS_SIBLING_ROW = "sibling_row"
BASIS_PERIOD_OF_REPORT = "period_of_report"
BASIS_ARITHMETIC = "arithmetic"
BASIS_ONE_ROW = "period_of_report_one_row"
SUGGESTION_BASES = (
    BASIS_FOOTNOTE,
    BASIS_SIBLING_ROW,
    BASIS_PERIOD_OF_REPORT,
    BASIS_ARITHMETIC,
    BASIS_ONE_ROW,
)
# a date the filing writes in several places takes the first of these as its basis
_WRITTEN_BASES = (BASIS_FOOTNOTE, BASIS_SIBLING_ROW, BASIS_PERIOD_OF_REPORT)

_MONTHS = {
    name: i
    for i, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
# the three ways a footnote writes a date: "March 23, 2023" (or "Mar. 23, 2023"), "11/21/2017", "2023-03-23"
_FOOTNOTE_DATE = re.compile(
    rf"\b(?P<mon>{_MONTH_NAMES})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})\b"
    r"|\b(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{4})\b"
    r"|\b(?P<y3>\d{4})-(?P<m3>\d{1,2})-(?P<d3>\d{1,2})\b",
    re.IGNORECASE,
)


class Suggestion(NamedTuple):
    """THE SUGGESTION for one held row (``suggest``). ``date`` = the suggested date (``None`` = withheld or none can
    be made); ``agrees`` = one click may ratify it; ``basis`` = where it comes from (``SUGGESTION_BASES``; ``None``
    with no date); ``words`` = the same in plain words, or why no date is offered; ``window_from`` /
    ``window_to`` = the filing's own range for the row (the later of the period of report and the row its balance
    follows; the earlier of the row that follows it and the market date), ``None`` when unknown."""

    date: date | None
    agrees: bool
    basis: str | None = None
    words: str | None = None
    window_from: date | None = None
    window_to: date | None = None


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


def suggestion_period_of_report(hold: Mapping[str, Any]) -> date | None:
    """The period of report THE SUGGESTION reads: the filed text as a date, a year below 100 read as ``20YY`` only
    when that is the accession's filing year or the year before (``0023-03-23`` on a 2024 accession -> 2023-03-23),
    and the result possible under the year rule; else ``None``. The placement rule never reads this: a ``00YY``
    period of report still fails its year check (``_placement``)."""
    por = _readable_date(hold.get("period_of_report_as_filed"))
    if por is None:
        return None
    acc = str(hold["accession"])
    if por.year < 100:
        filing_year = accession_filing_year(acc)
        if filing_year is None:
            return None
        year = next((y for y in (filing_year, filing_year - 1) if y % 100 == por.year), None)
        if year is None:
            return None
        try:
            por = por.replace(year=year)
        except ValueError:
            return None
    return por if year_is_possible(por, acc) else None


def _footnote_dates(texts: object) -> list[date]:
    """Every date written in the footnotes a row cites (``row["txn_footnotes"]``), in reading order."""
    if not isinstance(texts, (list, tuple)):
        return []
    out: list[date] = []
    for text in texts:
        for m in _FOOTNOTE_DATE.finditer(str(text)):
            if m.group("mon"):
                ymd = (int(m.group("y")), _MONTHS[m.group("mon").lower()], int(m.group("d")))
            elif m.group("m2"):
                ymd = (int(m.group("y2")), int(m.group("m2")), int(m.group("d2")))
            else:
                ymd = (int(m.group("y3")), int(m.group("m3")), int(m.group("d3")))
            try:
                out.append(date(*ymd))
            except ValueError:
                continue
    return out


def _number(v: object) -> float | None:
    """A share count or balance as a finite float, else ``None`` (missing or unreadable proves nothing)."""
    if v is None or v == "":
        return None
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _same_shares(a: float, b: float) -> bool:
    return abs(a - b) <= _SHARES_TOL * max(1.0, abs(a), abs(b))


class _Chain(NamedTuple):
    """The stored rows the held row's balance proves: ``before`` = the row whose balance this row opens from,
    ``after`` = the row whose balance opens from this row's (each ``None`` when not proven)."""

    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None


def _balance_chain(hold: Mapping[str, Any], siblings: Sequence[Mapping[str, Any]]) -> _Chain:
    """The filing's BALANCE CHAIN around the held row, among the siblings of its own ownership form (``D`` / ``I``)
    that carry a date and a balance. BEFORE: the one sibling whose ``shares_owned_after`` is the row's opening
    balance (its balance less its shares when the row acquired, plus them when it disposed; either when the row
    does not say). AFTER: the one other sibling whose balance differs from the row's by exactly that sibling's
    shares. A missing value, or more than one match, proves nothing (tolerance: the system's share tolerance,
    relative)."""
    r = _row_of(hold)
    own_after, own_shares = _number(r.get("shares_owned_after")), _number(r.get("shares"))
    form = r.get("direct_indirect")
    if own_after is None or own_shares is None or form not in ("D", "I"):
        return _Chain(None, None)
    same = [
        s
        for s in siblings
        if s.get("direct_indirect") == form
        and _number(s.get("shares_owned_after")) is not None
        and _as_date(s.get("valid_from")) is not None
    ]
    code = str(r.get("acquired_disposed") or "").strip().upper()
    opening = {"A": (own_after - own_shares,), "D": (own_after + own_shares,)}.get(
        code, (own_after - own_shares, own_after + own_shares)
    )
    before = [
        s for s in same if any(_same_shares(_number(s["shares_owned_after"]), o) for o in opening)
    ]
    first = before[0] if len(before) == 1 else None
    after = []
    for s in same:
        shares = _number(s.get("shares"))
        if s is first or shares is None:
            continue
        if _same_shares(abs(_number(s["shares_owned_after"]) - own_after), shares):
            after.append(s)
    return _Chain(first, after[0] if len(after) == 1 else None)


def _arithmetic_date(month_day: tuple[int, int], ceiling: date) -> date | None:
    """The written month and day in the latest year that puts it on or before ``ceiling``, tried in that date's
    year and the year before (never more than a year before the filing); ``None`` when neither year has it.
    """
    month, day = month_day
    for year in (ceiling.year, ceiling.year - 1):
        try:
            cand = date(year, month, day)
        except ValueError:
            continue
        if cand <= ceiling:
            return cand
    return None


def _and(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _balance(v: object) -> str:
    n = _number(v)
    if n is None:
        return "?"
    return f"{n:,.0f}" if n == int(n) else f"{n:,.4f}".rstrip("0").rstrip(".")


def suggest(
    hold: Mapping[str, Any],
    siblings: Iterable[Mapping[str, Any]] = (),
    *,
    row_count: int | None = None,
    one_row_por_agrees: bool = False,
) -> Suggestion:
    """THE SUGGESTION (spec 4.5, rule W3-1; the section comment states the rule) — a recommendation only, never
    applied by itself. ``siblings`` = the stored rows of the hold's filing and security (``txn_seq``,
    ``valid_from``, ``shares``, ``shares_owned_after``, ``direct_indirect``; a row with the hold's own number is
    ignored); ``row_count`` = how many rows that filing has for the security, stored and held (``None`` = unknown:
    never read as a one-row filing); ``one_row_por_agrees`` = the steward's setting for a one-row filing's period
    of report (``Settings.insider_hold_one_row_por_agrees``, read by the caller: the domain reads no settings).
    """
    text = date_as_filed(hold)
    ceiling = filing_market_date(hold)
    if ceiling is None:
        return Suggestion(
            None,
            False,
            words="no acceptance and no filing date are known, so no date can be checked",
        )
    month_day = _written_month_day(text)
    if month_day is None:
        return Suggestion(None, False, words=f"the row's written date ({text}) cannot be read")
    acc, seq = str(hold["accession"]), hold.get("txn_seq")
    sibs = [s for s in siblings if seq is None or int(s["txn_seq"]) != int(seq)]
    por = suggestion_period_of_report(hold)
    chain = _balance_chain(hold, sibs)
    first = _as_date(chain.before["valid_from"]) if chain.before is not None else None
    last = _as_date(chain.after["valid_from"]) if chain.after is not None else None
    low = max((d for d in (por, first) if d is not None), default=None)
    high = min(d for d in (last, ceiling) if d is not None)

    def fits(d: date) -> bool:
        return (low is None or d >= low) and d <= high

    def window(date_: date | None, agrees: bool, basis: str | None, words: str) -> Suggestion:
        return Suggestion(date_, agrees, basis, words, low, high)

    # 1. the dates the filing writes with this row's month and day (on or before the filing, the year rule)
    written: dict[date, dict[str, list[int]]] = {}

    def add(d: date | None, basis: str, row: int | None = None) -> None:
        if (
            d is None
            or (d.month, d.day) != month_day
            or d > ceiling
            or not year_is_possible(d, acc)
        ):
            return
        rows = written.setdefault(d, {}).setdefault(basis, [])
        if row is not None and row not in rows:
            rows.append(row)

    for d in _footnote_dates(_row_of(hold).get("txn_footnotes")):
        add(d, BASIS_FOOTNOTE)
    for s in sibs:
        add(_as_date(s.get("valid_from")), BASIS_SIBLING_ROW, int(s["txn_seq"]))
    add(por, BASIS_PERIOD_OF_REPORT)
    no_por = (
        ""
        if por is not None
        else "; the period of report cannot be read, so it does not agree on its own"
    )

    fitting = sorted(d for d in written if fits(d))
    if len(fitting) == 1:
        d = fitting[0]
        sources = written[d]
        basis = next(b for b in _WRITTEN_BASES if b in sources)
        parts = []
        if BASIS_FOOTNOTE in sources:
            parts.append("written in a footnote this row cites")
        if BASIS_SIBLING_ROW in sources:
            rows = sorted(sources[BASIS_SIBLING_ROW])
            label = "row" if len(rows) == 1 else "rows"
            parts.append(f"the date of {label} {_and([str(n) for n in rows])} of the same filing")
        if BASIS_PERIOD_OF_REPORT in sources:
            as_filed = str(hold.get("period_of_report_as_filed") or "").strip()
            filed_as = f" (written {as_filed})" if as_filed[:10] != d.isoformat() else ""
            parts.append(f"the filing's period of report{filed_as}")
        return window(d, por is not None, basis, f"{d.isoformat()}: {_and(parts)}{no_por}")
    if len(fitting) > 1:
        d = fitting[-1]
        return window(
            d,
            False,
            next(b for b in _WRITTEN_BASES if b in written[d]),
            f"the filing writes {_and([x.isoformat() for x in fitting])} with this row's month and day; "
            f"the latest is shown, and it does not agree on its own",
        )

    # 2. the month/day arithmetic, when it fits
    arith = _arithmetic_date(month_day, ceiling)
    if arith is not None and fits(arith):
        return window(
            arith,
            por is not None,
            BASIS_ARITHMETIC,
            f"{arith.isoformat()}: the written month and day ({text}) in the latest year on or before the "
            f"filing's market date ({ceiling.isoformat()}){no_por}",
        )

    # 3. a one-row filing: its period of report (the filing's earliest trade is its only one)
    if row_count == 1 and not sibs and por is not None and por <= ceiling:
        agrees = bool(one_row_por_agrees)
        tail = "" if agrees else ", so it does not agree on its own"
        return window(
            por,
            agrees,
            BASIS_ONE_ROW,
            f"{por.isoformat()}: the period of report of a one-row filing; the row's written date ({text}) "
            f"fits neither it nor the filing{tail}",
        )

    # 4. WITHHELD: the failed check, and the filing's own window
    if arith is None:
        why = f"the written month and day ({text}) exist in neither {ceiling.year} nor {ceiling.year - 1}"
    elif por is not None and arith < por:
        why = f"{arith.isoformat()} is before the period of report ({por.isoformat()})"
    elif first is not None and arith < first:
        why = (
            f"{arith.isoformat()} is before the {first.isoformat()} row this one follows in the filing's "
            f"balances ({_balance(chain.before['shares_owned_after'])} -> "
            f"{_balance(_row_of(hold).get('shares_owned_after'))} shares)"
        )
    else:
        why = (
            f"{arith.isoformat()} is after the {high.isoformat()} row that follows this one in the filing's "
            f"balances ({_balance(_row_of(hold).get('shares_owned_after'))} -> "
            f"{_balance(chain.after['shares_owned_after'] if chain.after else None)} shares)"
        )
    if low is None:
        span = f"the filing puts it on or before {high.isoformat()}"
    elif low > high:
        span = f"the filing's own dates leave no window ({low.isoformat()} is after {high.isoformat()})"
    else:
        span = f"the filing puts it between {low.isoformat()} and {high.isoformat()}"
    return window(None, False, None, f"{why}; {span}")


def suggested_date(hold: Mapping[str, Any]) -> tuple[date | None, bool]:
    """``(date, agrees)`` of ``suggest`` for a hold read alone (no stored sibling rows, its row count unknown)."""
    s = suggest(hold)
    return s.date, s.agrees


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

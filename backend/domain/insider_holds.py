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
2. THE FILER'S CORRECTION WINS: when an amendment applied and public at the read is linked to the hold's filing
   (it emits for it) and carries a row with the same reporting person, transaction code, shares and price, the
   held row is not returned — the amendment's row already is, so the trade counts once;
3. otherwise the newest STANDING steward decision (``insider_txn_hold_decision``, migration 0083) decides:
   ``approve`` returns the row dated at the ratified date (``date_basis`` 'ratified'), ``dismiss`` does not;
4. otherwise THE PLACEMENT RULE: a hold with an acceptance and a readable, possible period of report whose
   acceptance market date is 0..N days after it (``CallConfig.insider_hold_on_time_days``) — an ON-TIME
   filing — is returned dated at its filing night, ``domain.market_time.edgar_night(accepted)``
   (``date_basis`` 'filing_night'): the first night anyone could read it;
5. otherwise it is not returned: a late filing waits for the steward.

A returned row has the keys a resolved fact row has (``INSIDER_TXN_COLUMNS``, as an emitted 4/A row does) plus
``date_basis``, ``txn_date_as_filed`` and ``hold_id``, and obeys the same ``asof`` / floor gates as an emitted
amendment row. A security with no Form 4 hold reads exactly as before (the same list is returned). No corrected
date is invented (#3): a filing night is when the filing became public, never a claimed trade date, and the
price screens keyed on the trade day skip a filing-night row (``is_filing_night``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from domain.insider_amendments import (
    AMENDS_ACCESSION,
    INSIDER_TXN_COLUMNS,
    STATUS_APPLIED,
    _as_date,
    _same_owner,
    _same_value,
    effective_states,
    is_other_issuer,
    latest_by_accession,
    norm_cik,
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


def placement_night(hold: Mapping[str, Any], on_time_days: int) -> date | None:
    """THE PLACEMENT RULE: the filing night an ON-TIME held row counts from, or ``None`` (a late filing, or one
    the rule cannot judge). On time = the hold has an acceptance and a readable period of report that is
    possible under the year rule, and the acceptance's market date is 0..``on_time_days`` days after it.
    """
    at = _aware(hold.get("accepted"))
    por = _readable_date(hold.get("period_of_report_as_filed"))
    if at is None or por is None or not year_is_possible(por, str(hold["accession"])):
        return None
    lag = (market_date_of(at) - por).days
    if not 0 <= lag <= on_time_days:
        return None
    return edgar_night(at)


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


def filer_corrected(
    hold: Mapping[str, Any],
    states: Sequence[Any],
    amendments: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Rule 2: an APPLIED amendment visible to the read, linked to the hold's filing (it emits for it), carries a
    row with the same reporting person, transaction code, shares and price as the held row."""
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
                return True
    return False


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
    resolved (the module docstring's five steps). ``holds`` / ``hold_decisions`` are every row the read can see
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
    by_acc = latest_by_accession(amendments, "accession")
    standing = standing_hold_decisions(hold_decisions)
    own = frozenset(c for c in (norm_cik(x) for x in own_ciks) if c)
    emitted: list[dict[str, Any]] = []
    for h in sorted(form4, key=lambda x: (str(x["accession"]), int(x["txn_seq"]))):
        if filer_corrected(h, states, by_acc):  # 2. the filer's correction wins
            continue
        d = standing.get((str(h["accession"]), int(h["txn_seq"])))
        if d is not None:  # 3. the steward's decision
            if d["action"] != ACTION_APPROVE:
                continue
            day, basis = _as_date(d.get("ratified_date")), BASIS_RATIFIED
        else:  # 4. the placement rule (5: else it waits)
            day, basis = placement_night(h, on_time_days), BASIS_FILING_NIGHT
        if day is None or day > asof or (lower is not None and day < lower):
            continue
        out = held_fact_row(h, day, basis)
        if not is_other_issuer(out, own):
            emitted.append(out)
    if not emitted and len(kept) == len(rows):
        return rows if isinstance(rows, list) else list(rows)
    return kept + emitted


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
) -> str:
    """One held row as the page names it: the ticker (else the security id's first 8 characters), the
    accession, the transaction code and the shares — ``"ABCD 0000000000-24-000001 (A, 22,500 sh)"``; a row of an
    amendment says so (``"... 4/A (S, 100 sh)"``)."""
    who = ticker or security_id[:8]
    amended = " 4/A" if form == "4/A" else ""
    return f"{who} {accession}{amended} ({code or '?'}, {_shares(shares)})"


def page_names(named: Sequence[str], more: int) -> str:
    """The named rows, then the count of the rest — ``"A, B (+3 more in the run artifact)"``."""
    shown = ", ".join(named)
    if not more:
        return shown
    tail = f"+{more} more in the run artifact"
    return f"{shown} ({tail})" if shown else tail


def page_bit(named: Sequence[str], more: int) -> str:
    """The health line for NEW held rows, without its closing note (each surface adds its own: the page says
    "not a cron error", the Admin run row its benign marker)."""
    n = len(named) + more
    return (
        f"{n} insider row(s) held because the filing's transaction date cannot be true: "
        f"{page_names(named, more)} — held, not stored as a fact, and no date is invented"
    )

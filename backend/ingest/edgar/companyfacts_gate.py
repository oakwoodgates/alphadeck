"""Q17 — the FILING-GATED freshness class: when a RECURRING pass must re-pull a company's ``companyfacts``.

companyfacts (``data.sec.gov/api/xbrl/companyfacts/CIK<10>.json``) is the largest document the cron reads:
MEASURED on prod 2026-10-17, the nightly fundamentals leg re-pulled 680 of them, 1.18 GB, every weekday
night, because the cache's 12 h TTL had always expired by 22:30. Yet a company's companyfacts changes only
when it files something carrying XBRL facts — about 38 names a night on that roster. So on a RECURRING
client the ``companyfacts/`` key class is re-pulled only when one of these holds:

  (a) it is not cached (``EdgarClient.get_text`` — a miss always fetches);
  (b) the company's cached ``submissions/CIK<10>.json`` lists a filing whose form is in ``XBRL_FORMS``,
      ACCEPTED after the cached companyfacts was fetched (less ``ACCEPTANCE_GRACE_S``) — the index the
      nightly filing legs already re-pull at the five-minute recurring TTL, read from DISK here, so the gate
      adds no SEC request;
  (c) the weekly backstop ``BACKSTOP_S`` has elapsed — the bound on everything (b) cannot see.

A missing or unreadable index, or a qualifying row with no parseable date, is DUE (fail toward fetching:
recall is sacred, a missed quarter is a recall failure). The same gate decides when a companyfacts 404 is
retried (``EdgarClient``'s negative cache, X8): a company with no companyfacts is re-asked on a new
qualifying filing or the backstop, not nightly.

THE FORM SET IS MEASURED, NOT GUESSED (2026-10-17, read-only over prod's 688 cached companyfacts files,
1.20 GB): every distinct ``form`` on any fact, then an ``/A`` for every base form (an amendment of a form
that carried facts can carry restated ones — ``F-10/A`` was filed by a basket name in the measurement
window although no cached fact cites one yet). The measurement, as ``form: fact rows / companies``::

    10-Q 4,718,983/559 · 10-K 2,462,288/540 · 20-F 309,027/121 · 6-K 106,141/81 · 10-Q/A 76,232/178 ·
    10-K/A 70,866/235 · 8-K 54,079/63 · 40-F 30,226/29 · 20-F/A 21,627/48 · S-1 8,511/36 · 10-KT 8,071/19 ·
    S-1/A 6,480/22 · 6-K/A 6,037/20 · 40-F/A 5,232/17 · DEF 14A 5,106/188 · S-4 3,150/15 · POS AM 2,736/10 ·
    S-4/A 2,410/7 · 8-K/A 1,938/7 · 10-QT 855/4 · PRE 14A 647/29 · S-8 556/131 · 424B5 356/48 ·
    424B2 348/17 · S-3 269/47 · 424B7 216/13 · F-1 135/11 · S-3ASR 127/26 · F-3 109/19 · PRER14A 50/2 ·
    PREC14A 40/1 · DEFC14A 40/1 · PREM14A 34/7 · S-1MEF 25/5 · POSASR 20/3 · F-10 20/4 · 424I 20/4 ·
    F-1/A 20/2 · DEF 14C 18/1 · S-3/A 15/3 · 424B3 15/2 · DEFA14A 12/1 · SC TO-T 10/2 · F-10EF 10/2 ·
    S-3MEF 5/1 · F-3/A 5/1 · F-3ASR 5/1 · SC14D1F 5/1 · SC14D1F/A 5/1 · 2.01 SD 3/1 · 10-KT/A 2/1

It is deliberately the WHOLE set, not just the forms carrying the fundamentals leg's own concepts (revenue +
the three share counts: 21 forms, led by 10-Q / 10-K / 20-F / 10-Q/A / 10-K/A / 6-K / 8-K): the leg's
concepts can move, the cost of an extra form is one extra pull of one company, and the cost of a missing one
is a silently stale quarter. A form that starts carrying facts later is caught by the backstop within a week;
re-measure with the same scan when that matters.

Pure: no network, no clock (``now`` is a parameter), no DB. ``EdgarClient`` owns the file reads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ingest.edgar.acceptance import request_free_acceptance

_MEASURED_FORMS = (
    "10-Q",
    "10-K",
    "20-F",
    "6-K",
    "10-Q/A",
    "10-K/A",
    "8-K",
    "40-F",
    "20-F/A",
    "S-1",
    "10-KT",
    "S-1/A",
    "6-K/A",
    "40-F/A",
    "DEF 14A",
    "S-4",
    "POS AM",
    "S-4/A",
    "8-K/A",
    "10-QT",
    "PRE 14A",
    "S-8",
    "424B5",
    "424B2",
    "S-3",
    "424B7",
    "F-1",
    "S-3ASR",
    "F-3",
    "PRER14A",
    "PREC14A",
    "DEFC14A",
    "PREM14A",
    "S-1MEF",
    "POSASR",
    "F-10",
    "424I",
    "F-1/A",
    "DEF 14C",
    "S-3/A",
    "424B3",
    "DEFA14A",
    "SC TO-T",
    "F-10EF",
    "S-3MEF",
    "F-3/A",
    "F-3ASR",
    "SC14D1F",
    "SC14D1F/A",
    "2.01 SD",
    "10-KT/A",
)

# The forms whose acceptance makes a cached companyfacts DUE: the measured set plus an amendment of every
# base form in it (see the module docstring).
XBRL_FORMS: frozenset[str] = frozenset(_MEASURED_FORMS) | frozenset(
    f"{f}/A" for f in _MEASURED_FORMS if not f.endswith("/A")
)

# (c) THE WEEKLY BACKSTOP — 6.5 days, not 7: a pass takes up to an hour and reaches a company at a slightly
# different minute each night, so at exactly 7 days a name fetched by last Monday's pass could read a few
# seconds short of due at this Monday's and slip to Tuesday. 6.5 days makes "the same weekday next week" the
# due pass however the order shifts, and no weekday pass sees it due early.
BACKSTOP_S = 6.5 * 24 * 3600

# (b)'s slack: a qualifying filing accepted up to this long BEFORE the cached companyfacts was fetched still
# makes it due. It absorbs SEC's XBRL processing delay (a fetch that landed after the acceptance but before
# the facts were processed must not be trusted to hold them) and any clock skew between SEC's acceptance
# stamp and this host's file mtime. MEASURED 2026-10-17: same-day filings were already in companyfacts 5-6 h
# after acceptance (ROLR's two 10-Q/As at +330 min, CSHR's S-8 at +305 min), so a day is generous; on the
# nightly cadence it costs nothing extra (the next pass's fetch is already more than a day past the filing).
ACCEPTANCE_GRACE_S = 24 * 3600


@dataclass(frozen=True)
class GateDecision:
    """Whether a cached companyfacts (or its negative-cache marker) is DUE for a re-pull, and why — the
    reason is for a log line and a test, never a value anything computes on."""

    due: bool
    reason: str


def _row_instant(accepted: str | None, filing_date: str | None) -> datetime | None:
    """The instant a submissions row became public, with NO request (this gate reads the index from disk by
    design): the acceptance clock's request-free reading (``ingest.edgar.acceptance.request_free_acceptance``)
    — the earlier of the listed ``acceptanceDateTime`` and the END of the ``filingDate`` in ET, so a listed
    time SEC's list pushed past its filing date is bounded back to that date, and a row with no acceptance is
    dated at the latest instant its date allows (errs toward DUE); ``None`` when neither is readable.
    """
    return request_free_acceptance(accepted, filing_date)


def companyfacts_due(
    *,
    fetched_at: float,
    now: float,
    submissions: Mapping[str, Any] | None,
) -> GateDecision:
    """The gate, PURE. ``fetched_at`` = when the cached companyfacts (or its 404 marker) was written (epoch
    seconds — the file's mtime); ``now`` = the clock; ``submissions`` = the company's cached submissions JSON,
    or ``None`` when it is missing or unreadable.

    Order: the backstop first (it needs nothing else), then "no index" (fail toward fetching), then any
    qualifying filing accepted after ``fetched_at - ACCEPTANCE_GRACE_S``. Only EDGAR's ``recent`` window is
    read: it holds the newest >= 1,000 filings / 1 year, so a filing accepted since the last fetch (at most
    ``BACKSTOP_S`` ago) is always in it."""
    if now - fetched_at >= BACKSTOP_S:
        return GateDecision(True, "weekly backstop")
    if submissions is None:
        return GateDecision(True, "no cached filing index to vouch for it")
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    accepts = recent.get("acceptanceDateTime") or []
    dates = recent.get("filingDate") or []
    since = fetched_at - ACCEPTANCE_GRACE_S
    for i, form in enumerate(forms):
        if form not in XBRL_FORMS:
            continue
        at = _row_instant(
            accepts[i] if i < len(accepts) else None, dates[i] if i < len(dates) else None
        )
        accession = accns[i] if i < len(accns) else "?"
        if at is None:
            return GateDecision(True, f"{form} {accession} has no readable date")
        if at.timestamp() > since:
            return GateDecision(True, f"new {form} {accession} accepted {at.isoformat()}")
    return GateDecision(False, "no new XBRL-bearing filing since the last fetch")


__all__ = [
    "XBRL_FORMS",
    "BACKSTOP_S",
    "ACCEPTANCE_GRACE_S",
    "GateDecision",
    "companyfacts_due",
]

"""THE ACCEPTANCE CLOCK — which instant to store as a Form 4 / 4/A's SEC acceptance, so the filing lands on the
right night and never early.

WHY IT MATTERS. Since the public-clock rule (operator decision 2026-10-21, ``db/public_clock.py``) the stored
``accepted`` IS the public instant of an insider fact on every history view (the Scoreboard editions, the
drawer, a past-asof recompute, the backtest mirror) and of a 4/A record and its system decisions. A night ends
at EDGAR's next open, 06:00 ET the next weekday (``domain.market_time.known_at_for_asof``; midnight until F6,
2026-10-31), so a time EARLY on the right date is harmless, a LATE time past that open moves the filing to the
next night, and an early one into the night before is a lookahead. The live nightly reads the record clock.

WHAT WENT WRONG (MEASURED on prod 2026-10-24, read-only). The time comes from SEC's submissions JSON
(``acceptanceDateTime``), and from about 2026-10-23 04:00 ET that list became inconsistent PER FILING: every
capture since (onboarding loads, the 10-23 nightly) stored 25-45% of accessions at an hour EDGAR is closed
(before 06:00 or after 22:00 ET), mostly +4 h / +5 h late; the captures of 10-20..10-22 were clean. Checked
against SEC's own filing header (``ACCEPTANCE-DATETIME`` in ``<accession>-index-headers.html``, ET wall
clock): 0000902664-25-005199 was filed 2025-12-04, its header says 19:00:20 ET, and today's list says
2025-12-05T05:00:20Z = 00:00:20 ET the NEXT day — a night late. 5,776 stored accessions sit a night late.

THE RULE (``resolve_acceptance``):

1. TRUST the listed time when its ET date equals the listed ``filingDate`` AND its ET time is within EDGAR's
   hours, 06:00-22:00 inclusive (``listed_is_trusted``). A Form 3/4/5 accepted up to 22:00 is filed that
   business day (Regulation S-T 13(a)(4)), so a true Form 4 acceptance always passes.
2. Otherwise READ THE FILING'S OWN HEADER (``read_header_acceptance``): one small, immutable document through
   the ``EdgarClient`` — the global SEC budget, cached forever under ``forms/<accession>/`` (the immutable key
   class, the same key ``statement_sources`` reads), ET -> UTC with zoneinfo (EST and EDT both right).
3. Header unreadable (a cache miss without live pulls, a 404, no parseable field): the END of the filing date
   in ET (23:59:59 ET as UTC, ``filing_date_bound``). Never early — an acceptance is on or before its filing
   date — and always on the right night. Never NULL for this case: a NULL makes the public clock fall back to
   ``min(recorded_at)``, which for a cold-walked old filing is today.

Two degenerate inputs keep their old behavior, with no header read: NO listed time -> ``None`` (the row stays
NULL and visible, #9 — the NULL backfill, ``pipeline.backfill_accepted``, is its repair), and a listed time
with no filing date to judge it by -> the listed time, unchecked. EDGAR lists both on every row (the 0.66% of
NULL insider facts on prod predate the capture, migration 0040); these are fixture shapes.

THE REQUEST-FREE READS (never a header fetch):

- ``trusted_listed`` — the listed time only when trusted, else ``None``: the 4/A linker's order map of the
  ORIGINALS (``ingest.edgar.form4a.accepted_times``). An untrusted original is left out, so the order test
  falls back to the filing dates (unknown = keep the candidate, #9) instead of being refused on a late time.
- ``request_free_acceptance`` — the companyfacts gate (``ingest/edgar/companyfacts_gate.py``), which reads the
  index from disk by design: the earlier of the listed time and the filing-date bound (a trusted time is
  already the earlier). See that function for why the earlier, not the bound alone.

Pure except ``read_header_acceptance`` (one client read). One home: every leg that stores a listed acceptance
and the repair (``pipeline/backfill_accepted.py --drift``) call it, so they cannot drift.

THE PUBLIC DATE OF A FILING (F7, rule ``2026-11-filings-dated-by-acceptance``) — 8-K, 13D/G, 10-Q / 10-K.
``resolve_public_acceptance`` adapts the rule above to the forms whose filing date ROLLS: Regulation S-T Rule
13(a)(2) deems a filing submitted after 17:30 ET filed on the NEXT business day, though EDGAR disseminates it
that evening (13(a)(4) gives Forms 3/4/5 — and, since 2024, Schedules 13D/G — a 22:00 cutoff instead). So:

1. TRUST the listed time when it passes the test above, OR when its ET date is the business day BEFORE the
   filing date and its ET time is 17:30-22:00 (``listed_is_trusted_rolled``; no holiday calendar exists for
   SEC business days, so at most one weekday may sit between the two dates — a holiday — and none on a weekend).
2. Otherwise the filing's own header — from the CACHE only (``cached_header_acceptance``), never a request:
   the 8-K tape is built from the submissions index alone, and this rule adds no SEC traffic. A header time
   must pass the same test.
3. Otherwise NO acceptance (``None``, ``SOURCE_FILED``): the filing date stands, exactly as before F7 — late,
   never early. (Unlike a Form 4 there is no bound to store: these tables fall back to the filing date.)

``public_date`` turns a trusted acceptance into the row's ``valid_from``: its date in MARKET time
(``domain.market_time.market_tz``), else the filing date.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from domain.market_time import market_tz
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.client import InvalidBody
from ingest.edgar.submissions import (
    _listing_map,
    parse_acceptance,
    submissions_page_names,
)

# EDGAR's own clock. Fixed, not ``Settings.market_tz``: SEC stamps acceptances and filing dates in Eastern time
# whatever zone this deployment's trading day is configured in.
EDGAR_TZ = ZoneInfo("America/New_York")
# EDGAR accepts filings 06:00-22:00 ET (both ends inclusive here: a listed 22:00:00 is trusted, 22:00:01 is not)
EDGAR_OPENS = time(6, 0)
EDGAR_CLOSES = time(22, 0)

# Where a resolved acceptance came from.
SOURCE_LISTED = "listed"  # the submissions list's time, consistent with its filing date
SOURCE_HEADER = "header"  # the filing's own header (the listed time was rejected)
SOURCE_BOUND = "bound"  # the end of the filing date, ET (rejected, and the header was unreadable)
SOURCE_UNCHECKED = "unchecked"  # a listed time with no filing date to judge it by — kept as given
SOURCE_MISSING = "missing"  # no listed time and no filing date: nothing to store (NULL)
SOURCE_FILED = (
    "filed"  # F7: no trusted time (listed or cached header) — the filing date stands (NULL stored)
)

# Regulation S-T Rule 13(a)(2): submitted after 17:30 ET -> deemed filed the next business day (F7)
FILING_DATE_ROLL = time(17, 30)

_HEADER_FIELD = re.compile(r"ACCEPTANCE-DATETIME(?:>|&gt;)\s*(\d{14})")


@dataclass(frozen=True)
class Acceptance:
    """One filing's resolved acceptance: the instant to store (``at``), where it came from (``source``), and
    the listed time as SEC's list gave it (``listed``, for the report)."""

    at: datetime | None
    source: str
    listed: datetime | None = None

    @property
    def rejected(self) -> bool:
        """Was the listed time set aside (inconsistent with the filing date or EDGAR's hours)?"""
        return self.source in (SOURCE_HEADER, SOURCE_BOUND)


@dataclass
class AcceptanceTally:
    """How many listed times a pass set aside, by what replaced them — the quiet nightly line's counts
    (``pipeline.ingest_thesis.print_acceptance_line``)."""

    header: int = 0
    bound: int = 0

    def add(self, a: Acceptance) -> None:
        if a.source == SOURCE_HEADER:
            self.header += 1
        elif a.source == SOURCE_BOUND:
            self.bound += 1

    @property
    def rejected(self) -> int:
        return self.header + self.bound


def _as_instant(listed: str | datetime | None) -> datetime | None:
    if isinstance(listed, datetime):
        return listed if listed.tzinfo is not None else listed.replace(tzinfo=timezone.utc)
    return parse_acceptance(listed)


def parse_filing_date(filed: str | date | None) -> date | None:
    """EDGAR's ``filingDate`` ("2025-12-04") as a date; ``None`` when absent or unreadable."""
    if isinstance(filed, datetime):
        return filed.date()
    if isinstance(filed, date):
        return filed
    try:
        return date.fromisoformat(str(filed or "").strip()[:10])
    except ValueError:
        return None


def listed_is_trusted(listed: datetime | None, filed: date | None) -> bool:
    """THE TRUST TEST: the listed instant's ET date is the filing date, and its ET time is within EDGAR's hours
    (06:00-22:00 inclusive)."""
    if listed is None or filed is None:
        return False
    et = listed.astimezone(EDGAR_TZ)
    return et.date() == filed and EDGAR_OPENS <= et.time() <= EDGAR_CLOSES


def filing_date_bound(filed: date) -> datetime:
    """The END of the filing date in ET (23:59:59 ET), as UTC — the latest instant a filing of that date can
    have been accepted, so never early, and on that date's night."""
    return datetime.combine(filed, time(23, 59, 59), tzinfo=EDGAR_TZ).astimezone(timezone.utc)


def parse_header_acceptance(text: str) -> datetime | None:
    """SEC's ``ACCEPTANCE-DATETIME`` (``YYYYMMDDHHMMSS``, ET wall clock) from a filing header page — raw
    (``<ACCEPTANCE-DATETIME>``) or HTML-escaped (``&lt;ACCEPTANCE-DATETIME&gt;``, the ``<PRE>`` copy) — as
    a UTC instant; ``None`` when the field is absent or not a real time."""
    m = _HEADER_FIELD.search(text or "")
    if m is None:
        return None
    try:
        wall = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return wall.replace(tzinfo=EDGAR_TZ).astimezone(timezone.utc)


def header_url(cik: str | int, accession: str) -> str:
    """The filing's header page: ``<archives>/<cik>/<accession, no dashes>/<accession>-index-headers.html``."""
    return (
        f"{get_settings().sec_archives_base}/{int(cik)}/{accession.replace('-', '')}/"
        f"{accession}-index-headers.html"
    )


def header_cache_key(accession: str) -> str:
    """Under ``forms/<accession>/`` — the IMMUTABLE key class (``ingest.edgar.client._IMMUTABLE_PREFIXES``): a
    filing's header never changes, so it is fetched once and cached forever. The same key
    ``ingest.edgar.statement_sources`` reads the page under."""
    return f"forms/{accession}/{accession}-index-headers.html"


def _unreadable(e: Exception) -> bool:
    """Is ``e`` one header's fetch/parse failure (fall back to the filing-date bound) rather than a systemic
    fault? A cache miss with live pulls off, SEC's 2xx error reply (``InvalidBody``), a garbled body, a missing
    file, or a fetch that still fails after the polite retries (a 404 included). A missing User-Agent, a DB
    fault, anything else is not one filing's and still raises."""
    if isinstance(e, (CacheMiss, InvalidBody, json.JSONDecodeError, UnicodeDecodeError, OSError)):
        return True
    try:
        import httpx  # lazy, mirroring the clients
    except ImportError:  # pragma: no cover — with httpx absent no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPError)


def read_header_acceptance(client: Any, cik: str | int | None, accession: str) -> datetime | None:
    """The filing's own ``ACCEPTANCE-DATETIME`` through ``client`` (cache-first; a live read draws from the
    global SEC budget), or ``None`` when it cannot be read (``_unreadable``) or no CIK is known."""
    if not cik or not accession:
        return None
    try:
        text = client.get_text(header_url(cik, accession), header_cache_key(accession))
    except Exception as e:
        if _unreadable(e):
            return None
        raise
    return parse_header_acceptance(text)


def resolve_acceptance(
    listed: str | datetime | None,
    filed: str | date | None,
    header: Callable[[], datetime | None] | None,
) -> Acceptance:
    """THE RULE (module docstring): the listed time when trusted; else the filing's header (``header()``, called
    only then — ``None`` means no reader); else the end of the filing date in ET."""
    at = _as_instant(listed)
    if at is None:
        return Acceptance(None, SOURCE_MISSING, None)  # no listed time: NULL, as before (#9)
    day = parse_filing_date(filed)
    if day is None:
        return Acceptance(at, SOURCE_UNCHECKED, at)
    if listed_is_trusted(at, day):
        return Acceptance(at, SOURCE_LISTED, at)
    from_header = header() if header is not None else None
    if from_header is not None:
        return Acceptance(from_header, SOURCE_HEADER, at)
    return Acceptance(filing_date_bound(day), SOURCE_BOUND, at)


def resolve_filing_acceptance(
    client: Any,
    cik: str | int | None,
    filing: dict[str, Any],
    *,
    tally: AcceptanceTally | None = None,
) -> Acceptance:
    """``resolve_acceptance`` for one enumerated submissions row (``filings_of``'s shape: ``accession``,
    ``filed``, ``accepted``), reading the header through ``client`` when the listed time is set aside; counted
    on ``tally`` (the nightly line)."""
    accession = str(filing.get("accession") or "")
    out = resolve_acceptance(
        filing.get("accepted"),
        filing.get("filed"),
        lambda: read_header_acceptance(client, cik, accession),
    )
    if tally is not None:
        tally.add(out)
    return out


def trusted_listed(listed: str | datetime | None, filed: str | date | None) -> datetime | None:
    """The listed time when TRUSTED, else ``None`` — request-free (see the module docstring's linker note)."""
    at = _as_instant(listed)
    return at if listed_is_trusted(at, parse_filing_date(filed)) else None


def request_free_acceptance(
    listed: str | datetime | None, filed: str | date | None
) -> datetime | None:
    """The companyfacts gate's acceptance, with NO request: the EARLIER of the listed time and the end of the
    filing date in ET (whichever is known; ``None`` when neither is).

    A trusted listed time is always the earlier, so this is "the date-consistent value or the filing-date
    bound" wherever the two disagree in the drift's direction (a listed time pushed past its filing date's
    end: the bound wins). It keeps the listed time where the listed time is EARLIER than the bound but on
    another date — an XBRL filing accepted after 17:30 ET carries the NEXT business day's filing date (unlike
    a Form 4), so its correct acceptance is the evening before; bounding it to the end of that next day would
    keep its companyfacts DUE for up to three more nightly passes (the 24 h grace), each a needless re-pull.
    The gate only needs an instant that is never EARLIER than the truth; both inputs are bounded below by it
    under the measured drift (+4 h / +5 h late), and the 24 h grace absorbs hours either way."""
    at = _as_instant(listed)
    day = parse_filing_date(filed)
    bound = filing_date_bound(day) if day is not None else None
    if at is None:
        return bound
    if bound is None:
        return at
    return min(at, bound)


# --- F7: the public date of an 8-K / 13D/G / 10-Q / 10-K (the module docstring's second rule) --------------


def _rolls_to(acc_day: date, filed: date) -> bool:
    """Is ``filed`` the SEC business day after ``acc_day``? Both weekdays, ``filed`` later, and at most ONE
    weekday strictly between them (a federal holiday — no holiday calendar exists for SEC business days, so one
    is allowed; two consecutive are not)."""
    gap = (filed - acc_day).days
    if gap < 1 or gap > 5 or acc_day.weekday() >= 5 or filed.weekday() >= 5:
        return False
    between = (acc_day + timedelta(days=d) for d in range(1, gap))
    return sum(1 for d in between if d.weekday() < 5) <= 1


def listed_is_trusted_rolled(listed: datetime | None, filed: date | None) -> bool:
    """THE F7 TRUST TEST: ``listed_is_trusted`` (the ET date is the filing date, within EDGAR's hours), OR the
    filing-date roll — the ET date is the business day BEFORE the filing date and the ET time is 17:30-22:00
    (both ends inclusive), the evening an after-17:30 filing was disseminated."""
    if listed is None or filed is None:
        return False
    if listed_is_trusted(listed, filed):
        return True
    et = listed.astimezone(EDGAR_TZ)
    return FILING_DATE_ROLL <= et.time() <= EDGAR_CLOSES and _rolls_to(et.date(), filed)


def cached_header_acceptance(cache_dir: Path | None, accession: str) -> datetime | None:
    """The filing's own ``ACCEPTANCE-DATETIME`` from the EDGAR cache ON DISK (``header_cache_key``), never a
    request; ``None`` when not cached or unreadable."""
    if cache_dir is None or not accession:
        return None
    try:
        text = (Path(cache_dir) / header_cache_key(accession)).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    return parse_header_acceptance(text)


def resolve_public_acceptance(
    listed: str | datetime | None,
    filed: str | date | None,
    header: Callable[[], datetime | None] | None = None,
) -> Acceptance:
    """THE F7 RULE (module docstring): the listed time when ``listed_is_trusted_rolled``; else the header
    (``header()`` — a cache read; called only then) when it passes the same test; else ``None`` with
    ``SOURCE_FILED`` — the filing date stands, as before (never early)."""
    at = _as_instant(listed)
    day = parse_filing_date(filed)
    if day is None:
        return Acceptance(None, SOURCE_MISSING, at)  # nothing to judge a time by: store none
    if listed_is_trusted_rolled(at, day):
        return Acceptance(at, SOURCE_LISTED, at)
    from_header = header() if header is not None else None
    if listed_is_trusted_rolled(from_header, day):
        return Acceptance(from_header, SOURCE_HEADER, at)
    return Acceptance(None, SOURCE_FILED, at)


def public_date(accepted: datetime | None, filed: date) -> date:
    """A filing row's ``valid_from`` (F7): the MARKET-TIME date of its trusted acceptance, else its filing date."""
    if accepted is None:
        return filed
    return accepted.astimezone(market_tz()).date()


def cached_listings(cache_dir: Path | None, cik: str | int | None) -> dict[str, tuple[str, str]]:
    """``{accession: (filingDate, acceptanceDateTime)}`` for one company from the EDGAR cache ON DISK — its
    submissions index and every older page that is cached — never a request (``submissions.listings_deep``'s
    map without the fetches). ``{}`` when nothing is cached. The ``recent`` window wins an overlap.
    """
    if cache_dir is None or not cik:
        return {}
    root = Path(cache_dir) / "submissions"
    try:
        subs = json.loads((root / f"CIK{int(cik):010d}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    merged: dict[str, tuple[str, str]] = {}
    for name in submissions_page_names(subs):
        try:
            merged.update(_listing_map(json.loads((root / name).read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue  # an older page not cached: its accessions stay unresolved (dated by filing date)
    merged.update(_listing_map(subs.get("filings", {}).get("recent", {})))
    return merged


def resolve_listed_filing(
    listings: Mapping[str, tuple[str, str]],
    accession: str,
    filed: date,
    cache_dir: Path | None = None,
) -> Acceptance:
    """``resolve_public_acceptance`` for a STORED row by its accession, from a ``cached_listings`` map (+ the cached
    header). The listing must carry the row's own filing date — a row dated by another filing (a derived Q4 dated
    by the later of two filings) is never re-dated (``SOURCE_FILED``)."""
    listing = listings.get(accession)
    if listing is None or parse_filing_date(listing[0]) != filed:
        return Acceptance(None, SOURCE_FILED, None)
    return resolve_public_acceptance(
        listing[1], filed, lambda: cached_header_acceptance(cache_dir, accession)
    )


__all__ = [
    "EDGAR_TZ",
    "SOURCE_LISTED",
    "SOURCE_HEADER",
    "SOURCE_BOUND",
    "SOURCE_UNCHECKED",
    "SOURCE_MISSING",
    "SOURCE_FILED",
    "FILING_DATE_ROLL",
    "Acceptance",
    "AcceptanceTally",
    "cached_header_acceptance",
    "cached_listings",
    "filing_date_bound",
    "header_cache_key",
    "header_url",
    "listed_is_trusted",
    "listed_is_trusted_rolled",
    "parse_filing_date",
    "parse_header_acceptance",
    "public_date",
    "read_header_acceptance",
    "request_free_acceptance",
    "resolve_acceptance",
    "resolve_filing_acceptance",
    "resolve_listed_filing",
    "resolve_public_acceptance",
    "trusted_listed",
]

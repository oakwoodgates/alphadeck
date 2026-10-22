"""The freeze page's RATIO FLOOR — how many of the companies a pass ingested got FRESH SEC data.

O5 (#425) made the freeze verdict read COMPLETED EDGAR fetches, but it pages only at EXACTLY zero. The July
freeze did not look like zero: MEASURED on prod's stored artifacts, eleven live passes made 1–4 EDGAR
attempts across 255–494 basket names (0.2–1.6%) — a cache served stale, nothing errored, almost no request
left the box — and neither rule paged them. The operator's decision (2026-10-21): page when FEWER THAN
``Settings.freeze_completed_floor`` (50%) of the EDGAR-eligible companies a pass ingested got a completed
SEC fetch. ``docs/FEED_LOOP.md`` "Known gaps" holds the measured separation.

THE RATIO, exactly (``EdgarCoverage``):

- **A company is a CIK** (zero-padded to ten digits, ``cik_key``) — the SEC filer, which is what every EDGAR
  leg reads (``submissions/CIK<10>.json``). Two share classes of one issuer are one company; a name placed
  in N segments (N basket rows) is one company.
- **Denominator — the EDGAR-ELIGIBLE companies**: the distinct CIKs whose EDGAR legs (Form 4 / 8-K / 13D/G)
  RAN this pass, across the theses the pass PROCESSED (``NameResult.edgar_cik``). Not eligible: a member
  with no CIK (its legs return before any request), a member missing from the tenant master, and a company
  another writer held past the W1 wait (``lock_busy`` — its legs did not run here); a thesis a ``--catch-up``
  SKIPPED (M2) contributes nothing. A CLOSED or acknowledged-unpriceable name is still eligible: T3 / Q8
  excuse only its PRICE 404, and its EDGAR legs run like any other's. An ETF counts exactly when it has a
  CIK, because then its legs make the same requests.
- **Numerator — the companies with FRESH SEC data**: those for which at least one EDGAR fetch COMPLETED (a
  2xx body, ``EdgarClient.live_completed``) while their legs ran, in ANY thesis of the pass
  (``NameResult.edgar_completed``). The union across theses is load-bearing: each thesis builds its own
  client and the recurring TTL is five minutes, so a company another thesis fetched minutes earlier is a
  cache hit HERE — MEASURED on the 2026-10-19 22:30 ET pass (the X5 counters), a 14-company thesis run right
  after a 190-company one holding 13 of them made exactly 1 submissions fetch. Per thesis that reads starved
  (1 of 14); per pass all 14 were fetched minutes apart.

FROZEN when ``fresh < floor × eligible`` (strictly fewer — exactly at the floor does not page) on a LIVE pass,
beside O5's zero rule; ``eligible == 0`` never pages by the floor (no denominator).

THE RULE A PASS WAS JUDGED UNDER IS ITS OWN (the O5 contract): the coverage AND the floor ride the run
artifact (``payload``), and the Admin history re-derives from them (``from_payload``) — so a later change of
the setting never recolors a past night, and an artifact written before this existed (no coverage keys)
keeps the rule it was paged under.

Pure: no DB, no network, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keep the layering one-way (no runtime import of the pass or the ingest unit)
    from pipeline.daily import ThesisRunResult
    from pipeline.ingest_thesis import NameResult

# The operator's floor (2026-10-21) — `Settings.freeze_completed_floor` defaults to it (a test pins the two).
DEFAULT_FREEZE_COMPLETED_FLOOR = 0.5


def cik_key(cik: str | int | None) -> str | None:
    """The company key: the CIK zero-padded to ten digits (the ``submissions/CIK<10>.json`` form), or None for
    no CIK. A non-numeric value (never expected — the master stores digits) keys on its stripped text rather
    than raising: a coverage count must never cost a name its result."""
    if cik is None:
        return None
    s = str(cik).strip()
    if not s:
        return None
    return f"{int(s):010d}" if s.isdigit() else s


def clamp_floor(floor: float) -> float:
    """The floor as judged: a value outside ``[0, 1]`` is clamped (0 disables the floor; 1 pages unless every
    eligible company got fresh data). Lenient on purpose — a typo in an env var must not stop the cron.
    """
    return min(1.0, max(0.0, float(floor)))


def company_sets(names: Iterable[NameResult]) -> tuple[frozenset[str], frozenset[str]]:
    """``(eligible, fresh)`` CIK sets over one thesis's ingest results: eligible = a CIK and its EDGAR legs
    ran (``edgar_cik``); fresh = the eligible ones with >= 1 completed EDGAR fetch while they ran. A CIK seen on
    two rows (one company, two segments) is fresh if either row's legs completed a fetch."""
    eligible: set[str] = set()
    fresh: set[str] = set()
    for n in names:
        key = cik_key(getattr(n, "edgar_cik", None))
        if key is None:
            continue
        eligible.add(key)
        if getattr(n, "edgar_completed", 0) > 0:
            fresh.add(key)
    return frozenset(eligible), frozenset(fresh)


def pass_fresh(results: Iterable[ThesisRunResult]) -> frozenset[str]:
    """Every CIK that got fresh SEC data anywhere in the pass (the union over its processed theses)."""
    out: set[str] = set()
    for r in results:
        if not r.skipped and r.edgar_ciks_fresh is not None:
            out |= r.edgar_ciks_fresh
    return frozenset(out)


@dataclass(frozen=True)
class EdgarCoverage:
    """One pass's per-company EDGAR freshness and the floor it was judged under (see the module docstring)."""

    fresh: int  # eligible companies with >= 1 completed EDGAR fetch this pass
    eligible: int  # distinct EDGAR-eligible companies the pass's processed theses ingested
    floor: float  # the floor the pass was judged under (`Settings.freeze_completed_floor`, clamped)

    @property
    def ratio(self) -> float | None:
        return self.fresh / self.eligible if self.eligible else None

    @property
    def starved(self) -> bool:
        """Strictly FEWER than ``floor`` of the eligible companies got fresh SEC data (exactly at it is not)."""
        return self.eligible > 0 and self.fresh < self.floor * self.eligible

    def payload(self) -> dict:
        """The run-level artifact keys (``cron_run_log.build_run_payload``)."""
        return {
            "edgar_companies": self.eligible,
            "edgar_companies_fresh": self.fresh,
            "freeze_completed_floor": self.floor,
        }

    @classmethod
    def from_payload(cls, doc: dict) -> EdgarCoverage | None:
        """The coverage a run artifact recorded, or None when it carries none (written before the floor, or a
        malformed value) — then the pass is re-judged by the rule it was paged under, never by this one.
        """
        eligible = doc.get("edgar_companies")
        fresh = doc.get("edgar_companies_fresh")
        floor = doc.get("freeze_completed_floor")
        if not (_is_count(eligible) and _is_count(fresh)):
            return None
        if not isinstance(floor, (int, float)) or isinstance(floor, bool):
            return None
        return cls(fresh=fresh, eligible=eligible, floor=clamp_floor(floor))


def _is_count(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def coverage_of(results: Iterable[ThesisRunResult], *, floor: float) -> EdgarCoverage | None:
    """The pass's coverage over the theses it PROCESSED (a ``--catch-up``'s M2-skipped theses are left out),
    or None when it processed none or any processed result was not measured (``edgar_ciks`` None).
    """
    processed = [r for r in results if not r.skipped]
    if not processed:
        return None
    eligible: set[str] = set()
    for r in processed:
        if r.edgar_ciks is None or r.edgar_ciks_fresh is None:
            return None
        eligible |= r.edgar_ciks
    fresh = pass_fresh(processed) & eligible
    return EdgarCoverage(fresh=len(fresh), eligible=len(eligible), floor=clamp_floor(floor))

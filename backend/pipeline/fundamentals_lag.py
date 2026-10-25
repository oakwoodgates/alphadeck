"""The "fundamentals not current" reading (data hygiene, 2026-10-24): SEC's own data is behind a filed report.

A company files its 10-Q, and SEC's companyfacts — the one source our fundamentals (and the revenue trigger)
read — still lacks it weeks later. MEASURED 2026-10-24 on prod's cached EDGAR copy (1,026 companies with a
cached companyfacts): 36 (SRE, AEP, DUK, GD, PYPL, CDNS, NXPI, BIIB, ...) whose latest 10-Q is in no companyfacts
row — 35 of them the Q2 report filed late July / August, RBOT its Q1 — although our cached copy was fetched 58 to
177 days after the filing. That is an UPSTREAM lag (SEC's data is behind, not ours), so it is a quiet data flag
that says why, and it clears on its own when SEC catches up.

THE RULE (pure: ``judge``), per company, over two files already on disk — the cached ``submissions`` index and
the cached ``companyfacts`` document (no SEC request, ever):

1. the company's latest 10-Q / 10-K by filing date (``PERIODIC``; an amendment is not "the latest report");
2. filed at least ``MIN_AGE_DAYS`` before the view's day (SEC's XBRL processing takes hours to days);
3. our cached companyfacts was written at least ``ACCEPTANCE_GRACE_S`` after that filing was accepted —
   otherwise it is OUR copy that may be behind, which the nightly's filing gate re-pulls on its own
   (``ingest/edgar/companyfacts_gate.py``, the same grace), and the sentence "isn't in SEC's data" could be
   false;
4. no carried 10-Q / 10-K or amendment (any companyfacts row with its accession number) covers that report's
   period or a later one. An amendment counts: a 10-Q/A SEC carries for the same period means SEC's data is
   current (BWXT, QMLS, VTIX — MEASURED).

Nothing here judges a company whose companyfacts is not cached (no file says nothing about SEC). The reader
reads the files by their accession numbers as BYTES (``"<accession>"`` occurs in a companyfacts document only
as a row's ``accn``), and memoizes each file's answers by its (mtime, size), so a cached document is read
again only after the nightly re-pulled it. Display-only: nothing on the call path imports this module.
"""

from __future__ import annotations

import json
import mmap
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ingest.edgar.acceptance import request_free_acceptance
from ingest.edgar.companyfacts_gate import ACCEPTANCE_GRACE_S

# The EDGAR cache the nightly writes (``ingest.edgar.client._DEFAULT_CACHE``) — a local copy so this read path
# names the directory without building a client; pinned equal by test.
DEFAULT_EDGAR_CACHE = Path(__file__).resolve().parents[2] / "data" / "edgar_cache"

# "The latest report": an original quarterly or annual report. Amendments only ever count as CARRYING a period.
PERIODIC = ("10-Q", "10-K")
AMENDMENTS = ("10-Q/A", "10-K/A")

# A report filed less than a week ago is too new to judge: SEC's data takes hours to days to carry one.
MIN_AGE_DAYS = 7


@dataclass(frozen=True)
class PeriodicFiling:
    """One 10-Q / 10-K (or amendment) as the submissions index lists it."""

    form: str
    accession: str
    filed: date
    period: date
    accepted: datetime | None
    primary_doc: str | None = None


@dataclass(frozen=True)
class FundamentalsLag:
    """A filed report SEC's data does not carry yet. ``last_period`` = the newest period SEC's data carries
    among the listed reports (``None`` when it carries none of them)."""

    cik: str
    form: str
    accession: str
    filed: date
    period: date
    last_period: date | None
    primary_doc: str | None = None

    @property
    def filing_url(self) -> str:
        folder = self.accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{folder}"
        return f"{base}/{self.primary_doc}" if self.primary_doc else f"{base}/"

    @property
    def companyfacts_url(self) -> str:
        return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(self.cik):010d}.json"


def _day(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def periodic_filings(submissions: Mapping[str, Any] | None) -> list[PeriodicFiling]:
    """Every 10-Q / 10-K and amendment in the index's ``recent`` window (the newest >= 1,000 filings or a
    year — always holding a company's latest quarterly or annual report), with a readable filing date and
    period. Pure."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []

    def col(name: str, i: int) -> Any:
        values = recent.get(name) or []
        return values[i] if i < len(values) else None

    out: list[PeriodicFiling] = []
    for i, form in enumerate(forms):
        if form not in PERIODIC and form not in AMENDMENTS:
            continue
        filed, period, accession = (
            _day(col("filingDate", i)),
            _day(col("reportDate", i)),
            col("accessionNumber", i),
        )
        if filed is None or period is None or not accession:
            continue
        out.append(
            PeriodicFiling(
                form=form,
                accession=str(accession),
                filed=filed,
                period=period,
                accepted=request_free_acceptance(
                    col("acceptanceDateTime", i), col("filingDate", i)
                ),
                primary_doc=col("primaryDocument", i) or None,
            )
        )
    return out


def judge(
    cik: str,
    filings: Sequence[PeriodicFiling],
    carried: Callable[[str], bool],
    *,
    asof: date,
    fetched_at: datetime,
) -> FundamentalsLag | None:
    """The rule (module docstring), PURE: ``filings`` = ``periodic_filings`` of the cached index; ``carried``
    answers "does SEC's companyfacts hold a row from this accession?"; ``fetched_at`` = when our cached
    companyfacts was written. ``None`` = nothing to flag (current, too new to judge, or our copy is the one
    that may be behind)."""
    originals = [f for f in filings if f.form in PERIODIC and f.filed <= asof]
    if not originals:
        return None
    latest = max(originals, key=lambda f: (f.filed, f.period))
    if latest.filed > asof - timedelta(days=MIN_AGE_DAYS):
        return None
    # ``periodic_filings`` always dates a row (its filing-date bound when the listed time is unreadable)
    if latest.accepted is None or fetched_at < latest.accepted + timedelta(
        seconds=ACCEPTANCE_GRACE_S
    ):
        return None
    listed = [f for f in filings if f.filed <= asof]
    # the period's own reports first (the common answer is "carried" — one lookup), then the newest older one
    if any(carried(f.accession) for f in listed if f.period >= latest.period):
        return None
    older = sorted(
        (f for f in listed if f.period < latest.period), key=lambda f: f.period, reverse=True
    )
    return FundamentalsLag(
        cik=cik,
        form=latest.form,
        accession=latest.accession,
        filed=latest.filed,
        period=latest.period,
        last_period=next((f.period for f in older if carried(f.accession)), None),
        primary_doc=latest.primary_doc,
    )


# --- the disk read (memoized per file version) ------------------------------------------------------------

_LOCK = threading.Lock()
# path -> ((mtime_ns, size), parsed filings)
_SUBS_MEMO: dict[str, tuple[tuple[int, int], list[PeriodicFiling]]] = {}
# path -> ((mtime_ns, size), {accession: carried?})
_FACTS_MEMO: dict[str, tuple[tuple[int, int], dict[str, bool]]] = {}


def _sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _cached_filings(path: Path) -> list[PeriodicFiling] | None:
    sig = _sig(path)
    if sig is None:
        return None
    key = str(path)
    with _LOCK:
        hit = _SUBS_MEMO.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    filings = periodic_filings(doc if isinstance(doc, dict) else None)
    with _LOCK:
        _SUBS_MEMO[key] = (sig, filings)
    return filings


def _carried(path: Path, sig: tuple[int, int]) -> Callable[[str], bool]:
    """ "Does this companyfacts document hold a row from ``accession``?" — answered from the file's bytes,
    memoized per file version. The file is MAPPED, not read: ``find`` stops at the first match, and a
    company's newest filings sit in its first concept's newest rows near the top of the document, so a carried
    report costs a few pages; only a report SEC does not carry scans the whole file. An unreadable file
    answers ``False`` (no claim either way is made from it: ``read_lag`` only flags on a real scan).
    """
    key = str(path)

    def ask(accession: str) -> bool:
        with _LOCK:
            hit = _FACTS_MEMO.get(key)
            if hit is not None and hit[0] == sig and accession in hit[1]:
                return hit[1][accession]
        needle = b'"' + accession.encode("ascii", "ignore") + b'"'
        try:
            with path.open("rb") as fh:
                if sig[1] == 0:
                    found = False
                else:
                    with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        found = mm.find(needle) >= 0
        except (OSError, ValueError):
            raise _Unreadable from None
        with _LOCK:
            hit = _FACTS_MEMO.get(key)
            answers = dict(hit[1]) if hit is not None and hit[0] == sig else {}
            answers[accession] = found
            _FACTS_MEMO[key] = (sig, answers)
        return found

    return ask


class _Unreadable(Exception):
    """The cached companyfacts could not be read mid-judgment (replaced or removed under us)."""


def read_lag(
    cik: str | int, *, asof: date, cache_dir: Path | None = None
) -> FundamentalsLag | None:
    """One company's reading from the EDGAR cache on disk (``None`` = nothing to flag, or nothing cached to
    judge on). Never a network request."""
    root = cache_dir or DEFAULT_EDGAR_CACHE
    cik10 = f"{int(cik):010d}"
    name = f"CIK{cik10}.json"
    facts_path = root / "companyfacts" / name
    sig = _sig(facts_path)
    if sig is None:
        return None  # no cached companyfacts: nothing says what SEC carries
    filings = _cached_filings(root / "submissions" / name)
    if not filings:
        return None
    fetched_at = datetime.fromtimestamp(sig[0] / 1e9, tz=timezone.utc)
    try:
        return judge(cik10, filings, _carried(facts_path, sig), asof=asof, fetched_at=fetched_at)
    except _Unreadable:
        return None  # a file we could not read says nothing about SEC's data


__all__ = [
    "AMENDMENTS",
    "DEFAULT_EDGAR_CACHE",
    "FundamentalsLag",
    "MIN_AGE_DAYS",
    "PERIODIC",
    "PeriodicFiling",
    "judge",
    "periodic_filings",
    "read_lag",
]

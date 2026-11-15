"""BACKGROUND EXTRACTION (V2 W3a): the attempt record's gate and the runner that keeps platform values current.

``docs/PLATFORM_VALUES.md`` ("Background extraction"). Three pieces, no caller yet (the nightly leg, the backfill
CLI and the onboarding step are later PRs):

- **The gate (pure).** ``inputs_key`` names exactly what the extractor reads: the company's CIK plus the newest
  10-Q, 10-K, 20-F and 40-F (``PERIODIC_FORMS``, exact match, like ``ingest.edgar.submissions.filings_of``).
  ``due`` decides from a company's attempts and its stored filing index whether it is due: ``never`` (no attempt),
  ``new_filing`` (a periodic filing newer than the ones its last attempt read, public for ``hold_h`` hours, so
  companyfacts has caught up), or ``retry`` (the last attempt failed fewer than ``retry_max`` times in a row on the
  same key, or another writer held the company). Otherwise it is not due: ``up to date``, ``held`` or ``waiting``.
  "Public" is the SEC acceptance, else the end of the filing date in ET (late, never early); a filing not public
  at ``now`` never makes a company due (CLAUDE.md invariant #1).
- **The per-company step.** ``extract_one`` rolls back any open transaction, FETCHES with the company free
  (through the client the caller passes in), then calls the machine writer (``platform_values.machine``, which takes
  the company lock), then appends exactly ONE attempt row: ``done``, ``error`` (a bounded one-line reason, never
  the raw exception), ``busy`` or ``skipped``. It never raises for a company.
- **The run loop.** ``run_extraction`` walks a work list (``work_list``: due companies, most cards first, through
  ``platform_values.cards.card_counts``) under a count and a time budget, fail-open per company, honoring the kill
  switch and a ``should_stop`` check, and returns an ``ExtractionRun`` summary for a run artifact.

Nothing here is a call input. It lives in ``platform_values/``, never in ``domain/`` (the editions fingerprint hashes
``domain/``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, NamedTuple

from ingest.edgar.acceptance import filing_date_bound

# The forms the extractor chooses from (``ingest.edgar.extract``: the 10-Q / 10-K periodic path, else the 20-F /
# 40-F annual path). Exact match: an amendment (10-Q/A) or a current report (8-K, 6-K) is never read.
PERIODIC_FORMS: tuple[str, ...] = ("10-Q", "10-K", "20-F", "40-F")

DueReason = Literal["never", "new_filing", "retry"]
AttemptStatus = Literal["done", "error", "busy", "skipped"]
RunKind = Literal["nightly", "onboarding", "backfill", "steward", "get_data"]


@dataclass(frozen=True)
class PeriodicFiling:
    """One periodic filing of a company, from either source: the stored filing index (``fact_filing_index``) or
    the submissions JSON at extraction time. ``accepted`` is the TRUSTED SEC acceptance, or None."""

    form: str
    accession: str
    filed: date
    accepted: datetime | None = None

    @property
    def public_at(self) -> datetime:
        """When it became public: the acceptance, else the end of the filing date in ET (never early)."""
        return self.accepted if self.accepted is not None else filing_date_bound(self.filed)

    def _order(self) -> tuple:
        return (self.filed, self.public_at, self.accession)


@dataclass(frozen=True)
class AttemptRow:
    """The part of a stored attempt the gate reads."""

    status: str
    inputs_key: str
    recorded_at: datetime


class Due(NamedTuple):
    """``reason`` None = not due; ``note`` says why in a word or two (``up to date``, ``held``, ``waiting``)."""

    reason: DueReason | None
    note: str


def _norm_cik(cik: str | int | None) -> str:
    text = str(cik or "").strip()
    return str(int(text)) if text.isdigit() else text


def _latest(filings: Iterable[PeriodicFiling]) -> dict[str, PeriodicFiling]:
    out: dict[str, PeriodicFiling] = {}
    for f in filings:
        if f.form not in PERIODIC_FORMS:
            continue
        cur = out.get(f.form)
        if cur is None or f._order() > cur._order():
            out[f.form] = f
    return out


def inputs_key(cik: str | int | None, filings: Iterable[PeriodicFiling]) -> str:
    """``"<cik>|10-Q:<acc>|10-K:<acc>|20-F:<acc>|40-F:<acc>"`` (an empty part when the company has none). Pure;
    the same answer from the stored filing index and from the submissions JSON."""
    latest = _latest(filings)
    parts = [f"{form}:{latest[form].accession if form in latest else ''}" for form in PERIODIC_FORMS]
    return "|".join([_norm_cik(cik), *parts])


def parse_inputs_key(key: str) -> tuple[str, dict[str, str]]:
    """``inputs_key``'s inverse: ``(cik, {form: accession or ""})``."""
    head, *parts = (key or "").split("|")
    accs = dict.fromkeys(PERIODIC_FORMS, "")
    for part in parts:
        form, _, acc = part.partition(":")
        if form in accs:
            accs[form] = acc
    return head, accs


def _new_filings(ref_key: str, filings: Sequence[PeriodicFiling]) -> list[PeriodicFiling]:
    """The newest filing of each form that is NEWER than the one the attempt keyed on ``ref_key`` read. An
    attempt that read a filing the stored index does not have yet read a NEWER one (the index lags a night)."""
    _, read = parse_inputs_key(ref_key)
    by_acc = {(f.form, f.accession): f for f in filings}
    new: list[PeriodicFiling] = []
    for form, f in _latest(filings).items():
        seen_acc = read.get(form, "")
        if f.accession == seen_acc:
            continue
        if seen_acc:
            seen = by_acc.get((form, seen_acc))
            if seen is None or f._order() <= seen._order():
                continue
        new.append(f)
    return new


def due(
    attempts: Sequence[AttemptRow],
    filings: Iterable[PeriodicFiling],
    *,
    cik: str | int | None,
    now: datetime,
    hold_h: float,
    retry_max: int,
) -> Due:
    """Is the company due for extraction? ``attempts`` newest first; ``filings`` its stored periodic filings
    (any order). Pure. The rules are the module docstring's."""
    if not attempts:
        return Due("never", "never attempted")
    if attempts[0].status == "busy":
        return Due("retry", "busy")  # another writer held it: not a failure, take it next time
    ref = next(a for a in attempts if a.status != "busy")
    public = [f for f in filings if f.form in PERIODIC_FORMS and f.public_at <= now]
    ref_cik, _ = parse_inputs_key(ref.inputs_key)
    if _norm_cik(cik) != ref_cik:
        return Due("new_filing", "cik changed")
    new = _new_filings(ref.inputs_key, public)
    if new:
        newest = max(f.public_at for f in new)
        if now - newest < timedelta(hours=hold_h):
            return Due(None, "held")  # companyfacts lags acceptance: extract it on a later night
        return Due("new_filing", "new filing")
    if ref.status == "error":
        streak = 0
        for a in attempts:
            if a.status == "busy":
                continue
            if a.status != "error" or a.inputs_key != ref.inputs_key:
                break
            streak += 1
        if streak < retry_max:
            return Due("retry", "failed")
        return Due(None, "waiting")  # for a new filing or the steward
    return Due(None, "up to date")


def filings_from_rows(rows: Iterable[Mapping]) -> list[PeriodicFiling]:
    """Rows with ``form``, ``accession``, ``filed`` (a date) and ``accepted`` (a trusted instant or None)."""
    out = []
    for r in rows:
        accepted = r.get("accepted")
        if accepted is not None and accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=timezone.utc)
        out.append(PeriodicFiling(r["form"], r["accession"], r["filed"], accepted))
    return out

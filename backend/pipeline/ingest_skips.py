"""Q18 — the cross-pass diff of skipped FILINGS: known ones become one count per kind, new ones stay loud.

The O1 name-error mechanism (``pipeline/name_errors.py`` + ``cron_run_log.previous_name_errors``), mirrored
for the per-filing skips the filing legs tolerate (``ingest.skips``). The pass reads the skip baseline
BEFORE its loop — each thesis's skipped filings from the newest finished, live pass that COVERED it, the
per-thesis carry-forward ``cron_run_log.previous_ingest_skips`` shares with O1 — hands
the keys to the legs so a filing that skipped the same way last pass does not print its line again, then —
after the loop, before its own artifact is written — computes this diff for the artifact and the summary
lines.

Per kind the artifact carries ``total`` (distinct filings skipped this pass), ``known`` (of those, skipped on
the previous evaluated pass too), ``new`` (the rest, each with its security and reason), ``previous_total``
and ``delta`` (``total - previous_total``, so a jump in the known inventory is visible even when nothing is
"new"), and ``accessions`` (the whole inventory — the next pass's baseline). ``None`` previous (no evaluated
artifact yet: the first pass after this ships) makes everything new, so the inventory prints ONCE, loudly.

Pure: no DB, no network, no clock, no file I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ingest.skips import SKIP_KINDS, IngestSkip, dedup_skips, skip_key

# A previous pass's inventory: kind -> the accessions skipped under it.
SkipInventory = Mapping[str, set[str]]


def known_keys(previous: SkipInventory | None) -> set[str] | None:
    """The ``<kind>:<accession>`` keys the legs mute this pass — ``None`` (mute nothing) without a
    baseline."""
    if previous is None:
        return None
    return {skip_key(kind, acc) for kind, accs in previous.items() for acc in accs}


@dataclass(frozen=True)
class SkipKindSummary:
    """One kind's line of the diff (see the module docstring for each field)."""

    kind: str
    total: int
    known: int
    new: tuple[IngestSkip, ...]
    previous_total: int | None
    accessions: tuple[str, ...]

    @property
    def delta(self) -> int | None:
        return None if self.previous_total is None else self.total - self.previous_total

    def payload(self) -> dict:
        return {
            "total": self.total,
            "known": self.known,
            "new": [
                {
                    "accession": s.accession,
                    "security_id": str(s.security_id),
                    "ticker": s.ticker,
                    "reason": s.reason,
                }
                for s in self.new
            ],
            "previous_total": self.previous_total,
            "delta": self.delta,
            "accessions": list(self.accessions),
        }


@dataclass(frozen=True)
class SkipDiff:
    """The pass's skip diff. ``evaluated`` = a LIVE pass (the only kind that is a baseline — a ``--no-live``
    pass reads cache misses, not tonight's filings). The default is the not-evaluated empty diff, so an
    outcome built without one (a test, an old caller) writes an honest "not evaluated" block."""

    evaluated: bool = False
    kinds: tuple[SkipKindSummary, ...] = field(default_factory=tuple)

    @property
    def new(self) -> tuple[IngestSkip, ...]:
        return tuple(s for k in self.kinds for s in k.new)

    def payload(self) -> dict:
        return {
            "ingest_skips_evaluated": self.evaluated,
            "ingest_skips": {k.kind: k.payload() for k in self.kinds},
        }

    def report_lines(self) -> list[str]:
        """The quiet summary, one line per kind that skipped anything (lowercase: the known-errors
        convention). The new filings already printed one line each during the ingest; nothing here repeats
        them. A clean night prints nothing."""
        out: list[str] = []
        for k in self.kinds:
            if not k.total:
                continue
            if not self.evaluated:
                out.append(
                    f"{k.kind}: {k.total} filing(s) skipped — not diffed (a --no-live pass is never a "
                    "baseline); accessions in the run artifact"
                )
                continue
            delta = "" if k.delta is None else f", {k.delta:+d} vs the last pass"
            first = " (no baseline yet — each printed above)" if k.previous_total is None else ""
            out.append(
                f"{k.kind}: {k.known} known (skipped the same way last pass; quiet), "
                f"{len(k.new)} new{first}{delta} — every accession in the run artifact"
            )
        return out


def diff_skips(
    skips: Iterable[IngestSkip], previous: SkipInventory | None, *, evaluated: bool
) -> SkipDiff:
    """This pass's skips (deduped across theses — one filing is one row) against the previous evaluated
    pass's inventory. Every kind gets a summary, zero or not, so the artifact is self-describing."""
    current = dedup_skips(skips)
    kinds: list[SkipKindSummary] = []
    for kind in SKIP_KINDS:
        rows = sorted((s for s in current.values() if s.kind == kind), key=lambda s: s.accession)
        before = None if previous is None else set(previous.get(kind, set()))
        new = tuple(rows) if before is None else tuple(s for s in rows if s.accession not in before)
        kinds.append(
            SkipKindSummary(
                kind=kind,
                total=len(rows),
                known=len(rows) - len(new),
                new=new,
                previous_total=None if before is None else len(before),
                accessions=tuple(s.accession for s in rows),
            )
        )
    return SkipDiff(evaluated=evaluated, kinds=tuple(kinds))

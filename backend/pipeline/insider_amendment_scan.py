"""The nightly Form 4/A inventory (R8-A / R8-B): which amendments are OWED the operator's decision, which of those
are NEW since the last pass — so each pages ONCE (the T1/T2 page-once machinery) and the known ones stay quiet —
and how many the SYSTEM applied this pass (one quiet count line).

R8-B (operator decision 2026-10-20): a Form 4/A the linker resolves to a definite original is the filer's own
legal correction and is APPLIED BY THE SYSTEM — at ingest (``ingest.edgar.form4a``) or, for what a pre-R8-B
ingest left pending, by the idempotent SWEEP the pass runs BEFORE its per-thesis loop
(``run_amendment_sweep`` -> ``repositories.insider_amendment_repo.sweep``, so tonight's calls already read the
applied state). Neither pages: the cron report gets ONE quiet line, "Form 4/A: N applied automatically (filer
corrections), M owed your decision". What is OWED is only what the system cannot resolve AND a call can read:
an AMBIGUOUS link (several differing candidate originals) whose changed signal rows sit inside the review
horizon (``insider_amendment_review_horizon_days`` — derived: backtest window + the longest detector read
horizon, an UNRESOLVED row match (a buy/sell a partial / addition / restatement re-reports that matches no
original by shape and whose filer's words neither fix nor add — ``domain.insider_amendments.match_rows``) or a
HELD void (retraction language against an original of several trades — ``void_reading``) inside the horizon,
plus an operator-linked one awaiting its verdict and one the operator took back with an Undo. Either
kind outside the horizon is "unresolved — historical" on Admin: no call reads those rows, never paged.

URGENCY. An owed item whose changed signal rows sit inside a signal's live read horizon at the pass's asof (the
detectors' own declared horizons — ``signals.horizons.insider_signal_codes``) can move TONIGHT's call: it is
listed first, and the page NAMES every such item (never collapsed into a count); the other new owed items are
named after them up to ``PAGE_NAME_CAP`` names in all, the rest counted ("+N more in Admin").

THE BASELINE. The inventory is read WHOLE from the amendment tables every pass (every owed amendment of the
active basket union — ``repositories.insider_amendment_repo.pending_keys``), not from what that pass ingested.
So, exactly like T4's split-basis scan, the newest FINISHED, LIVE pass that evaluated it is a complete
baseline — a ``--catch-up`` that skipped theses included (it read the same tables). ``None`` (no evaluated
pass yet) pages the whole inventory ONCE; thereafter only a key absent from the previous pass's inventory
pages. A decision (or its Undo) changes the inventory too: an owed item the operator linked-and-decided leaves
it, and an Undo that makes it owed again pages it once more — the operator asked for it back. (The first pass
after R8-B deploys pages nothing new: the owed set is a subset of R8-A's pending keys, which already paged.)

FAIL-OPEN for the pass, LOUD for the operator (the split scan's shape): a scan or sweep that raises returns
``evaluated=False`` / ``error`` and the pass goes on; a ``--no-live`` pass neither sweeps nor scans and is never
a baseline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import psycopg

BLOCK = "insider_amendments"

# the most items one page NAMES: every in-window item is named regardless; the other owed items fill the rest
PAGE_NAME_CAP = 8

_UNION = """
    SELECT DISTINCT bm.security_id
      FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id
     WHERE t.archived_at IS NULL AND bm.security_id IS NOT NULL
"""


@dataclass(frozen=True)
class PendingAmendment:
    """One amendment owed a decision, as the artifact records it."""

    key: str  # "<security_id>:<accession>"
    security_id: str
    ticker: str | None
    accession: str
    disposition: str
    suppressing: tuple[str, ...]
    touches_buy: bool
    # the signal codes whose changed rows sit inside a signal's live read horizon at the pass's asof — an item
    # that can move TONIGHT's call (the urgency: listed first, and always named on the page)
    in_window: tuple[str, ...] = ()
    # R8-B — an AMBIGUOUS link's candidate originals (the operator picks one); 0 for any other kind
    candidates: int = 0
    # R8-B — an unresolved row match: the filer's words neither fix nor add a leftover buy/sell
    unresolved: bool = False
    # R8-B — a zero-row 4/A with retraction language against an original of several trades (the system held it)
    held: bool = False

    @property
    def label(self) -> str:
        who = self.ticker or self.security_id[:8]
        if self.candidates:
            held = f" — {self.candidates} candidate originals; pick the one it amends (nothing changes until you do)"
        elif self.unresolved:
            held = (
                " — a buy/sell it re-reports matches no original and its words neither fix nor add: restated, "
                "or an added trade? (the original stands until you decide)"
            )
        elif self.held:
            held = (
                " — it retracts a trade, but the original holds several: which one is your call (the original "
                "stands until you decide)"
            )
        elif self.suppressing:
            held = f" — suppressing {', '.join(self.suppressing)} until you decide"
        else:
            held = " — no effect until you decide"
        notes = []
        if self.touches_buy:
            notes.append("touches a code-P buy")
        if self.in_window:
            notes.append(f"in tonight's {'/'.join(self.in_window)} window")
        tail = f" ({'; '.join(notes)})" if notes else ""
        return f"{who} {self.accession}: {self.disposition}{held}{tail}"

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "security_id": self.security_id,
            "ticker": self.ticker,
            "accession": self.accession,
            "disposition": self.disposition,
            "suppressing": list(self.suppressing),
            "touches_buy": self.touches_buy,
            "in_window": list(self.in_window),
            "candidates": self.candidates,
            "unresolved": self.unresolved,
            "held": self.held,
            "label": self.label,
        }


def page_named(new: tuple[PendingAmendment, ...], cap: int = PAGE_NAME_CAP) -> set[str]:
    """The keys of the NEW owed items a page NAMES: every in-window one (never collapsed into a count), then the
    others in inventory order (urgency) until ``cap`` names in all."""
    named = [p.key for p in new if p.in_window]
    rest = [p.key for p in new if not p.in_window]
    named.extend(rest[: max(0, cap - len(named))])
    return set(named)


@dataclass(frozen=True)
class AmendmentScan:
    """One pass's amendment inventory outcome — what rides the artifact (``payload``), the printed report
    and the health page (``new_labels``)."""

    evaluated: bool = False
    pending: tuple[PendingAmendment, ...] = ()
    new: tuple[PendingAmendment, ...] = ()
    baseline: bool = (
        False  # a previous evaluated pass existed (False on the first night: all is new)
    )
    error: str | None = None
    duration_s: float = 0.0
    # R8-B — the SYSTEM applications recorded during this pass (the sweep + tonight's ingest), ``{basis: n}``
    applied: dict[str, int] = field(default_factory=dict)
    # R8-B — the sweep's own outcome (``run_amendment_sweep``): how many it applied, or why it failed
    swept: int = 0
    sweep_error: str | None = None

    @property
    def known(self) -> int:
        return len(self.pending) - len(self.new)

    @property
    def applied_total(self) -> int:
        """System APPLICATIONS this pass (the filer's corrections, and the no-signal-effect ones)."""
        return sum(
            n
            for b, n in self.applied.items()
            if b in ("system:filer-correction", "system:no-signal-effect")
        )

    @property
    def kept_total(self) -> int:
        """Zero-row 4/As the system kept as filed this pass (their text retracts nothing)."""
        return self.applied.get("system:no-retraction", 0)

    @property
    def _named(self) -> set[str]:
        return page_named(self.new)

    @property
    def new_labels(self) -> tuple[str, ...]:
        """The NEW owed items the page NAMES — every in-window one first, then the others up to the cap."""
        named = self._named
        return tuple(p.label for p in self.new if p.key in named)

    @property
    def new_more(self) -> int:
        """The NEW owed items the page only COUNTS ("+N more in Admin")."""
        named = self._named
        return sum(1 for p in self.new if p.key not in named)

    def payload(self) -> dict:
        named = self._named
        return {
            BLOCK: {
                "evaluated": self.evaluated,
                "pending": [p.to_json() for p in self.pending],
                "new": [
                    {
                        "key": p.key,
                        "label": p.label,
                        "in_window": list(p.in_window),
                        "named": p.key in named,
                    }
                    for p in self.new
                ],
                "baseline": self.baseline,
                "error": self.error,
                "duration_s": round(self.duration_s, 3),
                "applied": dict(self.applied),
                "swept": self.swept,
                "sweep_error": self.sweep_error,
            }
        }


def scan_pending(conn: psycopg.Connection, *, asof: date | None) -> tuple[PendingAmendment, ...]:
    """Every amendment of the active basket union OWED an operator decision — read-only — URGENCY-ordered: the
    in-window ones first (their changed signal rows sit inside a signal's live read horizon at ``asof``, from
    the detectors' own horizons), then buy-touching, then suppressing, then by name."""
    from repositories import insider_amendment_repo  # lazy: keeps the cron_run_log import light

    with conn.cursor() as cur:
        cur.execute(_UNION)
        ids = [r["security_id"] for r in cur.fetchall()]
    if not ids:
        return ()
    out: list[PendingAmendment] = []
    for key, it in insider_amendment_repo.pending_keys(conn, security_ids=ids, asof=asof).items():
        eff = it.effective
        ambiguous = eff.disposition == "ambiguous" and eff.link_decision_id is None
        out.append(
            PendingAmendment(
                key=key,
                security_id=str(it.security_id),
                ticker=it.ticker,
                accession=it.accession,
                disposition=eff.disposition,
                suppressing=tuple(sorted(set(eff.suppress))),
                touches_buy=it.touches_buy,
                in_window=it.in_window,
                candidates=len(eff.candidate_accessions) if ambiguous else 0,
                unresolved=it.unresolved and not ambiguous,
                held=it.held,
            )
        )
    out.sort(
        key=lambda p: (
            not p.in_window,
            not p.touches_buy,
            not p.suppressing,
            p.ticker or "",
            p.accession,
        )
    )
    return tuple(out)


def _err(e: Exception) -> str:
    msg = " ".join(str(e).split())[:200]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


@dataclass(frozen=True)
class SweepOutcome:
    """The pre-loop sweep's outcome as the pass carries it to the scan (and the artifact)."""

    applied: int = 0
    by_basis: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    ran: bool = False


def run_amendment_sweep(
    *, allow_live: bool, connect: Callable[[], psycopg.Connection]
) -> SweepOutcome:
    """R8-B — the pass's SWEEP, run BEFORE the per-thesis loop so tonight's calls read the applied state: apply
    every resolvable Form 4/A a pre-R8-B ingest left without a decision (``insider_amendment_repo.sweep`` — one
    system ``apply`` each, ``recorded_at = now``; idempotent: re-running appends nothing). Its own connection
    and transaction (committed on success, rolled back on a fault). Never raises (a fault rides ``error`` and
    the pass goes on — the amendments simply stay pending, voids suppressing, until a later pass); a
    ``--no-live`` pass does not sweep (a cache-only dev run writes no decisions)."""
    if not allow_live:
        return SweepOutcome()
    from repositories import insider_amendment_repo  # lazy: the detector registry sits above it

    try:
        conn = connect()
        try:
            res = insider_amendment_repo.sweep(conn)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a sweep fault is reported, never a failed pass
        return SweepOutcome(error=_err(e), ran=True)
    return SweepOutcome(applied=len(res.applied), by_basis=dict(res.by_basis), ran=True)


def run_amendment_scan(
    *,
    asof: date | None = None,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
    previous: Callable[[], set[str] | None],
    clock: Callable[[], float],
    since: datetime | None = None,
    sweep: SweepOutcome | None = None,
) -> AmendmentScan:
    """The nightly unit: read the owed inventory on its own read-only connection (urgency judged at the pass's
    ``asof``) and — with ``since`` (the pass's start) — count the SYSTEM applications recorded during the
    pass, then diff against the previous evaluated pass. ``sweep`` (the pre-loop sweep's outcome) rides the
    artifact. Never raises (a fault rides ``error``); ``--no-live`` does not scan."""
    if not allow_live:
        return AmendmentScan()
    sw = sweep or SweepOutcome()
    t0 = clock()
    applied: dict[str, int] = {}
    try:
        conn = connect()
        try:
            conn.read_only = True  # the scan reads; structurally it cannot write a row
            pending = scan_pending(conn, asof=asof)
            if since is not None:
                from repositories.insider_amendment_repo import system_applied_since

                applied = system_applied_since(conn, since)
            conn.rollback()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        return AmendmentScan(
            error=_err(e),
            duration_s=clock() - t0,
            swept=sw.applied,
            sweep_error=sw.error,
        )
    prior = previous()
    new = tuple(p for p in pending if prior is None or p.key not in prior)
    return AmendmentScan(
        evaluated=True,
        pending=pending,
        new=new,
        baseline=prior is not None,
        duration_s=clock() - t0,
        applied=applied,
        swept=sw.applied,
        sweep_error=sw.error,
    )


def keys_of(doc: Mapping) -> set[str]:
    """The pending keys one artifact recorded. Fail-open: a malformed row is skipped."""
    block = doc.get(BLOCK)
    if not isinstance(block, Mapping):
        return set()
    return {
        str(r["key"]) for r in block.get("pending") or [] if isinstance(r, Mapping) and r.get("key")
    }


def previous_amendment_keys(*, base_dir: Path | None = None) -> set[str] | None:
    """The pending keys of the newest LIVE, FINISHED pass whose scan was evaluated — the baseline (see the
    module docstring for why the newest such pass is complete). ``None`` when none exists yet. Fail-open per
    artifact. Pure file read."""
    from pipeline.cron_run_log import is_finished, list_run_logs  # lazy: cron_run_log imports this

    for doc in list_run_logs(base_dir=base_dir):
        block = doc.get(BLOCK)
        if (
            doc.get("mode") != "live"
            or not isinstance(block, Mapping)
            or not block.get("evaluated")
            or not is_finished(doc)
        ):
            continue
        return keys_of(doc)
    return None


def _new_rows(doc: Mapping) -> list[Mapping]:
    block = doc.get(BLOCK)
    if not isinstance(block, Mapping):
        return []
    return [r for r in block.get("new") or [] if isinstance(r, Mapping) and r.get("label")]


def _was_named(r: Mapping) -> bool:
    """Did the page NAME this new row? R8-B artifacts record it (``named``); an older artifact named exactly
    its in-window rows."""
    return bool(r["named"]) if "named" in r else bool(r.get("in_window"))


def new_labels_of(doc: Mapping) -> tuple[str, ...]:
    """The NEW labels one artifact NAMED on its page — the Admin history re-derives the run's page from these.
    ``()`` for an artifact without the block."""
    return tuple(str(r["label"]) for r in _new_rows(doc) if _was_named(r))


def new_more_of(doc: Mapping) -> int:
    """How many NEW items one artifact only COUNTED on its page."""
    return sum(1 for r in _new_rows(doc) if not _was_named(r))


def count_line(scan: AmendmentScan) -> str | None:
    """R8-B — the ONE quiet count line: the system applications recorded this pass and the owed inventory
    (``None`` when both are zero — nothing at all on a clean night)."""
    owed = len(scan.pending)
    if not scan.applied_total and not scan.kept_total and not owed:
        return None
    kept = (
        f", {scan.kept_total} kept as filed (their text retracts nothing)"
        if scan.kept_total
        else ""
    )
    return (
        f"Form 4/A: {scan.applied_total} applied automatically (filer corrections){kept}, "
        f"{owed} owed your decision (quiet)"
    )


def report_lines(scan: AmendmentScan) -> list[str]:
    """The cron's printed block: a failed sweep or scan LOUD, every NEW owed item the page names LOUD (one line
    each) with ONE count line for the rest, then ONE quiet line counting the system applications and the owed
    inventory — nothing at all on a clean or un-scanned night (loudness marks the exception). A system
    application is never loud."""
    lines: list[str] = []
    if scan.sweep_error:
        lines.append(
            f"FORM 4/A SWEEP FAILED — {scan.sweep_error} (pre-R8-B amendments stay pending until a later "
            "pass; a pending void still suppresses its original)"
        )
    if scan.error:
        return [
            *lines,
            f"FORM 4/A INVENTORY FAILED — {scan.error} (owed amendments were not checked)",
        ]
    if not scan.evaluated:
        return lines
    if scan.new:
        lines.append(
            "FORM 4/A AMENDMENTS — NEW and owed your decision (Admin, Insider amendments — the system could "
            "not tell which filing each one amends):"
        )
        lines.extend(f"  {label}" for label in scan.new_labels)
        if scan.new_more:
            lines.append(f"  +{scan.new_more} more — listed in Admin")
    quiet = count_line(scan)
    if quiet:
        lines.append(quiet)
    return lines

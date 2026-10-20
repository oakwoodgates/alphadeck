"""The nightly Form 4/A inventory (R8-A): which amendments are OWED the operator's decision, and which of those
are NEW since the last pass — so each pages ONCE (the T1/T2 page-once machinery) and the known ones stay quiet.

The Form 4 leg stores and links every 4/A as it arrives (``ingest.edgar.form4a``); a clean 1:1 correction
applies itself, and so does every routed amendment that changes no row a call signal reads (the signal-effect
rule, 2026-10-18 — resolved by the system, listed on Admin as "applied — no signal effect"); neither pages.
What is OWED a decision — a signal-touching void (it suppresses the original while it waits), duplicates set,
partial / consolidation / addition / restatement / field change, and EVERY ambiguous link — is listed on the
Admin "Insider amendments" panel, and this scan pages the NEW ones through the run's health event.

URGENCY. An item whose changed signal rows sit inside a signal's live read horizon at the pass's asof (the
detectors' own declared horizons — ``signals.horizons.insider_signal_codes``) can move TONIGHT's call: it is
listed first and the page NAMES every such item; the rest are counted ("+N more in Admin").

THE BASELINE. The inventory is read WHOLE from the amendment tables every pass (every pending amendment of the
active basket union — ``repositories.insider_amendment_repo.pending_keys``), not from what that pass ingested.
So, exactly like T4's split-basis scan, the newest FINISHED, LIVE pass that evaluated it is a complete
baseline — a ``--catch-up`` that skipped theses included (it read the same tables). The per-thesis
carry-forward (``cron_run_log.carried_inventory``) exists for inventories a pass builds only from the theses
it covered; this one has nothing to carry. ``None`` (no evaluated pass yet — the first night after deploy)
pages the whole inventory ONCE; thereafter only a key absent from the previous pass's inventory pages. A
decision (or its Undo) changes the inventory too: a pending item the operator ratified leaves it, and an
Undo that makes it pending again pages it once more — the operator asked for it back.

FAIL-OPEN for the pass, LOUD for the operator (the split scan's shape): a scan that raises returns
``evaluated=False`` with the ``error``; a ``--no-live`` pass does not scan and is never a baseline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

BLOCK = "insider_amendments"

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

    @property
    def label(self) -> str:
        who = self.ticker or self.security_id[:8]
        held = (
            f" — suppressing {', '.join(self.suppressing)} until you decide"
            if self.suppressing
            else " — no effect until you decide"
        )
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
            "label": self.label,
        }


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

    @property
    def known(self) -> int:
        return len(self.pending) - len(self.new)

    @property
    def new_labels(self) -> tuple[str, ...]:
        """The NEW items the page NAMES — every in-window one (never collapsed into a count)."""
        return tuple(p.label for p in self.new if p.in_window)

    @property
    def new_more(self) -> int:
        """The NEW items the page only COUNTS ("+N more in Admin") — the ones outside tonight's windows."""
        return sum(1 for p in self.new if not p.in_window)

    def payload(self) -> dict:
        return {
            BLOCK: {
                "evaluated": self.evaluated,
                "pending": [p.to_json() for p in self.pending],
                "new": [
                    {"key": p.key, "label": p.label, "in_window": list(p.in_window)}
                    for p in self.new
                ],
                "baseline": self.baseline,
                "error": self.error,
                "duration_s": round(self.duration_s, 3),
            }
        }


def scan_pending(conn: psycopg.Connection, *, asof: date | None) -> tuple[PendingAmendment, ...]:
    """Every amendment of the active basket union owed an operator decision — read-only — URGENCY-ordered: the
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
        out.append(
            PendingAmendment(
                key=key,
                security_id=str(it.security_id),
                ticker=it.ticker,
                accession=it.accession,
                disposition=it.effective.disposition,
                suppressing=tuple(sorted(set(it.effective.suppress))),
                touches_buy=it.touches_buy,
                in_window=it.in_window,
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


def run_amendment_scan(
    *,
    asof: date | None = None,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
    previous: Callable[[], set[str] | None],
    clock: Callable[[], float],
) -> AmendmentScan:
    """The nightly unit: read the inventory on its own read-only connection (urgency judged at the pass's
    ``asof``), then diff against the previous evaluated pass. Never raises (a fault rides ``error``);
    ``--no-live`` does not scan."""
    if not allow_live:
        return AmendmentScan()
    t0 = clock()
    try:
        conn = connect()
        try:
            conn.read_only = True  # the scan reads; structurally it cannot write a row
            pending = scan_pending(conn, asof=asof)
            conn.rollback()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        msg = " ".join(str(e).split())[:200]
        return AmendmentScan(
            error=f"{type(e).__name__}: {msg}" if msg else type(e).__name__,
            duration_s=clock() - t0,
        )
    prior = previous()
    new = tuple(p for p in pending if prior is None or p.key not in prior)
    return AmendmentScan(
        evaluated=True,
        pending=pending,
        new=new,
        baseline=prior is not None,
        duration_s=clock() - t0,
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


def new_labels_of(doc: Mapping) -> tuple[str, ...]:
    """The NEW labels one artifact NAMED on its page (the in-window ones) — the Admin history re-derives the
    run's page from these. ``()`` for an artifact without the block."""
    return tuple(str(r["label"]) for r in _new_rows(doc) if r.get("in_window"))


def new_more_of(doc: Mapping) -> int:
    """How many NEW items one artifact only COUNTED on its page (outside tonight's windows)."""
    return sum(1 for r in _new_rows(doc) if not r.get("in_window"))


def report_lines(scan: AmendmentScan) -> list[str]:
    """The cron's printed block: every NEW in-window item LOUD (one line each), the other new ones ONE count
    line, the known ones ONE quiet line, a failed scan LOUD, nothing at all on a clean or un-scanned night —
    loudness marks the exception."""
    if scan.error:
        return [f"FORM 4/A INVENTORY FAILED — {scan.error} (pending amendments were not checked)"]
    if not scan.evaluated or not scan.pending:
        return []
    lines: list[str] = []
    if scan.new:
        lines.append(
            "FORM 4/A AMENDMENTS — NEW and owed your decision (Admin, Insider amendments; a pending void "
            "suppresses its original meanwhile):"
        )
        lines.extend(f"  {label}" for label in scan.new_labels)
        if scan.new_more:
            lines.append(
                f"  +{scan.new_more} more outside tonight's signal windows — listed in Admin"
            )
    if scan.known:
        lines.append(f"{scan.known} known form 4/A amendment(s) still owed a decision (quiet)")
    return lines

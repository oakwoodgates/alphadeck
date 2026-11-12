"""The nightly count of HELD insider rows (``insider_txn_hold``, migration 0082): how many there are, by
reason, and which are NEW since the last pass — so each held row is told to the operator ONCE and the known
ones stay a quiet count. The Form 4/A inventory's shape (``pipeline/insider_amendment_scan.py``), smaller.

WHAT A HOLD IS. A Form 4 or Form 4/A row whose transaction date cannot be true
(``ingest.edgar.form4.implausible_txn_date``: a year before 1934, or a year after the accession's own filing
year). The ingest writes it to ``insider_txn_hold`` instead of storing it as a fact, and no corrected date is
invented (#3). Nothing reads the table for a call, a signal or a dashboard; this scan only COUNTS it.

THE BASELINE. The inventory is read WHOLE from the table every pass (every held row, whatever pass or tool
wrote it — the ingest, or ``pipeline.backfill_insider_holds``), not from what that pass ingested. So the newest
FINISHED, LIVE pass that evaluated it is a complete baseline: a row whose key is in that pass's ``keys`` is
known, any other is new. ``None`` (no evaluated pass yet — the first pass after this ships) names every held
row once. A hold row is never updated or deleted, so a known row stays known.

THE BUCKET (step 3). Each held row carries where it sits in the steward's queue
(``domain.insider_holds.BUCKETS``), judged by the SAME ladder the insider read runs
(``repositories.insider_hold_repo.items`` -> ``domain.insider_holds.hold_state``), as of the scan. The block
counts them (``by_bucket``) and records each new row's bucket.

LOUDNESS. Only a NEW row OWED a steward decision is loud: the health page names those (ticker, accession, code,
shares) up to ``PAGE_NAME_CAP`` and counts the rest; it is benign-class ("not a cron error": the run did its
job, a filer wrote an impossible date). A new row placed by the rule, corrected by the filer, or held in a Form
4/A needs no decision and is ONE quiet count line. The printed report lists the owed rows with the date as filed
and the reason, and one quiet line counts the known ones. Nothing at all on a night with no held row.

FAIL-OPEN for the pass, LOUD for the operator: a scan that raises returns ``evaluated=False`` / ``error`` and
the pass goes on; a ``--no-live`` pass does not scan and is never a baseline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from domain.insider_holds import BUCKET_OWED, PAGE_NAME_CAP, hold_label, quiet_new_line

BLOCK = "insider_holds"

# Every held row with its company's ticker. No time gate: the scan counts what is held now.
_HOLDS = """
    SELECT h.id, h.tenant_id, h.security_id, h.accession, h.txn_seq, h.form, h.reason,
           h."row" AS held_row, sm.ticker
      FROM insider_txn_hold h
      LEFT JOIN security_master sm ON sm.id = h.security_id AND sm.tenant_id = h.tenant_id
     ORDER BY sm.ticker NULLS LAST, h.accession, h.txn_seq, h.form
"""


def hold_key(security_id: Any, accession: str, txn_seq: int, form: str) -> str:
    """One held row's identity on the artifact — the table's natural key (less the tenant)."""
    return f"{security_id}:{accession}:{txn_seq}:{form}"


@dataclass(frozen=True)
class HeldRow:
    """One held row, as the artifact records it."""

    key: str  # "<security_id>:<accession>:<txn_seq>:<form>"
    security_id: str
    ticker: str | None
    accession: str
    txn_seq: int
    form: str  # '4' or '4/A'
    code: str | None  # the transaction code (A, S, P, ...)
    shares: float | None
    price: float | None
    date_as_filed: str | None  # the text of the filing's own transaction date element
    reason: str  # the rule's code (year_before_1934 / year_after_filing_year)
    bucket: str = BUCKET_OWED  # where it sits in the steward's queue (domain.insider_holds.BUCKETS)

    @property
    def owed(self) -> bool:
        return self.bucket == BUCKET_OWED

    @property
    def label(self) -> str:
        """The row as the page names it (ticker, accession, code, shares)."""
        return hold_label(
            ticker=self.ticker,
            security_id=self.security_id,
            accession=self.accession,
            form=self.form,
            code=self.code,
            shares=self.shares,
        )

    @property
    def line(self) -> str:
        """The row as the printed report lists it: the label plus the date as filed and the reason."""
        return f"{self.label}: row {self.txn_seq}, date as filed {self.date_as_filed or '?'} ({self.reason})"

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "ticker": self.ticker,
            "accession": self.accession,
            "txn_seq": self.txn_seq,
            "form": self.form,
            "code": self.code,
            "shares": self.shares,
            "price": self.price,
            "date_as_filed": self.date_as_filed,
            "reason": self.reason,
            "bucket": self.bucket,
        }


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _held(r: Mapping[str, Any], bucket: str = BUCKET_OWED) -> HeldRow:
    row = r["held_row"] if isinstance(r["held_row"], Mapping) else {}
    return HeldRow(
        key=hold_key(r["security_id"], r["accession"], r["txn_seq"], r["form"]),
        security_id=str(r["security_id"]),
        ticker=r["ticker"],
        accession=r["accession"],
        txn_seq=r["txn_seq"],
        form=r["form"],
        code=row.get("txn_code"),
        shares=_num(row.get("shares")),
        price=_num(row.get("price")),
        date_as_filed=row.get("txn_date_as_filed"),
        reason=r["reason"],
        bucket=bucket,
    )


def scan_holds(conn: psycopg.Connection) -> tuple[HeldRow, ...]:
    """Every held row — read-only — by ticker, accession and row number, each with its bucket as the read sees
    it now (``insider_hold_repo.items``, per tenant the table holds — the DATA tenant in practice).
    """
    from repositories import insider_hold_repo  # lazy: the repo imports the read's loader

    with conn.cursor() as cur:
        cur.execute(_HOLDS)
        rows = cur.fetchall()
    buckets: dict[Any, str] = {}
    for tenant in dict.fromkeys(r["tenant_id"] for r in rows):
        for it in insider_hold_repo.items(conn, tenant_id=tenant):
            buckets[it.id] = it.bucket
    return tuple(_held(r, buckets.get(r["id"], BUCKET_OWED)) for r in rows)


@dataclass(frozen=True)
class HoldScan:
    """One pass's held-row outcome — what rides the artifact (``payload``), the printed report
    (``report_lines``) and the health page (``new_labels`` / ``new_more``)."""

    evaluated: bool = False
    holds: tuple[HeldRow, ...] = ()
    new: tuple[HeldRow, ...] = ()
    baseline: bool = (
        False  # a previous evaluated pass existed (False on the first night: all is new)
    )
    error: str | None = None
    duration_s: float = 0.0
    by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.holds)

    @property
    def known(self) -> int:
        return len(self.holds) - len(self.new)

    @property
    def new_owed(self) -> tuple[HeldRow, ...]:
        """The NEW held rows OWED a steward decision — the only loud ones."""
        return tuple(h for h in self.new if h.owed)

    @property
    def new_by_bucket(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for h in self.new:
            out[h.bucket] = out.get(h.bucket, 0) + 1
        return out

    @property
    def by_bucket(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for h in self.holds:
            out[h.bucket] = out.get(h.bucket, 0) + 1
        return out

    @property
    def new_labels(self) -> tuple[str, ...]:
        """The NEW OWED held rows the page NAMES — the first ``PAGE_NAME_CAP`` in inventory order."""
        return tuple(h.label for h in self.new_owed[:PAGE_NAME_CAP])

    @property
    def new_more(self) -> int:
        """The NEW OWED held rows the page only COUNTS."""
        return max(0, len(self.new_owed) - PAGE_NAME_CAP)

    def payload(self) -> dict:
        return {
            BLOCK: {
                "evaluated": self.evaluated,
                "total": self.total,
                "by_reason": dict(sorted(self.by_reason.items())),
                # step 3 — where every held row sits in the steward's queue (owed / placed_by_rule / ...)
                "by_bucket": dict(sorted(self.by_bucket.items())),
                "new": [h.to_json() for h in self.new],
                "known": self.known,
                "error": self.error,
                "duration_s": round(self.duration_s, 3),
                # the whole inventory's keys: the NEXT pass's baseline (``previous_hold_keys``). Without it
                # "new" could only be told from the rows one pass named, and a known row would page again.
                "keys": [h.key for h in self.holds],
            }
        }


def _err(e: Exception) -> str:
    msg = " ".join(str(e).split())[:200]
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def run_hold_scan(
    *,
    allow_live: bool,
    connect: Callable[[], psycopg.Connection],
    previous: Callable[[], set[str] | None],
    clock: Callable[[], float],
) -> HoldScan:
    """The nightly unit: read the hold table on its own read-only connection and diff it against the previous
    evaluated pass. Never raises (a fault rides ``error``); ``--no-live`` does not scan."""
    if not allow_live:
        return HoldScan()
    t0 = clock()
    try:
        conn = connect()
        try:
            conn.read_only = True  # the scan reads; structurally it cannot write a row
            holds = scan_holds(conn)
            conn.rollback()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a monitor fault is reported, never a failed pass
        return HoldScan(error=_err(e), duration_s=clock() - t0)
    prior = previous()
    by_reason: dict[str, int] = {}
    for h in holds:
        by_reason[h.reason] = by_reason.get(h.reason, 0) + 1
    return HoldScan(
        evaluated=True,
        holds=holds,
        new=tuple(h for h in holds if prior is None or h.key not in prior),
        baseline=prior is not None,
        duration_s=clock() - t0,
        by_reason=by_reason,
    )


def keys_of(doc: Mapping) -> set[str]:
    """The held-row keys one artifact recorded. Fail-open: a malformed entry is skipped."""
    block = doc.get(BLOCK)
    if not isinstance(block, Mapping):
        return set()
    return {str(k) for k in block.get("keys") or [] if isinstance(k, str) and k}


def previous_hold_keys(*, base_dir: Path | None = None) -> set[str] | None:
    """The held-row keys of the newest LIVE, FINISHED pass whose scan was evaluated — the baseline (see the
    module docstring). ``None`` when none exists yet. Fail-open per artifact. Pure file read."""
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
    """The NEW rows one artifact PAGED: the owed ones (an artifact written before step 3 carries no bucket —
    every new row was paged then, so it reads as owed: the history shows the page the run emitted).
    """
    block = doc.get(BLOCK)
    if not isinstance(block, Mapping):
        return []
    return [
        r
        for r in block.get("new") or []
        if isinstance(r, Mapping)
        and r.get("accession")
        and (r.get("bucket") or BUCKET_OWED) == BUCKET_OWED
    ]


def _label_of(r: Mapping) -> str:
    key = str(r.get("key") or "")
    return hold_label(
        ticker=r.get("ticker"),
        security_id=key.split(":", 1)[0],
        accession=str(r["accession"]),
        form=str(r.get("form") or ""),
        code=r.get("code"),
        shares=_num(r.get("shares")),
    )


def new_labels_of(doc: Mapping) -> tuple[str, ...]:
    """The NEW held rows one artifact NAMED on its page — the Admin history re-derives the run's page from
    these. ``()`` for an artifact without the block."""
    return tuple(_label_of(r) for r in _new_rows(doc)[:PAGE_NAME_CAP])


def new_more_of(doc: Mapping) -> int:
    """How many NEW held rows one artifact only COUNTED on its page."""
    return max(0, len(_new_rows(doc)) - PAGE_NAME_CAP)


def report_lines(scan: HoldScan) -> list[str]:
    """The cron's printed block: a failed scan LOUD; every NEW held row the page names LOUD (one line each)
    with ONE count line for the rest; then ONE quiet line counting the known ones. Nothing at all on a night
    with no held row, or on a pass that did not scan (loudness marks the exception)."""
    if scan.error:
        return [f"INSIDER HOLDS CHECK FAILED — {scan.error} (held insider rows were not counted)"]
    if not scan.evaluated:
        return []
    lines: list[str] = []
    owed = scan.new_owed
    if owed:
        lines.append(
            f"INSIDER ROWS HELD — {len(owed)} NEW, OWED A STEWARD DECISION (the filing's transaction date "
            "cannot be true; each row is kept in insider_txn_hold, not counted until a steward decides on "
            "Admin, and no date is invented):"
        )
        lines.extend(f"  {h.line}" for h in owed[:PAGE_NAME_CAP])
        if scan.new_more:
            lines.append(
                f"  +{scan.new_more} more — listed in the run artifact (insider_holds.new)"
            )
    quiet = quiet_new_line(scan.new_by_bucket)
    if quiet:
        lines.append(quiet)
    if scan.known:
        lines.append(
            f"{scan.known} known held insider row(s) — the same rows as the last pass (quiet; kept in "
            "insider_txn_hold)"
        )
    return lines

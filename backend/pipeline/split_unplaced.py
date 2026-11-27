"""``pipeline.split_unplaced``: the ONE reader of the split filings the list could not place, and the record a
long-window check writes (framework completion F1).

A filing that talks about a split the detector cannot place (no ratio, no date, an extractor abstain) is listed,
never dropped (``pipeline/detect_splits.py``, #9). Two runs list them:

- **the nightly**: its run artifact's ``split_detect.unplaced`` (``data/cron_runs``), over the filings filed in
  the last ``SEC_LOOKBACK_DAYS``;
- **the long-window check**: ``python -m pipeline.detect_splits --sec-days 800 --polygon-days 800 --record``
  writes its list here (``record_check``), one file a run under ``UNPLACED_DIR``. Without ``--record`` a dry
  run writes nothing.

``read_unplaced`` merges the newest of each: the nightly's newest finished live artifact whose split detection
ran, and the newest check record. A filing on both is the nightly's row. Each row says where it came from
(``source``) and the night or day that run read it (``seen_on``). Display only: no decision reads it.

Pure file reads and one atomic file write: no DB, no network. Fail-open per file: an unreadable file is
skipped (and named in ``error``), an older one is read instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pipeline import cron_run_log

if TYPE_CHECKING:
    from pipeline.detect_splits import SplitDetection

# the check's records: the repo's gitignored data/ locally, /data in the containers (the shared appdata volume)
UNPLACED_DIR = Path(__file__).resolve().parents[2] / "data" / "split_unplaced"
KIND = "detect_splits"
SOURCE_NIGHTLY = "nightly"
SOURCE_CHECK = "check"

# a row's keys (``detect_splits.Unplaced.to_json``); an older artifact lacks the evidence keys: they read null
ROW_KEYS = (
    "security_id",
    "ticker",
    "accession",
    "form",
    "filed",
    "reason",
    "url",
    "document",
    "passage",
    "ratio_from",
    "ratio_to",
    "abstain_reason",
    "date_note",
)


@dataclass(frozen=True)
class UnplacedRead:
    """The merged list (``rows``: newest filing first, each with ``source`` and ``seen_on``), the runs it came
    from (``runs``: ``source``, ``asof``, ``started_at``, ``sec_days``, ``rows`` = the run's own count; the
    nightly's ``sec_days`` is not on its artifact: null), and ``error``: the unreadable files passed over,
    ``None`` when every file read was fine."""

    rows: list[dict] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    error: str | None = None


def _write_atomic(path: Path, payload: dict) -> None:
    """A sibling temp file + ``os.replace`` (``cron_run_log._write_atomic``'s way): never seen half-written.
    The temp name does not end in ``.json``, so the reader skips it."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_check(
    result: SplitDetection,
    *,
    started_at: datetime,
    finished_at: datetime,
    asof: date,
    sec_days: int,
    polygon_days: int,
    base_dir: Path | None = None,
) -> Path:
    """Write one check run's unplaced list to ``<started_at %Y%m%dT%H%M%SZ>.json`` (UTC) under ``base_dir``
    (default ``UNPLACED_DIR``) and return its path."""
    home = base_dir or UNPLACED_DIR
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    _write_atomic(
        path,
        {
            "kind": KIND,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "asof": asof.isoformat(),
            "sec_days": sec_days,
            "polygon_days": polygon_days,
            "unplaced": [u.to_json() for u in result.unplaced],
        },
    )
    return path


def _load(path: Path) -> Any:
    """One file, parsed (raises on an unreadable one)."""
    return json.loads(path.read_text(encoding="utf-8"))


def _asof(doc: dict) -> str | None:
    try:
        return date.fromisoformat(doc["asof"]).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _nightly_rows(doc: Any) -> list | None:
    """The unplaced rows of a nightly artifact that counts: finished, live, its split detection evaluated."""
    if not isinstance(doc, dict) or not cron_run_log.is_finished(doc) or doc.get("mode") != "live":
        return None
    block = doc.get("split_detect")
    if not isinstance(block, dict) or block.get("evaluated") is not True or _asof(doc) is None:
        return None
    rows = block.get("unplaced")
    return rows if isinstance(rows, list) else []


def _check_rows(doc: Any) -> list | None:
    if not isinstance(doc, dict) or doc.get("kind") != KIND or _asof(doc) is None:
        return None
    rows = doc.get("unplaced")
    return rows if isinstance(rows, list) else None


def _newest(home: Path, rows_of, unreadable: list[str]) -> tuple[dict, list] | None:
    """The newest file under ``home`` that ``rows_of`` accepts, parsed one at a time newest-first (the name is the
    started-at instant, so the name sort is the time sort), stopping at the first. Unreadable files passed over
    are appended to ``unreadable``."""
    if not home.is_dir():
        return None
    for path in sorted(home.glob("*.json"), reverse=True):
        # fail-open per file: an unreadable file is skipped, and named
        try:
            doc = _load(path)
        except Exception:  # noqa: BLE001
            unreadable.append(f"{home.name}/{path.name}")
            continue
        if not isinstance(doc, dict):
            unreadable.append(f"{home.name}/{path.name}")
            continue
        rows = rows_of(doc)
        if rows is not None:
            return doc, rows
    return None


def _row(raw: Any, *, source: str, seen_on: str) -> dict | None:
    """One row with every key (``.get``: an older artifact reads null), or ``None`` when it names no filing."""
    if not isinstance(raw, dict):
        return None
    try:
        security_id = str(UUID(str(raw["security_id"])))
        filed = date.fromisoformat(raw["filed"]).isoformat()
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(raw.get("accession"), str) or not raw["accession"]:
        return None
    row = {k: raw.get(k) for k in ROW_KEYS}
    row["security_id"], row["filed"] = security_id, filed
    row["source"] = source
    row["seen_on"] = seen_on
    return row


def read_unplaced(*, runs_dir: Path | None = None, records_dir: Path | None = None) -> UnplacedRead:
    """The unplaced split filings: the nightly's newest list merged with the newest check record (the module
    docstring). ``runs_dir`` defaults to the cron's artifact home, ``records_dir`` to ``UNPLACED_DIR``, both
    read when called."""
    unreadable: list[str] = []
    sides = (
        (SOURCE_NIGHTLY, runs_dir or cron_run_log._DEFAULT_CRON_RUNS, _nightly_rows),
        (SOURCE_CHECK, records_dir or UNPLACED_DIR, _check_rows),
    )
    merged: dict[tuple[str, str], dict] = {}
    runs: list[dict] = []
    for source, home, rows_of in sides:
        found = _newest(home, rows_of, unreadable)
        if found is None:
            continue
        doc, raw_rows = found
        seen_on = _asof(doc)
        runs.append(
            {
                "source": source,
                "asof": seen_on,
                "started_at": doc.get("started_at"),
                "sec_days": doc.get("sec_days") if source == SOURCE_CHECK else None,
                "rows": len(raw_rows),
            }
        )
        for raw in raw_rows:
            row = _row(raw, source=source, seen_on=seen_on)
            if row is not None:
                # the nightly side is read first: a filing on both keeps the nightly's row
                merged.setdefault((row["security_id"], row["accession"]), row)
    rows = sorted(merged.values(), key=lambda r: (str(r["ticker"] or ""), r["accession"]))
    rows.sort(key=lambda r: r["filed"], reverse=True)
    error = f"unreadable: {', '.join(unreadable)}" if unreadable else None
    return UnplacedRead(rows=rows, runs=runs, error=error)

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

# The pandas sentinel (a measured ~5x on the replay lab, Windows). DuckDB's Python binding probes
# ``import pandas`` twice per bound parameter on EVERY execute (8x per as-of read below), and CPython
# never caches a FAILED import — each probe re-walks sys.path with stat calls (69,559 attempts ->
# 483,920 nt.stat = 217 s of a 308 s replay test; Windows stat is far slower than Linux, which was the
# whole CI-vs-local gap). A ``None`` entry makes ``import pandas`` raise ImportError IMMEDIATELY (loud,
# not silent — DuckDB's own try/except takes its no-pandas branch exactly as before) with no path scan;
# semantically a no-op because pandas is absent anyway. The ``find_spec`` guard means a venv WITH pandas
# is untouched. Measured: test_compare 315 s -> 67 s, rows identical. Pinned by test_pandas_probe.py.
if importlib.util.find_spec("pandas") is None:  # only when pandas is genuinely absent
    sys.modules["pandas"] = None

import duckdb

from db.bitemporal import _FACT_IDENTITY, knowability_expr
from db.session import DEFAULT_TENANT_ID
from replay.export import FACT_TABLES, MANIFEST_NAME
from signals.base import window_prices

# jsonb / array columns the export wrote as JSON strings — the accessor decodes them back to
# dicts/lists so the detectors (e.g. dilution_clock -> ConvertTerms.model_validate; the corporate
# detectors' ``items`` membership checks) get the same shape live and in replay. fact_corporate_event
# .items is a Postgres text[] (a Python list via psycopg) that the export json.dumps'd; decoding
# restores the list — a NULL stays None (unresolved items read identically on both engines).
_JSON_COLS: dict[str, tuple[str, ...]] = {
    "fact_dilution": ("terms",),
    "fact_corporate_event": ("items",),
}


def connect_mirror(parquet_dir: str | Path) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB over the Parquet mirror — one materialized table per fact table. The
    mirror is rebuildable + non-authoritative; this reads it for the fast as-of sweeps.

    A table the export DECLARED excluded (B2: a ``clock=public`` mirror drops any table with no public
    clock) has no file and is skipped. The list comes from the mirror's own manifest, not from a
    "skip whatever is missing" rule: a file missing for any OTHER reason is a broken export and must still
    fail loudly here rather than produce a mirror that silently answers nothing for that table. A detector
    that reads an excluded table then fails loudly too, which is the honest outcome — and
    ``export_snapshot`` names those detectors in the manifest before the run starts."""
    con = duckdb.connect()
    base = Path(parquet_dir)
    excluded: set[str] = set()
    manifest = base / MANIFEST_NAME
    if manifest.is_file():
        try:
            excluded = set(
                json.loads(manifest.read_text(encoding="utf-8")).get("excluded_tables", [])
            )
        except (OSError, json.JSONDecodeError):
            excluded = (
                set()
            )  # an unreadable manifest means "assume nothing was excluded" -> fail loud
    for table in FACT_TABLES:
        if table in excluded:
            continue
        path = (base / f"{table}.parquet").as_posix().replace("'", "''")
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{path}')")
    return con


def _partition_cols(table: str) -> list[str]:
    """The natural-key partition for a BASKET-WIDE read: ``security_id`` prefixed to the per-security
    identity, exactly as ``db.bitemporal.as_of_many`` builds it (``bitemporal.py:190``).

    Prefixing matters twice. It keeps each security's partition equal to what the SCOPED read sees (so a
    batch can never collapse two securities' rows onto one natural key — ``fact_insider_txn``'s identity
    is ``(accession, insider_name, valid_from, txn_seq)``, which is unique only *within* a security), and
    it makes the ORDER BY prefix a security boundary, so the within-security row order is identical on
    both paths."""
    ident = _FACT_IDENTITY[table]
    return ["security_id"] + [c for c in ident if c != "security_id"]


class ReplayPointInTimeData:
    """A DuckDB/Parquet-backed point-in-time view that **duck-types** ``signals.base.PointInTimeData`` —
    the same five accessors, same signatures, same ``list[dict]`` returns — so the unchanged detectors
    (and ``pipeline.core.assemble_from_pit``) consume it identically. Only the fact SOURCE differs.

    The as-of read mirrors ``db.bitemporal._as_of`` exactly: ``valid_from <= asof AND recorded_at <=
    known_at``, then the latest row per natural-key identity (``_FACT_IDENTITY``) by ``recorded_at DESC,
    id DESC`` (the same deterministic tiebreak). The cap is constructor-bound — every accessor query is
    upper-bounded by ``asof``/``known_at`` with no widening path (the lookahead boundary). A parity test
    asserts each accessor equals the live ``PointInTimeData`` accessor row-for-row.

    **The memo + basket prefetch + read bounds (backtest B5a) — the LIVE PIT's rule, ported.** This view
    had none of the three, and that was the entire cost of a replay: MEASURED on the dev copy of prod, a
    196-name thesis issued **2,935 DuckDB queries per session** (``price_history`` alone ~7x per
    member-session, each re-reading that name's WHOLE tape unbounded and trimming in Python) and took
    **42.1 s per session**. With the three below it issues **8 queries per session** and takes **1.65 s**
    — a MEASURED **25.5x**, with byte-identical ``CallSnapshot``s. The mechanisms are deliberately the
    same ones ``signals.base.PointInTimeData`` already documents, so the two PITs read as twins:

    - **memo** — each ``(table, scope_id)`` as-of result is fetched ONCE for this view's lifetime. The key
      is ONLY the table + the scope id: ``asof`` / ``known_at`` / ``tenant_id`` are constructor-bound, so a
      memo can never cross a time or tenant boundary, and a per-CALL value (``price_history``'s
      ``lookback_days``) is NEVER part of a key — every caller trims its own window from the memoized rows.
    - **prefetch** — with a ``basket`` (the roster's resolved security ids AT THIS asof), the FIRST read of
      a security-scoped table loads that table for the WHOLE basket in one query and fills the memo,
      seeding an EMPTY list for a member with no rows so it is never re-queried. An id outside the basket
      falls back to the per-security read, memoized. ``basket=None`` is the plain per-security read.
    - **bounds** — the registry-DERIVED event-time floor per table (``signals/horizons.call_bounds(cfg)``:
      ``max(declared reader horizons) + MARGIN_DAYS``, ``None`` = unbounded). Applied as a ``valid_from >=``
      predicate on BOTH paths. Like the live twin, this view **never applies a floor on its own
      initiative**: the map is caller-supplied and registry-derived, and the caller must derive it from the
      SAME ``cfg`` the assembler runs with — a sweep that widens a lookback dial widens the floor with it,
      or the read silently truncates. The floor is version-safe on the tables the registry bounds because
      ``valid_from`` is a natural-key column there (``db/bitemporal.py:176-182``), so it can only drop
      whole facts older than the floor, never change which VERSION of a fact wins.

    **Row ORDER is pinned.** Both queries carry an explicit ``ORDER BY`` on the partition columns. DuckDB's
    ``QUALIFY`` leaves the output order implementation-defined, and a tool whose premise is reproducible,
    addressable runs must not rest on an accident — the same reason ``db.bitemporal.as_of_many`` states
    that its ``DISTINCT ON`` ordering makes the batch's within-security order equal the scoped read's.
    """

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        asof: date,
        known_at: datetime,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        basket: Iterable[UUID] | None = None,
        bounds: Mapping[str, int | None] | None = None,
    ) -> None:
        self.con = con
        self.asof = asof
        self.known_at = known_at
        self.tenant_id = tenant_id
        # the prefetch scope, de-duplicated and ORDER-STABLE (the bound parameter list must not depend on
        # set iteration order — a run has to reproduce itself). Empty == no basket == per-security reads.
        self._basket: tuple[UUID, ...] = tuple(dict.fromkeys(basket or ()))
        self._in_basket: frozenset[UUID] = frozenset(self._basket)
        # the mirror stores uuid columns as VARCHAR (``export._OID_ARROW[2950]`` is ``pa.string()`` and
        # ``_coerce`` stringifies), so a prefetched row's ``security_id`` comes back as text. This maps it
        # BACK to the caller's UUID; without it every memo lookup misses and the prefetch silently serves
        # empty lists — which looks exactly like a 35x speed-up and is measuring nothing.
        self._sid_by_text: dict[str, UUID] = {str(s): s for s in self._basket}
        self._bounds: dict[str, int | None] = dict(bounds or {})
        self._memo: dict[tuple[str, UUID], list[dict[str, Any]]] = {}
        self._prefetched: set[str] = set()

    # --- the memo + prefetch seam (every fact accessor below goes through here) ---------------------

    def _lower(self, table: str) -> date | None:
        """The event-time floor for ``table`` under this view's bounds, or None (unbounded)."""
        days = self._bounds.get(table)
        return None if days is None else self.asof - timedelta(days=days)

    def _decode(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Decode jsonb-as-string back to a dict/list — on EVERY path, scoped and prefetched."""
        for jc in _JSON_COLS.get(table, ()):
            for row in rows:
                if isinstance(row.get(jc), str):
                    row[jc] = json.loads(row[jc])
        return rows

    def _gate(self, table: str) -> str:
        """The knowability gate — BYTE-IDENTICAL to db.bitemporal._as_of (currently recorded_at for every
        table: the strict "what we held" no-lookahead axis; accepted is display/metrics only, never a
        gate). ONE source of truth (knowability_expr), so both engines revert in lockstep."""
        return knowability_expr(table)

    def _read_scoped(self, table: str, scope_col: str, scope_id: UUID) -> list[dict[str, Any]]:
        """The per-scope as-of read — one security (or one thesis). The pre-B5a query, plus the optional
        registry floor and the explicit ORDER BY."""
        ident = ", ".join(_FACT_IDENTITY[table])  # identity cols (trusted whitelist)
        lower = self._lower(table)
        floor = " AND valid_from >= ?" if lower is not None else ""
        query = (
            f"SELECT * FROM {table} "
            f"WHERE tenant_id = ? AND {scope_col} = ? "
            f"AND valid_from <= ? AND {self._gate(table)} <= ?{floor} "
            f"QUALIFY ROW_NUMBER() OVER "
            f"(PARTITION BY {ident} ORDER BY recorded_at DESC, id DESC) = 1 "
            f"ORDER BY {ident}"
        )
        params: list[Any] = [str(self.tenant_id), str(scope_id), self.asof, self.known_at]
        if lower is not None:
            params.append(lower)
        res = self.con.execute(query, params)
        cols = [d[0] for d in res.description]
        return self._decode(table, [dict(zip(cols, r)) for r in res.fetchall()])

    def _prefetch(self, table: str) -> None:
        """ONE as-of read for the WHOLE basket, filling the memo — the DuckDB twin of
        ``db.bitemporal.as_of_many``: same gate, same per-security partition, same ``recorded_at DESC,
        id DESC`` tiebreak, same optional floor, and an entry for EVERY requested id (``[]`` when it has
        no rows, so "nothing on file" is memoized and never re-queried)."""
        ident = ", ".join(_partition_cols(table))
        lower = self._lower(table)
        floor = " AND valid_from >= ?" if lower is not None else ""
        placeholders = ", ".join("?" for _ in self._basket)
        query = (
            f"SELECT * FROM {table} "
            f"WHERE tenant_id = ? AND security_id IN ({placeholders}) "
            f"AND valid_from <= ? AND {self._gate(table)} <= ?{floor} "
            f"QUALIFY ROW_NUMBER() OVER "
            f"(PARTITION BY {ident} ORDER BY recorded_at DESC, id DESC) = 1 "
            f"ORDER BY {ident}"
        )
        params: list[Any] = [
            str(self.tenant_id),
            *[str(s) for s in self._basket],
            self.asof,
            self.known_at,
        ]
        if lower is not None:
            params.append(lower)
        res = self.con.execute(query, params)
        cols = [d[0] for d in res.description]
        by_sid: dict[UUID, list[dict[str, Any]]] = {sid: [] for sid in self._basket}
        for r in res.fetchall():
            row = dict(zip(cols, r))
            sid = self._sid_by_text.get(str(row["security_id"]))
            if sid is not None:  # an id outside the basket cannot appear, but never guess
                by_sid[sid].append(row)
        for sid, rows in by_sid.items():
            self._memo[(table, sid)] = self._decode(table, rows)

    def _as_of(self, table: str, scope_col: str, scope_id: UUID) -> list[dict[str, Any]]:
        if table not in _FACT_IDENTITY:
            raise ValueError(f"unknown fact table: {table!r}")
        key = (table, scope_id)
        if key not in self._memo:
            if (
                scope_col == "security_id"
                and scope_id in self._in_basket
                and table not in self._prefetched
            ):
                self._prefetch(table)
                self._prefetched.add(table)
            if key not in self._memo:  # outside the basket, thesis-scoped, or no basket at all
                self._memo[key] = self._read_scoped(table, scope_col, scope_id)
        # a FRESH list per call; the memo's container is never handed out (the row dicts are shared —
        # readers are audited never to mutate a row, the live twin's rule)
        return list(self._memo[key])

    def insider_txns(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_insider_txn", "security_id", security_id)

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self._as_of("fact_price_eod", "security_id", security_id)
        return window_prices(rows, self.asof, lookback_days)  # the SAME sort/trim as the live PIT

    def dilution_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_dilution", "security_id", security_id)

    def catalyst_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_catalyst", "security_id", security_id)

    def fundamentals_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The mirror twin of ``PointInTimeData.fundamentals_facts`` (§2.2) — the same as-of quarterly
        series over the DuckDB/Parquet mirror, so the revenue-acceleration detector runs identically in
        replay (A.1: everything the live path reads, replay must read). Parity-gated row-for-row."""
        return self._as_of("fact_fundamentals", "security_id", security_id)

    def corporate_event_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The mirror twin of ``PointInTimeData.corporate_event_facts`` (Band 03 S3) — the as-of 8-K
        item-code tape over the mirror, so the corporate-catalyst/-risk detectors run identically in
        replay. ``items`` (a Postgres text[]) round-trips through the export as a JSON string and is
        decoded back to a list here (``_JSON_COLS``). Parity-gated row-for-row."""
        return self._as_of("fact_corporate_event", "security_id", security_id)

    def activist_stake_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The mirror twin of ``PointInTimeData.activist_stake_facts`` (Band 03 S5) — the as-of
        SC 13D/G ownership tape over the mirror, so the activist-stake detector runs identically in
        replay (scalar columns only — no ``_JSON_COLS`` decode needed). Parity-gated row-for-row."""
        return self._as_of("fact_activist_stake", "security_id", security_id)

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_theme_conviction", "thesis_id", thesis_id)

    def security_name(self, security_id: UUID) -> str | None:
        """Satisfies the protocol; the replay mirror holds FACT tables only, not ``security_master``
        (identity). Returns ``None`` — the insider-detector's issuer-self screen falls back here to the
        CANONICAL CIK match (``rpt_owner_cik == issuer_cik``), which flows into the replay rows via the
        ``SELECT *`` insider-txn read, so a self-filing captured with CIKs is still excluded in replay.
        (A pre-capture row with no CIKs is not excluded in replay until the mirror is re-exported — a
        documented, rebuildable-mirror limitation, never a silent live-path drop.)"""
        return None

    def security_cik(self, security_id: UUID) -> str | None:
        """Satisfies the protocol; the replay mirror holds FACT tables only, not ``security_master``
        (identity), so there is no subject CIK to resolve. Returns ``None`` — the activist-stake
        detector's self-filed screen (``filer_cik`` == the subject's CIK) simply keeps the row in replay
        (recall-safe, #9), exactly the ``security_name`` fallback shape. The sub-5% pct screen is pure
        (reads the fact row) so it still applies in replay; and the detector is OFF by default, forced on
        only by ``--activist-stake``. A documented, rebuildable-mirror limitation, never a live-path drop.
        """
        return None

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql

from db.bitemporal import _FACT_IDENTITY
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_tz

# The bitemporal fact tables, taken from the as-of read's own whitelist (single source of truth — the
# mirror can never drift from the set of tables the live reader knows).
FACT_TABLES: tuple[str, ...] = tuple(_FACT_IDENTITY)

MANIFEST_NAME = "manifest.json"

# --- THE PUBLIC-CLOCK REGISTRY (backtest only) ---------------------------------------------------------
# "When could ANYONE have known this?", per table. The platform has three clocks — event time
# (``valid_from``: when it happened), public time (below: when it was disclosed), system time
# (``recorded_at``: when THIS system ingested it). The Scoreboard gates on system time and must keep
# doing so: it answers "what did we hold", and new features and new doc processing mean the record can
# only speak to what the system had. The BACKTEST asks a different question — "what would the algorithm
# have called on the information available?" — and for that the system clock is the wrong axis and, for
# any window before 2026-06, an unusable one (the filing tables' ``recorded_at`` does not reach back).
#
# THE REGISTRY LIVES HERE, in the exporter, and NEVER beside the live gate. ``db.bitemporal
# .knowability_expr`` stays the single definition of knowability for every serve path; this is a property
# of a research MIRROR, materialized once at export, so the replay reader and the as-of gate are untouched
# and cannot drift into the product.
#
# A table absent from this map is EXCLUDED WHOLE from a ``clock=public`` mirror and named in the export
# manifest together with the detectors thereby blind. A ROW whose clock cannot be determined is EXCLUDED
# and COUNTED. Nothing is modeled, estimated or back-filled from a neighbouring column: an unknown
# disclosure date is a row we decline to replay, not a guess (#6).
_PUBLIC_CLOCK: dict[str, str] = {
    "fact_price_eod": "d",  # the bar's own date — a close is public at the close
    # XBRL fundamentals: there is NO ``filed`` column; ``valid_from`` IS the filing date here (R17 already
    # stamps it that way — MEASURED on the dev copy, ``recorded_at::date == valid_from`` for all 120,626
    # rows). So ``clock=public`` is a NO-OP for this table, which is worth knowing before reading a
    # fundamentals result as something the clock change revealed.
    "fact_fundamentals": "valid_from",
    "fact_corporate_event": "filed",  # the 8-K's filing date
    "fact_activist_stake": "filed",  # the 13D/G's filing date
    # Form 4: EDGAR's ACCEPTANCE instant, the closest proxy for dissemination. NULL on 0.67% of facts
    # (2,128 of 316,374 on the latest-version grain) — those are excluded and counted, never modeled: the
    # measured disclosure lag has a long tail (p50/p90/p99 = 2/5/83 days on open-market buys), so a flat
    # offset would be wrong in exactly the cases that matter.
    "fact_insider_txn": "accepted",
    # The three operator-RATIFIED tables carry no disclosure column, so ``valid_from`` — the announcement
    # date by construction (``ingest/catalyst.py`` writes ``valid_from = event_date``). Operator decision.
    # THE HONEST CAVEAT, which no column can fix: the operator ratified these in 2026, and this models them
    # as public on an announcement date that can predate the ratification by years. It is the right call on
    # the DATA axis and hindsight on the DECISION axis; eight rows in total, and the run's labels say so.
    "fact_catalyst": "valid_from",
    "fact_dilution": "valid_from",
    "fact_theme_conviction": "valid_from",
}

# The SCOPE column each fact is partitioned within — the same prefix ``db.bitemporal.as_of_many`` applies
# (``bitemporal.py:190``), because a table's ``_FACT_IDENTITY`` is unique only WITHIN one scope:
# ``fact_insider_txn``'s is ``(accession, insider_name, valid_from, txn_seq)``, which two securities can
# share (the 0037 lesson — the uniqueness constraint had to be widened with ``security_id``).
_SCOPE_COL: dict[str, str] = {
    t: "thesis_id" if t == "fact_theme_conviction" else "security_id" for t in _FACT_IDENTITY
}


class AmbiguousPublicClock(RuntimeError):
    """An identity whose public clock cannot be determined from its own versions — see ``_public_sql``."""


# Postgres type OID -> Arrow type, so the Parquet schema is EXPLICIT (correct even for an empty table — a
# tenant missing one fact kind still gets a date-typed `valid_from`, so the as-of date filter binds). numeric
# -> float64 (we float() it; every detector floats numerics anyway, so this is lossless for the call); uuid /
# jsonb / text -> string; date -> date32; timestamptz -> tz-aware timestamp. Unknown OIDs fall back to string.
_OID_ARROW: dict[int, pa.DataType] = {
    16: pa.bool_(),  # bool
    21: pa.int64(),  # int2
    23: pa.int64(),  # int4
    20: pa.int64(),  # int8
    25: pa.string(),  # text
    1043: pa.string(),  # varchar
    1082: pa.date32(),  # date  (valid_from / valid_to / d / horizon_end)
    1114: pa.timestamp("us"),  # timestamp (no tz)
    1184: pa.timestamp("us", tz="UTC"),  # timestamptz (recorded_at)
    700: pa.float64(),  # float4
    701: pa.float64(),  # float8
    1700: pa.float64(),  # numeric (close/usd/shares/... -> float)
    2950: pa.string(),  # uuid
    114: pa.string(),  # json
    3802: pa.string(),  # jsonb (terms -> JSON string; the accessor json.loads it back)
}


def _coerce(v: Any) -> Any:
    """Coerce a Postgres value to the Parquet schema's Python type. Lossless for what the call cares about:
    numerics float (every detector ``float(...)``s them), uuids stringify, jsonb (dict/list — fact_dilution
    .terms) JSON-dumps (the accessor ``json.loads`` it back so ``ConvertTerms.model_validate`` still gets a
    dict). dates / datetimes / strings / ints / None pass through."""
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return v


def _partition_cols(table: str) -> list[str]:
    """``tenant_id, <scope>, <identity minus scope>`` — one logical fact, scoped, exactly as the as-of
    reads partition it."""
    ident = _FACT_IDENTITY[table]
    scope = _SCOPE_COL[table]
    return ["tenant_id", scope] + [c for c in ident if c != scope]


def _read_table(
    conn: psycopg.Connection, table: str, tenant_id: UUID
) -> tuple[pa.Schema, list[dict]]:
    """Read ALL columns + ALL rows of one fact table for the tenant, plus an explicit Arrow schema derived
    from the Postgres column types (table name from the trusted ``_FACT_IDENTITY`` whitelist)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE tenant_id = %(tenant_id)s").format(
                sql.Identifier(table)
            ),
            {"tenant_id": tenant_id},
        )
        rows = cur.fetchall()
        schema = pa.schema(
            [(d.name, _OID_ARROW.get(d.type_code, pa.string())) for d in cur.description]
        )
    return schema, rows


def _read_table_public(
    conn: psycopg.Connection, table: str, tenant_id: UUID
) -> tuple[pa.Schema, list[dict], dict[str, int]]:
    """Read one fact table onto the PUBLIC clock: ``recorded_at`` becomes the disclosure instant, and the
    version pile-up that creates is resolved HERE rather than left to the reader.

    **Why the resolution cannot be left to the reader.** Both as-of reads pick a version with
    ``ORDER BY recorded_at DESC, id DESC``. On the system clock the versions of one fact have DISTINCT
    ``recorded_at`` values, so the ``id`` tiebreak almost never fires. Rewriting ``recorded_at`` to the
    public clock collapses them onto ONE value, and then ``id`` decides — and ``id`` is
    ``uuid DEFAULT gen_random_uuid()`` on all fourteen fact tables, so "the tiebreak picks the latest
    content" is false: it picks a RANDOM one. MEASURED on the dev copy: that would fire on 44,982 insider
    version-groups and 17,373 price version-groups, and **8,947 of the price groups hold different closes**
    — a coin flip between a corrected and an uncorrected bar, in a tool whose entire premise is that a run
    is reproducible and that a config delta is attributable to the config.

    It would also silently corrupt the SCORING reader, which nobody would think to check:
    ``replay.scoring.RealizedPrices`` reads this same mirror with the same tiebreak and is deliberately
    NOT as-of capped, so the same random pick would land in the realized returns.

    **The resolution.** Per identity:

    1. Determine the identity's clock. A row's own clock when it has one; otherwise the unique non-NULL
       clock among its OTHER versions. That NULL-fill is what keeps our BEST PARSE: an insider fact whose
       latest version predates the ``accepted`` backfill would otherwise have to fall back to an older,
       less-repaired version (MEASURED: 6 identities on the dev copy are in exactly that state). The clock
       is a property of the ACCESSION, identical across an identity's versions — MEASURED: 0 identities on
       any table have more than one distinct non-NULL clock together with a NULL one.
    2. That "unique" is ASSERTED, not assumed: an identity carrying a NULL clock AND two different non-NULL
       ones cannot be attributed, and raises rather than picking one.
    3. Partition by ``(identity, clock)`` — NOT by identity alone. Including the clock is what preserves a
       genuine re-disclosure: a restated XBRL quarter has its own ``filed`` date and must stay visible at
       that date, while a pure re-parse (same clock, later ``recorded_at``) collapses to our best one.
       MEASURED: 20,801 ``fact_fundamentals`` identities carry more than one ``filed``; every one survives.
    4. Keep the latest by the HONEST ``recorded_at`` within each group, then overwrite ``recorded_at`` with
       the clock. After this no identity has two rows sharing a ``recorded_at``, so the random tiebreak has
       nothing left to decide.

    Returns the schema, the rows, and the four counts the run manifest reports — separate numbers because
    they answer separate questions: a 47% raw-row drop with 0.67% identities lost is a healthy export (the
    dropped rows were superseded versions), while a small row drop with a large identity loss is a hole.
    """
    clock = _PUBLIC_CLOCK[table]
    part = sql.SQL(", ").join(sql.Identifier(c) for c in _partition_cols(table))
    clock_id = sql.Identifier(clock)
    query = sql.SQL(
        "WITH scoped AS ("
        "  SELECT *, "
        "         min({clock}) OVER p AS _cmin, "
        "         max({clock}) OVER p AS _cmax, "
        "         count(*) OVER p - count({clock}) OVER p AS _nulls "
        "  FROM {table} WHERE tenant_id = %(tenant_id)s "
        "  WINDOW p AS (PARTITION BY {part})"
        "), resolved AS ("
        "  SELECT *, COALESCE({clock}, _cmax) AS _clock FROM scoped"
        ") "
        "SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY {part}, _clock ORDER BY recorded_at DESC, id DESC) AS _rn "
        "FROM resolved ORDER BY {part}, _clock"
    ).format(clock=clock_id, table=sql.Identifier(table), part=part)

    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id})
        raw = cur.fetchall()
        schema = pa.schema(
            [
                (d.name, _OID_ARROW.get(d.type_code, pa.string()))
                for d in cur.description
                if not d.name.startswith("_")
            ]
        )

    ambiguous = [r for r in raw if r["_nulls"] > 0 and r["_cmin"] != r["_cmax"]]
    if ambiguous:
        r = ambiguous[0]
        raise AmbiguousPublicClock(
            f"{table}: {len(ambiguous)} row(s) belong to a fact whose versions disagree on "
            f"{clock!r} while some carry none — the disclosure instant cannot be attributed. "
            f"First: {{{', '.join(f'{c}={r[c]!r}' for c in _partition_cols(table))}}} "
            f"spans {r['_cmin']!r}..{r['_cmax']!r}. This is a FLAG, not a row to guess at."
        )

    identities_total = len({tuple(r[c] for c in _partition_cols(table)) for r in raw})
    kept, dropped_null_clock = [], 0
    survivors: set[tuple] = set()
    for r in raw:
        if r["_clock"] is None:  # no version of this fact carries a disclosure instant
            dropped_null_clock += 1
            continue
        survivors.add(tuple(r[c] for c in _partition_cols(table)))
        if r["_rn"] != 1:
            continue
        row = {k: v for k, v in r.items() if not k.startswith("_")}
        # THE REWRITE: the mirror's transaction axis becomes the public one. The reader and the gate are
        # untouched — they still read ``recorded_at``; it now means "when this became public".
        row["recorded_at"] = _as_utc(r["_clock"])
        kept.append(row)

    counts = {
        "rows_in": len(raw),
        "rows_out": len(kept),
        "rows_dropped_null_clock": dropped_null_clock,
        "versions_collapsed": len(raw) - dropped_null_clock - len(kept),
        "identities_lost": identities_total - len(survivors),
    }
    return schema, kept, counts


def _as_utc(clock: Any) -> datetime:
    """The clock value as a UTC instant.

    A ``timestamptz`` (``accepted``) passes through — it already IS an instant. A DATE becomes the START of
    that day in MARKET time, not UTC: this repo's rule is that a trading day is a domain fact rather than
    an environment one (``domain.market_time``), and anchoring in market time is what keeps the stamp from
    sliding into the previous day across a DST boundary. The choice is inert on visibility either way,
    because every date-clocked table here has ``clock == valid_from``, so the event-time half of the gate
    (``valid_from <= asof``) already decides those rows — but a later reader will want to know that it was
    a decision and not an accident."""
    if isinstance(clock, datetime):
        return clock
    return datetime.combine(clock, time.min, tzinfo=market_tz()).astimezone(timezone.utc)


def blind_detectors(excluded: list[str]) -> list[str]:
    """Which registered detectors read ONLY excluded tables — i.e. which ones cannot fire in this mirror.

    Named beside the excluded tables because listing the tables alone is half an answer: a reader still has
    to work out what stopped firing, and without that a result reads as "that detector is inert on this
    tape" when the truth is "that detector had no tape". Derived from each detector's own declared read
    horizons (``signals/horizons.py``), so it cannot fall behind the registry.
    """
    if not excluded:
        return []
    from domain.config import DEFAULT_CONFIG
    from signals import registered_detectors

    gone = set(excluded)
    out = []
    for det in registered_detectors():
        tables = set(det.horizons(DEFAULT_CONFIG))
        if tables and tables <= gone:
            out.append(det.name)
    return sorted(out)


def export_snapshot(
    conn: psycopg.Connection,
    out_dir: str | Path,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    clock: Literal["record", "public"] = "record",
) -> dict[str, Any]:
    """Export the bitemporal fact tables (ALL columns, ALL rows for the tenant) from Postgres (the SoR) to
    one Parquet file per table — the rebuildable, NON-authoritative analytical mirror DuckDB sweeps.

    One-shot truncate-and-rewrite. The PIN (``known_at`` ceiling) is **not** applied here: it is a READ-time
    filter in ``ReplayPointInTimeData``, so the mirror faithfully reproduces the SoR's ``as_of`` for *any*
    ``known_at`` (the parity + transaction-time no-lookahead tests require this). Every column is exported so
    no detector-read field can be silently dropped (the parity gate then catches any divergence). Returns a
    manifest (per-table row count + max ``recorded_at``) — derived from the data, so it carries no clock.

    ``clock`` picks WHICH AXIS the mirror's ``recorded_at`` carries.

    ``"record"`` (the default, and the ONLY mode the Postgres-vs-Parquet parity gate runs on) is the
    system clock, byte-for-byte what this exporter always wrote: every row, every version, ``recorded_at``
    untouched. Nothing about that path changes.

    ``"public"`` rewrites it to the disclosure instant from ``_PUBLIC_CLOCK`` and resolves the version
    pile-up that creates (see ``_read_table_public`` — this is where the random-``id`` tiebreak is closed).
    A table with no declared clock is excluded WHOLE and named in the manifest with the detectors it
    blinds. The reader, the gate and ``knowability_expr`` are untouched in both modes: the axis is chosen
    once, when the mirror is materialized.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    excluded = [t for t in FACT_TABLES if clock == "public" and t not in _PUBLIC_CLOCK]
    manifest: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "clock": clock,
        "tables": {},
        "excluded_tables": excluded,
        "blind_detectors": blind_detectors(excluded),
    }
    for table in FACT_TABLES:
        if table in excluded:
            continue
        counts: dict[str, int] = {}
        if clock == "public":
            schema, rows, counts = _read_table_public(conn, table, tenant_id)
        else:
            schema, rows = _read_table(conn, table, tenant_id)
        coerced = [{k: _coerce(v) for k, v in row.items()} for row in rows]
        pq.write_table(pa.Table.from_pylist(coerced, schema=schema), out / f"{table}.parquet")
        max_rec = max((r["recorded_at"] for r in rows), default=None)
        manifest["tables"][table] = {
            "rows": len(rows),
            "max_recorded_at": max_rec.isoformat() if max_rec is not None else None,
            **counts,
        }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest

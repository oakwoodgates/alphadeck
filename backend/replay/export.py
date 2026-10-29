from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql

from db.bitemporal import _FACT_IDENTITY, DISPLAY_ONLY_FACT_TABLES
from db.public_clock import (
    CEILING_PARAM,
    INGESTED_COL,
    PUBLIC_COL,
    PUBLIC_TIME,
    aux_public_sql,
    partition_cols,
    public_params,
    public_versions_sql,
    split_versions_sql,
    to_public_row,
)
from db.session import DEFAULT_TENANT_ID
from db.set_aside import active_ids, exclusion_sql, has_set_aside

# The bitemporal fact tables, taken from the as-of read's own whitelist (single source of truth — the
# mirror can never drift from the set of tables the live reader knows) MINUS the display-only tables no detector
# reads (``db.bitemporal.DISPLAY_ONLY_FACT_TABLES``): ``connect_mirror`` requires a file for every table in this
# set, so one added here would make every frozen mirror written before it unreadable — for rows no replay reads.
FACT_TABLES: tuple[str, ...] = tuple(t for t in _FACT_IDENTITY if t not in DISPLAY_ONLY_FACT_TABLES)

MANIFEST_NAME = "manifest.json"

# --- THE PUBLIC CLOCK — one definition, shared with every history view ---------------------------------
# "When could ANYONE have known this?", per table. The platform has three clocks — event time
# (``valid_from``: when it happened), public time (when it was disclosed), system time (``recorded_at``: when
# THIS system ingested it). Since the operator's 2026-10-21 rule (INVARIANTS §4) HISTORY is judged on the public
# clock everywhere — the backtest AND the product's history views (the past-asof Board/Cockpit/Workbench
# recompute, the missed-night backfill, the Scoreboard drawer). So the registry no longer lives here: it is
# ``db.public_clock.PUBLIC_TIME``, and this exporter runs the SAME SQL (``public_versions_sql``) the live
# history read runs, materialized once into the mirror — a public mirror read is row-for-row the Postgres
# public read (``tests/replay/test_public_clock.py``'s parity test). The LIVE read (asof = today) stays on the
# record clock, where the two coincide; ``clock="record"`` here stays the parity gate's mode, byte-for-byte.
#
# A table absent from the registry is EXCLUDED WHOLE from a ``clock=public`` mirror and named in the export
# manifest together with the detectors thereby blind (today every fact table has a public clock). A ROW whose
# clock cannot be determined (a Form 4 with no acceptance on any version) is EXCLUDED and COUNTED here —
# the backtest's contract — while the serve path counts it from the first time we held it (an upper bound,
# never early). Nothing is modeled or estimated from a neighbouring column (#6).
#
# Fundamentals: there is NO ``filed`` column; ``valid_from`` IS the filing date (R17), and the public instant
# is the start of that day. Rows written before migration 0057 were ALSO stamped ``recorded_at = filed 00:00
# UTC`` (MEASURED on prod: all 120,876), rows since carry the honest ingest instant; either way the public
# mirror clocks every version at its filing (one row per (identity, filing) — the key 0057 enforces), so the
# clock change reveals nothing for fundamentals on its own.
_PUBLIC_CLOCK = PUBLIC_TIME  # the name this module's callers and tests have always read


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


def _ceiling_sql(known_by: datetime | None, alias: str | None = None) -> sql.Composable:
    """The FREEZE CEILING (``db.public_clock.CEILING_PARAM``) on a plain scan: `` AND recorded_at <= cut``
    when ``known_by`` is set, else nothing (the export is then byte-identical to before the ceiling).
    """
    if known_by is None:
        return sql.SQL("")
    col = sql.Identifier(alias, "recorded_at") if alias else sql.Identifier("recorded_at")
    return sql.SQL(" AND {} <= {}").format(col, sql.Placeholder(CEILING_PARAM))


def _read_table(
    conn: psycopg.Connection, table: str, tenant_id: UUID, *, known_by: datetime | None = None
) -> tuple[pa.Schema, list[dict]]:
    """Read ALL columns + ALL rows of one fact table for the tenant, plus an explicit Arrow schema derived
    from the Postgres column types (table name from the trusted ``_FACT_IDENTITY`` whitelist) — except a
    SET-ASIDE version (migration 0062, ``db.set_aside``), which the live read skips too, so the two engines
    read the same versions (the parity gate); every other table is read whole, exactly as before. ``known_by``
    = only the rows recorded by that cut (the freeze ceiling), the set-asides judged as they stood at it.
    """
    at = sql.Placeholder(CEILING_PARAM) if known_by is not None else None
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE tenant_id = %(tenant_id)s{}{}").format(
                sql.Identifier(table), exclusion_sql(table, at=at), _ceiling_sql(known_by)
            ),
            {"tenant_id": tenant_id, CEILING_PARAM: known_by},
        )
        rows = cur.fetchall()
        schema = pa.schema(
            [(d.name, _OID_ARROW.get(d.type_code, pa.string())) for d in cur.description]
        )
    return schema, rows


def _count_set_aside(
    conn: psycopg.Connection, table: str, tenant_id: UUID, *, known_by: datetime | None = None
) -> int:
    """How many of the tenant's stored versions of ``table`` are ACTIVELY set aside (migration 0062) — the
    versions both export modes leave out (``exclusion_sql`` here, ``public_versions_sql`` on the public path).
    ``known_by`` (the freeze ceiling) counts what the cut left out: versions stored by the cut whose set-aside
    was active at the cut."""
    at = sql.Placeholder(CEILING_PARAM) if known_by is not None else None
    query = sql.SQL(
        "SELECT count(*) AS n FROM {} WHERE tenant_id = %(tenant_id)s AND id IN ({}){}"
    ).format(sql.Identifier(table), active_ids(table, at=at), _ceiling_sql(known_by))
    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id, CEILING_PARAM: known_by})
        return cur.fetchone()["n"]


def _read_table_public(
    conn: psycopg.Connection, table: str, tenant_id: UUID, *, known_by: datetime | None = None
) -> tuple[pa.Schema, list[dict], dict[str, int]]:
    """Read one fact table onto the PUBLIC clock: ``recorded_at`` becomes each version's public instant
    (``db.public_clock`` — the SAME ``public_versions_sql`` the live history read runs), the honest ingest
    instant rides beside it as ``ingested_at`` (exactly the live public read's row shape), and the version
    pile-up that creates is resolved HERE rather than left to the reader.

    **Why the resolution cannot be left to the reader.** Both as-of reads pick a version with
    ``ORDER BY recorded_at DESC, id DESC``. On the system clock the versions of one fact have DISTINCT
    ``recorded_at`` values, so the ``id`` tiebreak almost never fires. Rewriting ``recorded_at`` to the
    public clock can collapse them onto ONE value (a re-parse of one filing shares its acceptance), and then
    ``id`` decides — and ``id`` is ``uuid DEFAULT gen_random_uuid()`` on all fourteen fact tables, so "the
    tiebreak picks the latest content" is false: it picks a RANDOM one. MEASURED on the dev copy: that would
    have fired on 44,982 insider version-groups and 17,373 price version-groups (8,947 holding different
    closes) under the pre-2026-10-21 price clock, which put every version of a bar at its date.

    It would also silently corrupt the SCORING reader, which nobody would think to check:
    ``replay.scoring.RealizedPrices`` reads this same mirror with the same tiebreak and is deliberately
    NOT as-of capped, so the same random pick would land in the realized returns.

    **The resolution.** Per identity:

    1. Each version's public instant (``public_time_sql``): a Form 4's acceptance — the FACT's, on every
       version: the newest stored statement of it (a re-parse of one accession shares it, and a version
       re-dating a mis-captured time — ``pipeline.backfill_accepted --drift`` — moves the older ones with it);
       a bar's settle for its FIRST version and its own ``recorded_at`` for a re-version (a vendor correction
       counts from when it was published to us); a filing date for the filing tables; the ratification /
       sampling instant for the operator-ratified Workbench tables and the fund shares samples.
    2. (Retired 2026-10-24.) The NULL-fill used to be the MAX over the fact's versions and so raised on a
       fact carrying a NULL acceptance AND two different non-NULL ones (it could not tell which to fill). The
       rule now NAMES the winner — the newest statement, by honest ``recorded_at`` — so there is nothing left
       to attribute: the acceptance-clock repair writes exactly that shape (a NULL pre-capture version, the
       backfilled time, then the re-dated one), every correction carrying its ``supersedes`` link and its
       ``data_repair`` journal row.
    3. Partition by ``(identity, public instant)`` — NOT by identity alone. Including the instant is what
       preserves a genuine re-disclosure (a restated quarter at its own ``filed``; a corrected bar at its own
       publication), while a pure re-parse (the same instant, a later ``recorded_at``) collapses to our best.
    4. Keep the latest by the HONEST ``recorded_at`` within each group, then overwrite ``recorded_at`` with
       the public instant (``ingested_at`` := the honest one). After this no identity has two rows sharing
       a ``recorded_at``, so the random tiebreak has nothing left to decide — and the live public read's
       ``ORDER BY public DESC, recorded_at DESC, id DESC`` pick is exactly the mirror reader's.

    Returns the schema, the rows, and the four counts the run manifest reports — separate numbers because
    they answer separate questions: a 47% raw-row drop with 0.67% identities lost is a healthy export (the
    dropped rows were superseded versions), while a small row drop with a large identity loss is a hole.

    ``known_by`` is the FREEZE CEILING (``db.public_clock``): the store as it stood at that cut — every row
    recorded after it absent, and every cross-version read (a NULL-filled acceptance, a bar's first version,
    the tape edge and the split list behind a bar's scale) computed from the rows held then. Not a history
    gate: the public instant still decides what a view at ``T`` sees.
    """
    part_cols = partition_cols(table)
    part = sql.SQL(", ").join(sql.Identifier(c) for c in part_cols)
    versions = public_versions_sql(
        table,
        where=sql.SQL("tenant_id = %(tenant_id)s"),
        serve=False,
        ceiling=known_by is not None,
    )
    pub = sql.Identifier(PUBLIC_COL)
    query = sql.SQL(
        "WITH v AS ({versions}) "
        "SELECT *, ROW_NUMBER() OVER ("
        "    PARTITION BY {part}, {pub} ORDER BY recorded_at DESC, id DESC) AS _rn "
        "FROM v ORDER BY {part}, {pub}"
    ).format(versions=versions, part=part, pub=pub)

    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id, **public_params(known_by)})
        raw = cur.fetchall()
        schema = pa.schema(
            [
                (d.name, _OID_ARROW.get(d.type_code, pa.string()))
                for d in cur.description
                if not d.name.startswith("_")
            ]
            + [(INGESTED_COL, pa.timestamp("us", tz="UTC"))]
        )

    identities_total = len({tuple(r[c] for c in part_cols) for r in raw})
    kept, dropped_null_clock = [], 0
    survivors: set[tuple] = set()
    for r in raw:
        if r[PUBLIC_COL] is None:  # no version of this fact carries a disclosure instant
            dropped_null_clock += 1
            continue
        survivors.add(tuple(r[c] for c in part_cols))
        if r["_rn"] != 1:
            continue
        row = {k: v for k, v in r.items() if not k.startswith("_")}
        # THE REWRITE: the mirror's transaction axis becomes the public one. The reader and the gate are
        # untouched — they still read ``recorded_at``; it now means "when this became public". The honest
        # ingest instant rides as ``ingested_at`` (the live public read's shape — bookkeeping, never a gate).
        row[INGESTED_COL] = row["recorded_at"]
        row["recorded_at"] = r[PUBLIC_COL]
        kept.append(row)

    counts = {
        "rows_in": len(raw),
        "rows_out": len(kept),
        "rows_dropped_null_clock": dropped_null_clock,
        "versions_collapsed": len(raw) - dropped_null_clock - len(kept),
        "identities_lost": identities_total - len(survivors),
    }
    return schema, kept, counts


# --- R8-A — the insider-amendment AUX tables ------------------------------------------------------------
# What the replay resolver reads BESIDE ``fact_insider_txn`` (``domain.insider_amendments.resolve_insider_rows``,
# the ONE resolver both engines share): the Form 4/As as filed, their links, the operator's decisions, and
# every CIK a security has been known by (the other-issuer screen — IDENTITY, materialized from the master +
# the T7 CIK-change log, never as-of). Not fact tables (no valid-time natural key), so they sit outside
# ``FACT_TABLES`` and outside the public-clock registry above, with their own export rule below.
AUX_TABLES: tuple[str, ...] = (
    "insider_amendment",
    "insider_amendment_link",
    "insider_amendment_decision",
    "security_cik",
)
# The column a PUBLIC mirror adds to the three Form 4/A tables: each row's public instant
# (``db.public_clock.aux_public_sql`` — the 4/A and its link at the amendment's acceptance, a SYSTEM decision
# at that same publication, an OPERATOR decision when the operator made it). The mirror reader gates on it
# when present (``replay.pit``), while the ORDER the resolver reads stays the honest ``recorded_at`` — exactly
# the live public read (``db.insider_resolution``).
AUX_PUBLIC_COL = "public_at"

_SECURITY_CIK_SQL = (
    "SELECT id::text AS security_id, ltrim(cik, '0') AS cik FROM security_master "
    "WHERE tenant_id = %(t)s AND cik IS NOT NULL "
    "UNION SELECT security_id::text, ltrim(prior_cik, '0') FROM security_cik_change "
    "WHERE tenant_id = %(t)s AND prior_cik IS NOT NULL "
    "UNION SELECT security_id::text, ltrim(new_cik, '0') FROM security_cik_change "
    "WHERE tenant_id = %(t)s AND new_cik IS NOT NULL "
    "ORDER BY 1, 2"
)


def _read_aux(
    conn: psycopg.Connection,
    table: str,
    tenant_id: UUID,
    clock: str,
    *,
    known_by: datetime | None = None,
) -> tuple[pa.Schema, list[dict], int]:
    """One aux table for the mirror: ``(schema, rows, rows_dropped_null_clock)``.

    ``record`` — every row, every version, untouched (the parity gate's mode, like the fact tables).

    ``public`` — every row, every version, its honest ``recorded_at`` kept (the order the resolver reads: the
    newest link, the newest standing decision — so a re-link or a re-derivation resolves to today's reading
    and an operator verdict still overrules a system one), plus ``public_at``: the row's public instant from
    the SAME ``aux_public_sql`` the live public read gates on. The amendment and its link count from the 4/A's
    SEC acceptance (else its filing date); a SYSTEM decision from that same publication (the system's reading
    of a public filing under today's rules); an OPERATOR decision from when the operator made it — so a run
    replays the operator's pending window honestly instead of applying a later ratification from the filing
    date (the pre-2026-10-21 mirror's named hindsight). ``public_at`` is never NULL (each falls back to the
    row's own ``recorded_at``), so nothing is dropped.

    ``known_by`` (either clock) = the rows recorded by that cut, each public instant computed from the 4/A
    records + links held then (the freeze ceiling). ``security_cik`` is IDENTITY, never as-of: uncapped.
    """
    with conn.cursor() as cur:
        if table == "security_cik":
            cur.execute(_SECURITY_CIK_SQL, {"t": tenant_id})
        elif clock == "public":
            cur.execute(
                sql.SQL(
                    "SELECT x.*, {pub} AS {col} FROM {table} x WHERE x.tenant_id = %(t)s{ceil} "
                    "ORDER BY x.recorded_at, x.id"
                ).format(
                    pub=aux_public_sql(table, alias="x", ceiling=known_by is not None),
                    col=sql.Identifier(AUX_PUBLIC_COL),
                    table=sql.Identifier(table),
                    ceil=_ceiling_sql(known_by, "x"),
                ),
                {"t": tenant_id, **public_params(known_by)},
            )
        else:
            cur.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE tenant_id = %(t)s{} ORDER BY recorded_at, id"
                ).format(sql.Identifier(table), _ceiling_sql(known_by)),
                {"t": tenant_id, CEILING_PARAM: known_by},
            )
        rows = cur.fetchall()
        schema = pa.schema(
            [(d.name, _OID_ARROW.get(d.type_code, pa.string())) for d in cur.description]
        )
    return schema, rows, 0


# S2 — the SPLIT LIST (migration 0052) beside the fact tables: what the insider price screen reads
# (``PointInTimeData.split_events``) to put a Form 4's as-traded price on the tape's scale. Not a fact table
# (``db.bitemporal._FACT_IDENTITY`` stays the fact set, so ``connect_mirror``'s every-file rule is untouched):
# it rides as ONE optional file, written by every export since S2. A mirror from before has no file and reads
# as "no splits listed" — exactly what the system knew then, so a frozen backtest mirror still opens and
# replays as it always did.
SPLIT_TABLE = "security_split"


def _read_splits(
    conn: psycopg.Connection, tenant_id: UUID, clock: str, *, known_by: datetime | None = None
) -> tuple[pa.Schema, list[dict]]:
    """The split list for the mirror. ``record`` — every version, untouched (the parity mode). ``public`` —
    the newest version of each logical split, ``recorded_at`` = the split's PUBLIC instant and the honest
    stamp as ``ingested_at`` (``db.public_clock.split_versions_sql``: the SAME builder the live history read
    runs, ``split_repo.current_rows``) — a split counts from its own publication, and the reader's gate and
    pick are unchanged (one row per split). ``known_by`` = the list as it stood at that cut (the freeze
    ceiling).
    """
    if clock != "public":
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT * FROM security_split WHERE tenant_id = %(t)s{} ORDER BY recorded_at, id"
                ).format(_ceiling_sql(known_by)),
                {"t": tenant_id, CEILING_PARAM: known_by},
            )
            rows = cur.fetchall()
            schema = pa.schema(
                [(d.name, _OID_ARROW.get(d.type_code, pa.string())) for d in cur.description]
            )
        return schema, rows
    query = split_versions_sql(
        where=sql.SQL("s.tenant_id = %(tenant_id)s"), ceiling=known_by is not None
    )
    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id, **public_params(known_by)})
        raw = cur.fetchall()
        schema = pa.schema(
            [
                (d.name, _OID_ARROW.get(d.type_code, pa.string()))
                for d in cur.description
                if not d.name.startswith("_")
            ]
            + [(INGESTED_COL, pa.timestamp("us", tz="UTC"))]
        )
    return schema, [to_public_row(dict(r)) for r in raw]


# The IDENTITY aux (the Scoreboard editions, ``editions/``): ``(security_id, name, cik)`` from
# ``security_master`` — what the live PIT's ``security_name`` / ``security_cik`` read (identity, never as-of).
# Without it a mirror answers ``None`` there, and the insider issuer-self NAME screen and the activist
# self-filed screen read differently from the Postgres call the edition must equal (``replay/pit.py``).
# Written only when asked (``identity=True``), so a backtest mirror is byte-identical to before.
IDENTITY_TABLE = "security_identity"


def _read_identity(conn: psycopg.Connection, tenant_id: UUID) -> tuple[pa.Schema, list[dict]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text AS security_id, name, cik FROM security_master "
            "WHERE tenant_id = %(t)s ORDER BY id",
            {"t": tenant_id},
        )
        rows = cur.fetchall()
    schema = pa.schema([("security_id", pa.string()), ("name", pa.string()), ("cik", pa.string())])
    return schema, rows


def blind_detectors(excluded: list[str]) -> list[str]:
    """Which registered detectors read ONLY excluded tables — i.e. which ones cannot fire in this mirror.

    Named beside the excluded tables because listing the tables alone is half an answer: a reader still has
    to work out what stopped firing, and without that a result reads as "that detector is inert on this
    tape" when the truth is "that detector had no tape". Derived from each detector's own declared read
    horizons (``signals/horizons.py``), so it cannot fall behind the registry. A ROLE read of the Form 4 tape
    (``Detector.reads_insider_roles`` with no ``txn_codes`` — the 13D holder screen) only ever stops a fire,
    never makes one, so it does not count as a tape the detector can fire from.
    """
    if not excluded:
        return []
    from domain.config import DEFAULT_CONFIG
    from signals import registered_detectors
    from signals.horizons import INSIDER_TABLE

    gone = set(excluded)
    out = []
    for det in registered_detectors():
        tables = set(det.horizons(DEFAULT_CONFIG))
        if det.reads_insider_roles and not det.txn_codes:
            tables.discard(
                INSIDER_TABLE
            )  # a role read only screens; it can never make the detector fire
        if tables and tables <= gone:
            out.append(det.name)
    return sorted(out)


def export_snapshot(
    conn: psycopg.Connection,
    out_dir: str | Path,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    clock: Literal["record", "public"] = "record",
    known_by: datetime | None = None,
    identity: bool = False,
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

    ``"public"`` rewrites it to each version's public instant from ``db.public_clock`` (the definition the
    live history views share) and resolves the version pile-up that creates (see ``_read_table_public`` —
    this is where the random-``id`` tiebreak is closed); the Form 4/A tables carry ``public_at``. A table with
    no declared clock is excluded WHOLE and named in the manifest with the detectors it blinds. The fact
    reader and its gate are untouched in both modes: the axis is chosen once, when the mirror is
    materialized.

    ``known_by`` (either clock) is the FREEZE CEILING (``db.public_clock.CEILING_PARAM``) — the Scoreboard
    editions' cut: the store as it stood at that instant, so an edition can be recomputed later on exactly the
    rows it first saw (the store is append-only). Not a history gate: on the public clock the public instant
    still decides what a view sees. ``None`` = no ceiling, byte-identical to the export before it existed.

    ``identity`` also writes the ``security_identity`` aux (``IDENTITY_TABLE``), so the mirror answers
    ``security_name`` / ``security_cik`` as the live PIT does. Off by default (a backtest mirror is unchanged).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    excluded = [t for t in FACT_TABLES if clock == "public" and t not in PUBLIC_TIME]
    manifest: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "clock": clock,
        "tables": {},
        "excluded_tables": excluded,
        "blind_detectors": blind_detectors(excluded),
    }
    if (
        known_by is not None
    ):  # only when set, so a plain export's manifest is byte-identical to before
        manifest["known_by"] = known_by.isoformat()
    for table in FACT_TABLES:
        if table in excluded:
            continue
        counts: dict[str, int] = {}
        if clock == "public":
            schema, rows, counts = _read_table_public(conn, table, tenant_id, known_by=known_by)
        else:
            schema, rows = _read_table(conn, table, tenant_id, known_by=known_by)
        if has_set_aside(table):
            # the versions left out as SET ASIDE (0062) — named, so the mirror says what it does not hold
            # (under the freeze ceiling: what the CUT left out, the set-asides as they stood at it)
            counts["rows_set_aside"] = _count_set_aside(conn, table, tenant_id, known_by=known_by)
        coerced = [{k: _coerce(v) for k, v in row.items()} for row in rows]
        pq.write_table(pa.Table.from_pylist(coerced, schema=schema), out / f"{table}.parquet")
        max_rec = max((r["recorded_at"] for r in rows), default=None)
        manifest["tables"][table] = {
            "rows": len(rows),
            "max_recorded_at": max_rec.isoformat() if max_rec is not None else None,
            **counts,
        }
    # R8-A — the insider-amendment AUX tables, ALWAYS written (empty or not), so a mirror either has all of
    # them or is from before they existed (``replay.pit.connect_mirror`` refuses a partial set)
    manifest["aux_tables"] = {}
    for table in AUX_TABLES:
        schema, rows, dropped = _read_aux(conn, table, tenant_id, clock, known_by=known_by)
        coerced = [{k: _coerce(v) for k, v in row.items()} for row in rows]
        pq.write_table(pa.Table.from_pylist(coerced, schema=schema), out / f"{table}.parquet")
        manifest["aux_tables"][table] = {"rows": len(rows), "rows_dropped_null_clock": dropped}
    # S2 — the split list, ALWAYS written (empty or not), so a mirror either has it or is from before it
    schema, rows = _read_splits(conn, tenant_id, clock, known_by=known_by)
    coerced = [{k: _coerce(v) for k, v in row.items()} for row in rows]
    pq.write_table(pa.Table.from_pylist(coerced, schema=schema), out / f"{SPLIT_TABLE}.parquet")
    manifest["split_table"] = {"rows": len(rows)}
    if (
        identity
    ):  # see IDENTITY_TABLE — only when asked, so a backtest mirror's manifest is unchanged
        schema, rows = _read_identity(conn, tenant_id)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), out / f"{IDENTITY_TABLE}.parquet")
        manifest["identity_table"] = {"rows": len(rows)}
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


@dataclass(frozen=True)
class MirrorManifest:
    """What a materialized mirror SAYS ABOUT ITSELF — read back, not re-derived.

    **The clock is a property of the MIRROR, and a run inherits it.** It is chosen once, when the Parquet
    is written, and after that no caller can change it: the exported ``recorded_at`` already carries that
    axis. So everything downstream READS this rather than accepting a flag of its own, because a flag that
    can disagree with the bytes will eventually disagree with the bytes — and a run mislabeled `public`
    over a record-clock tape is a result nobody can catch by looking at it.

    ``clock`` defaults to ``"record"`` when the manifest predates the field, which is honest: a mirror
    written before the public clock existed WAS the system clock.
    """

    clock: Literal["record", "public"] = "record"
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    excluded_tables: list[str] = field(default_factory=list)
    blind_detectors: list[str] = field(default_factory=list)
    # S2 — the exporter wrote ``security_split.parquet`` (it always does since S2, and says so). True makes the
    # file REQUIRED at open: a lost file must fail loudly, never replay as "no splits listed" (the B5 review
    # fix). A run manifest carries it too (``backtest.manifest.MirrorInfo.split_table``), so a run directory
    # re-opened as a mirror is held to the same rule; a run from before S2 reads False.
    split_table: bool = False
    split_rows: int | None = None
    # the exporter wrote ``security_identity.parquet`` (``identity=True``): REQUIRED at open, like the split
    # file — a lost file must fail loudly, never answer ``None`` where the edition read a name
    identity_table: bool = False


def read_mirror_manifest(mirror_dir: str | Path) -> MirrorManifest | None:
    """The mirror's own manifest, or ``None`` when there is none or it cannot be read.

    Reads TWO shapes from the same filename, and it has to, because they share one. ``MANIFEST_NAME`` is
    ``manifest.json`` for both this exporter and ``backtest.manifest``, and a run that exports its OWN
    mirror writes both into the one directory — the run manifest last. So a finished run directory holds a
    RUN manifest where a mirror manifest used to be, and re-opening it as a mirror (which
    ``replay.pit.connect_mirror`` legitimately does) has to find the exclusion list anyway. A run manifest
    is recognized by its ``run_id`` and carries the same facts under ``mirror``, which is exactly why
    ``BacktestManifest`` records them.

    ``None`` means "no manifest", which callers read as "assume nothing was excluded" — so a file missing
    for any OTHER reason still fails loudly downstream rather than yielding a mirror that silently answers
    nothing for that table.
    """
    path = Path(mirror_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(blob, dict):
        return None
    inner = blob.get("mirror") if "run_id" in blob else blob
    if not isinstance(inner, dict):
        inner = {}
    clock = blob.get("clock", "record")
    return MirrorManifest(
        clock="public" if clock == "public" else "record",
        tables=inner.get("tables") or {},
        excluded_tables=list(inner.get("excluded_tables") or []),
        blind_detectors=list(inner.get("blind_detectors") or []),
        # present and not null in either shape (an exporter's ``{"rows": N}``, a run's ``MirrorInfo``)
        split_table=isinstance(inner.get("split_table"), dict),
        split_rows=(inner.get("split_table") or {}).get("rows"),
        identity_table=isinstance(inner.get("identity_table"), dict),
    )


def mirror_clock(mirror_dir: str | Path) -> Literal["record", "public"]:
    """Which axis this mirror's ``recorded_at`` carries. A mirror with no manifest is the system clock —
    the only thing this exporter wrote before the public mode existed."""
    m = read_mirror_manifest(mirror_dir)
    return m.clock if m else "record"

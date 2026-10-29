from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from operator import itemgetter
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

from db import insider_resolution
from db.public_clock import (
    INGESTED_COL,
    PUBLIC_COL,
    Clock,
    public_params,
    public_versions_sql,
    to_public_row,
)
from db.set_aside import exclusion_sql

# Whitelist of bitemporal fact tables -> the natural-key columns that identify one logical fact
# (so an as-of read keeps the latest *version* of each, by recorded_at). Whitelisting also keeps
# the dynamic SQL injection-safe.
_FACT_IDENTITY: dict[str, list[str]] = {
    "fact_insider_txn": ["accession", "insider_name", "valid_from", "txn_seq"],
    "fact_price_eod": ["security_id", "d"],
    "fact_dilution": ["accession"],  # one convert offering per accession
    "fact_catalyst": [
        "source_ref"
    ],  # one catalyst per source (accession / award id / ratified URL)
    "fact_theme_conviction": ["source_ref"],  # one theme conviction per source (ratified doc / URL)
    "fact_revenue_mix": [
        "source_ref"
    ],  # one revenue-mix fact per source (10-K segment) — Workbench purity
    "fact_shares_outstanding": [
        "source_ref"
    ],  # one shares fact per source (10-Q cover) — Workbench mkt cap
    "fact_cash_burn": ["source_ref"],  # one cash/burn fact per source (10-Q) — Workbench runway
    "fact_fund_shares": [
        "security_id",
        "d",
    ],  # one fund shares-out sample per day (ETF net flow) — a restated count is a new version
    "fact_fundamentals": [
        "security_id",
        "metric_key",
        "period_end",
    ],  # one financial fact per (security, metric, fiscal period) — a restatement is a new version (§2.2)
    "fact_corporate_event": [
        "accession"
    ],  # one 8-K filing per accession (within the security scope) — items resolving is a new version
    "fact_activist_stake": [
        "accession"
    ],  # one 13D/G filing per accession (within the SUBJECT security scope) — filer/pct resolving is a new version (S5)
    "fact_filing_index": [
        "accession"
    ],  # one EDGAR index row per accession (within the security scope) — insert-only, display only (0070)
}

# The fact tables NO detector reads — DISPLAY data only (the research dashboards), kept OUT of the replay mirror.
# ``replay.export.FACT_TABLES`` (the mirror's table set) excludes them, because ``replay.pit.connect_mirror``
# REQUIRES a parquet file for every table in that set: registering a display-only table there would make every
# frozen mirror written before it (the backtest archives, the editions run dirs) fail to open, and no replay
# reader could ever use the rows. They stay in ``_FACT_IDENTITY`` (the as-of read's whitelist, the public clock's
# registry) so the live point-in-time view reads them with both clocks like any fact.
DISPLAY_ONLY_FACT_TABLES: frozenset[str] = frozenset({"fact_filing_index"})

# The KNOWABILITY axis per table — the SQL expression for the transaction-time no-lookahead gate
# (``{expr} <= known_at``): "which rows had THIS SYSTEM actually recorded as of the pinned read time?".
# EVERY fact table gates on ``recorded_at`` (our ingest instant) — the strict "what we held" axis, and the
# SINGLE no-lookahead definition across all tables (a mirror test,
# ``test_mirror_has_no_valid_time_lookahead_corporate_events``, pins that strictness).
#
# ``fact_insider_txn`` deliberately does NOT special-case this onto ``COALESCE(accepted, recorded_at)``
# (PR #283 tried that; reverted for one strict definition everywhere). ``accepted`` (the SEC acceptance
# datetime) stays a DISPLAY + metrics column ONLY: the Scoreboard's honest ``disclosed`` line
# (``scoreboard/overlays.py``) and the B2 disclosure-lag metric (``scoreboard/provenance.py``) read it
# DIRECTLY, never through this gate. It must never gate the as-of read — insider rows are re-versioned in
# place by backfills (0037/0040), so a v2 carrying the SAME ``accepted`` but a LATER ``recorded_at`` would
# clear a ``COALESCE(accepted, …)`` gate BEFORE it was recorded, and the ``ORDER BY recorded_at DESC``
# version-pick would then surface it — the same latent lookahead the 8-K resolve carries. Gating on
# ``recorded_at`` closes that. (Live reads pin ``known_at = now`` where ``accepted <= recorded_at <= now``,
# so the choice is inert on every live path; it matters only for an honest past-as-of replay.)
#
# The map is intentionally EMPTY — every table falls through to ``_DEFAULT_KNOWABILITY_EXPR``. It stays as
# the injection-safe seam for any future per-table override (a trusted literal, never caller input); both
# engines (Postgres ``_as_of`` + DuckDB ``ReplayPointInTimeData._as_of``) render whatever it returns
# BYTE-IDENTICALLY, so the replay-parity gate stays provable.
#
# THIS IS THE RECORD CLOCK — the LIVE read's gate (``asof`` = today, ``known_at = now``), where it coincides
# with the public one. HISTORY is judged on the PUBLIC clock (operator decision 2026-10-21, INVARIANTS §4):
# a past-asof view reads with ``clock="public"``, which gates each version on its PUBLIC instant
# (``db.public_clock`` — the filing / acceptance / settled session; a correction from its own publication)
# instead of ``recorded_at``. The paragraphs above are the record-clock history of this gate.
_KNOWABILITY_EXPR: dict[str, str] = {}
_DEFAULT_KNOWABILITY_EXPR = "recorded_at"


def knowability_expr(table: str) -> str:
    """The transaction-time no-lookahead gate column/expression for ``table`` (``_KNOWABILITY_EXPR`` above,
    else ``recorded_at`` — currently every table). ONE source of truth, imported by both the Postgres and
    DuckDB as-of reads so their WHERE gate is identical (replay parity)."""
    return _KNOWABILITY_EXPR.get(table, _DEFAULT_KNOWABILITY_EXPR)


# --- FIRST KNOWABILITY (D3a) — a re-reported, UNCHANGED number is not new knowledge ----------------------
#
# The XBRL fundamentals ingest versions every ``(metric, period_end, filed)``: when a later 10-Q/10-K re-reports
# a period as its prior-year comparative, that comparative is a NEW VERSION with ``valid_from = recorded_at =
# the later filing``, and the latest-version pick hands a detector that later date as the period's "filed". So
# a year-old revenue inflection re-fired as fresh for another 180 days (MEASURED on prod 2026-10-16: 12,572 of
# 14,002 later revenue versions are value-identical to the version before them; GEV's Q1-2025 revenue of
# $8,032M was filed 2025-04-23 and re-reported 2026-04-22, and the 2026-10-01 Nuclear Buildout card cited it
# dated 2026-04-22).
#
# The rule (operator-approved 2026-10-16, rule (a)): a fact's knowability date is the EARLIEST knowable date
# in the trailing run of value-identical versions that ends at the version the as-of read picked. Every input
# is a version the as-of read could already see (``valid_from <= asof AND recorded_at <= known_at``), so a
# version recorded after ``known_at`` can influence nothing — not the pick, not the run, not the date.
#
# A version's own knowable date is its PUBLIC date — ``valid_from``, the filing's acceptance date — never the
# day this system happened to record it (the public clock judges history; ``recorded_at`` is bookkeeping —
# INVARIANTS.md §4, operator decision 2026-10-21, which also approved retiring the held-date term below). The
# store stamp only bounds which rows the gate hands in (``recorded_at <= known_at``; at the live nightly's
# ``asof = today`` the two clocks coincide). Until migration 0057 the ingest stamped ``recorded_at = filed
# 00:00 UTC`` (a backdated stamp — MEASURED: all 120,876 prod rows), and this date read ``max(valid_from, market
# date of recorded_at)``, a second term inert on every such row (filed 00:00 UTC is the evening BEFORE in
# market time). The ingest now stamps the honest transaction time (``ingest.fundamentals.store_quarters``),
# and that second term would have turned live: a name onboarded today would date every historical quarter
# TODAY — the phantom-arm class D3a closed (a year-old inflection firing as fresh), re-opened through the
# onboarding door — and a filing ingested days late would stay live days longer. Dating by the filing keeps
# every signal exactly where the backdated stamp put it, on every row old and new.
#
# Equality is EXACT on the stored number (compared as float on both engines, which is lossless for these
# integer XBRL values and keeps the Postgres numeric and the mirror's float64 in lockstep). Any nonzero
# difference counts as new knowledge at its own date — the only choice under which an event is never dated
# before the exact number it rests on was public. MEASURED: 0 consecutive version pairs differ by <= 1e-9
# relative (there is no float jitter to absorb), while 7 revenue pairs differ by <= 1e-6 (a precision change:
# 84,182,954 re-reported as 84,183,000) — those stay new knowledge, a named residual.
#
# THE PICK is the latest FILING the read can see: versions ordered ``valid_from, recorded_at, id`` and the
# last one wins. With the backdated stamp that was byte-for-byte the ``DISTINCT ON ... recorded_at DESC, id
# DESC`` pick (``recorded_at`` was a strictly increasing function of ``valid_from``, so the two orders — ties
# and all — coincide on every existing row). With the honest stamp they would not: one pass writes several
# filings of one period in ONE transaction (MEASURED: 20,752 prod identities hold >= 2 versions written
# together), all sharing one ``recorded_at``, and ``id`` is a random uuid — so ordering by ``recorded_at``
# would hand the pick to a coin flip, and a late-recorded OLDER filing would outrank a newer one. The row
# carries two more keys; detectors date, cite and age a fact by them, never by the picked ``valid_from``.
#
# the first knowable date of the row's CURRENT value (a date)
KNOWABLE_FROM = "knowable_from"
# the filing that first made that value knowable (provenance, #6)
KNOWABLE_SOURCE = "knowable_accession"


@dataclass(frozen=True)
class _FirstKnowability:
    # the columns whose equality makes two versions "the same fact re-reported"
    value_cols: tuple[str, ...]
    # the column naming the filing that carried a version (-> KNOWABLE_SOURCE)
    source_col: str
    # the version's PUBLIC date: its own knowable date, and the leading key of the version order
    public_col: str


# The tables whose as-of read carries the first-knowability annotation — a trusted whitelist like
# ``_FACT_IDENTITY``. Both engines (Postgres here, DuckDB ``replay/pit.py``) branch on it and post-process
# through the SAME pure ``first_knowable_versions`` below, so replay parity holds by construction.
_FIRST_KNOWABILITY: dict[str, _FirstKnowability] = {
    "fact_fundamentals": _FirstKnowability(
        value_cols=("value",), source_col="accession", public_col="valid_from"
    ),
}


def has_first_knowability(table: str) -> bool:
    """Does the as-of read of ``table`` carry the D3a first-knowability annotation?"""
    return table in _FIRST_KNOWABILITY


def first_knowable_order(table: str) -> str:
    """The SQL ORDER BY tail (after the partition columns) both engines pre-sort a first-knowability read by:
    the version order itself — public date, then ``recorded_at``, then ``id`` — so the Python pick re-sorts
    nothing in the common case. Rendered from the trusted ``_FIRST_KNOWABILITY`` literals, never input.
    """
    return f"{_FIRST_KNOWABILITY[table].public_col}, recorded_at, id"


def _same_number(v: Any) -> Any:
    """The equality key for one value column: numerics as float (Postgres numeric -> Decimal, the mirror ->
    float64 — one representation on both engines), anything else as-is."""
    return float(v) if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool) else v


def version_knowable_date(row: dict[str, Any], table: str = "fact_fundamentals") -> date:
    """One version's own knowable date: its PUBLIC date (for ``fact_fundamentals``, ``valid_from`` = the
    filing's acceptance date), never the instant this system recorded it. Whether the version is HELD at all
    is the gate's question (``recorded_at <= known_at``), settled before this is ever asked (module note).
    """
    return row[_FIRST_KNOWABILITY[table].public_col]


def first_knowable_versions(
    table: str,
    columns: Sequence[str],
    versions: Iterable[Sequence[Any]],
    partition: Sequence[str],
) -> list[dict[str, Any]]:
    """Pure: every as-of-VISIBLE version of ``table`` (already gated on ``valid_from <= asof`` and the
    knowability column ``<= known_at``) -> the latest version per natural key, annotated (D3a).

    - **The version order** is ascending ``public date, recorded_at, id`` (``first_knowable_order``): the
      latest FILING last, a same-filing re-record after its predecessor, ``id`` breaking an exact tie
      (``str(id)`` because the mirror stores uuids as VARCHAR; canonical lowercase-hex text sorts exactly like
      Postgres's byte-wise uuid order, so both engines break a tie identically).
    - **The pick** is the last version in that order, all of its columns untouched. On every row stamped
      before migration 0057 it IS the ``DISTINCT ON ... ORDER BY recorded_at DESC, id DESC`` row (module
      note).
    - **The run** is the trailing stretch of versions, ending at the pick, whose value columns equal the
      pick's. A different value anywhere before it ends the run (a restatement, or a restatement reverted —
      each is new knowledge at its own date). Only each public date's latest-recorded version takes part:
      a superseded parse of a filing is not a separate filing (the per-filing collapse, below).
    - **The annotation** is the run's FIRST version: its public date (``KNOWABLE_FROM`` — the earliest in
      the run, the order being ascending by it; ``version_knowable_date``) and its filing
      (``KNOWABLE_SOURCE``).

    ``versions`` are TUPLE rows in ``columns`` order — what both engines' cursors return natively — and only
    the picks become dicts (``{column: value}``, exactly the shape the ``dict_row`` read returned). Output
    order follows the first appearance of each natural key — the callers' SQL orders by the partition,
    exactly the ``DISTINCT ON`` output order. Inputs are not mutated.

    Written for the basket prefetch's volume (~38k versions for a 190-name thesis): tuple rows,
    ``itemgetter`` keys, a ``(public date, recorded_at)`` sort that falls back to the full key (``id`` too)
    only on a tie, and no walk for a single version.
    """
    spec = _FIRST_KNOWABILITY[table]
    columns = list(columns)
    at = {c: i for i, c in enumerate(columns)}
    pub_i, rec_i, id_i, src_i = (
        at[spec.public_col],
        at["recorded_at"],
        at["id"],
        at[spec.source_col],
    )
    key_of = itemgetter(*(at[c] for c in partition))
    order_of = itemgetter(pub_i, rec_i)
    value_of = itemgetter(*(at[c] for c in spec.value_cols))  # a scalar for one column
    multi = len(spec.value_cols) > 1

    def same_value(row: Sequence[Any]) -> Any:
        v = value_of(row)
        return tuple(_same_number(x) for x in v) if multi else _same_number(v)

    def version_order(row: Sequence[Any]) -> tuple[Any, Any, str]:
        return (row[pub_i], row[rec_i], str(row[id_i]))

    groups: dict[Any, list[Sequence[Any]]] = {}
    for row in versions:
        k = key_of(row)
        group = groups.get(k)
        if group is None:
            groups[k] = [row]
        else:
            group.append(row)

    out: list[dict[str, Any]] = []
    for group in groups.values():
        if len(group) == 1:
            pick = first = group[0]
        else:
            group.sort(key=order_of)
            if any(order_of(a) == order_of(b) for a, b in zip(group, group[1:])):
                group.sort(
                    key=version_order
                )  # an exact (public date, recorded_at) tie: the id decides
            pick = group[-1]
            # ONE version per public date — per FILING, for fundamentals — its latest-recorded: a same-filing
            # re-parse (the revenue-label supersession, migration 0060) replaces that filing's earlier parse,
            # exactly as the public read collapses a fact's versions sharing one public instant
            # (``_public_read``). Without it an old tag's superseded number sitting between two filings of the
            # current tag's number breaks the run, and the current number dates at its year-later comparative
            # (the D3 phantom shape — late, never early). The group is in version order, so a public date's
            # last version is its latest-recorded, and the pick (the last of all) always survives. A no-op on
            # every filing holding one version (all of them before 0060).
            group = [r for r, nxt in zip(group, group[1:]) if r[pub_i] != nxt[pub_i]] + [pick]
            target = same_value(pick)
            start = len(group) - 1
            while start > 0 and same_value(group[start - 1]) == target:
                start -= 1
            first = group[start]  # ascending by public date: the run's earliest filing
        row = dict(zip(columns, pick))
        row[KNOWABLE_FROM] = first[pub_i]
        row[KNOWABLE_SOURCE] = first[src_i]
        out.append(row)
    return out


def _as_of(
    conn: psycopg.Connection,
    table: str,
    *,
    scope_col: str,
    scope_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
    clock: Clock = "record",
) -> list[dict[str, Any]]:
    """Shared bitemporal point-in-time read, scoped by ``scope_col`` — no lookahead on either axis.

    ``clock="public"`` (a HISTORY view — the past-asof serve path, the backfill) judges the transaction axis on
    each version's PUBLIC instant instead of ``recorded_at`` (``_public_read``; ``db.public_clock``). The
    default ``"record"`` is the live read, byte-for-byte what follows.

    Returns the latest version (by ``recorded_at``) of each logical fact whose event time
    (``valid_from``) is on/before ``asof`` AND whose ingest time (``recorded_at``) is on/before
    ``known_at``. ``known_at`` is what makes replay honest: a correction recorded after it cannot
    leak into a read pinned at an earlier transaction time. ``scope_col`` is a TRUSTED literal
    ('security_id' / 'thesis_id') from the wrappers below, never caller input — kept injection-safe
    via ``sql.Identifier``.

    On a ``recorded_at`` tie (two versions of one natural key recorded at the same instant), the row with
    the greater ``id`` wins: a deterministic secondary sort so the read is reproducible and the DuckDB
    replay mirror, which applies the identical ``recorded_at DESC, id DESC`` ordering, agrees row-for-row.

    A ``_FIRST_KNOWABILITY`` table (``fact_fundamentals``) reads every VISIBLE version under the SAME gate
    and picks in Python (``first_knowable_versions``): the latest FILING held — ordered by its public date,
    not by ``recorded_at`` — plus the D3a annotation.

    ``fact_insider_txn`` is then RESOLVED against the Form 4/A amendments, links and operator decisions
    recorded at/before ``known_at`` (R8-A, ``db.insider_resolution`` -> the pure
    ``domain.insider_amendments.resolve_insider_rows``, shared with the replay mirror): a suppressed original
    leaves the read, an applied correction's rows replace it, an other-issuer row is screened. A security no
    amendment touches is returned unchanged.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    if clock == "public":
        rows = _public_read(
            conn,
            table,
            where=sql.SQL(
                "tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s AND valid_from <= %(asof)s"
            ).format(scope=sql.Identifier(scope_col)),
            params={"tenant_id": tenant_id, "scope_id": scope_id, "asof": asof},
            known_at=known_at,
            partition=_FACT_IDENTITY[table],
        )
        if table == insider_resolution.RESOLVED_TABLE and scope_col == "security_id":
            inputs = insider_resolution.load_inputs(
                conn, [scope_id], known_at=known_at, tenant_id=tenant_id, clock=clock
            )
            rows = inputs.resolve(scope_id, rows, asof=asof, lower=None)
        return rows
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in _FACT_IDENTITY[table])
    # the RECORD-clock knowability gate — recorded_at for every table (the strict "what we held" axis; the
    # live read). A public-clock history read took the branch above, where an insider fact's gate is its
    # ``accepted`` (``db.public_clock``); the version-pick ORDER BY is unchanged
    knowability = sql.SQL(knowability_expr(table))  # noqa: S608 — hardcoded map, never caller input
    first_knowable = has_first_knowability(table)
    template = (
        # every visible version (the run walk needs the history), pre-sorted in the Python pick's own order
        "SELECT * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s{set_aside} "
        "ORDER BY {ident}, {version_order}"
        if first_knowable
        else "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s{set_aside} "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    )
    parts: dict[str, sql.Composable] = {
        "ident": ident,
        "table": sql.Identifier(table),
        "scope": sql.Identifier(scope_col),
        "knowability": knowability,
        # a set-aside version (migration 0062, ``db.set_aside``) is skipped BEFORE the pick and the D3a walk,
        # so the read falls back to the version it superseded; the empty SQL for every other table
        "set_aside": exclusion_sql(table),
    }
    if first_knowable:  # a trusted literal from the whitelist, never caller input
        parts["version_order"] = sql.SQL(first_knowable_order(table))  # noqa: S608
    query = sql.SQL(template).format(**parts)
    params = {"tenant_id": tenant_id, "scope_id": scope_id, "asof": asof, "known_at": known_at}
    if first_knowable:
        return _first_knowable_read(conn, table, query, params, _FACT_IDENTITY[table])
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    if table == insider_resolution.RESOLVED_TABLE and scope_col == "security_id":
        inputs = insider_resolution.load_inputs(
            conn, [scope_id], known_at=known_at, tenant_id=tenant_id
        )
        rows = inputs.resolve(scope_id, rows, asof=asof, lower=None)
    return rows


def _first_knowable_read(
    conn: psycopg.Connection,
    table: str,
    query: sql.Composable,
    params: dict[str, Any],
    partition: Sequence[str],
) -> list[dict[str, Any]]:
    """Run an all-visible-versions query on a TUPLE-row cursor and pick + annotate through the shared pure
    function. Tuple rows because most of the versions are discarded: only the picks are built into dicts —
    the same ``{column: value}`` shape the ``dict_row`` read returns (MEASURED on prod, min-of-10: ~0.71 s
    with every version built as a dict vs ~0.57 s this way, for a 190-name basket's 38k versions).
    """
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(query, params)
        columns = [d.name for d in cur.description]
        return first_knowable_versions(table, columns, cur.fetchall(), partition)


_PICK_COL = "_pick"


def _public_read(
    conn: psycopg.Connection,
    table: str,
    *,
    where: sql.Composable,
    params: dict[str, Any],
    known_at: datetime,
    partition: Sequence[str],
) -> list[dict[str, Any]]:
    """The PUBLIC-clock as-of read (a history view): every version ``where`` admits gets its public instant
    (``db.public_clock.public_versions_sql`` — the SAME SQL the backtest's public mirror materializes), the
    gate is ``public <= known_at``, and the pick is the NEWEST version public by then (``public DESC``, then
    our best parse of it, ``recorded_at DESC, id DESC``). Returned rows carry ``recorded_at`` = that public
    instant — the axis the read was judged on, exactly the mirror's rewrite, so replay parity holds row-for-row.

    A ``_FIRST_KNOWABILITY`` table first collapses each fact's versions sharing one public instant to our best
    parse (the mirror's export-time resolution), rewrites, and walks the result through the shared pure
    ``first_knowable_versions`` — so D3a's knowable date is judged on the public clock too (its held-date term
    reads the public instant, which for a filing-dated table is the filing day: inert)."""
    versions = public_versions_sql(table, where=where)
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in partition)
    pub = sql.Identifier(PUBLIC_COL)
    params = {**params, "known_at": known_at, **public_params()}
    if has_first_knowability(table):
        query = sql.SQL(
            "WITH v AS ({versions}), c AS ("
            "  SELECT *, row_number() OVER (PARTITION BY {ident}, {pub} "
            "    ORDER BY recorded_at DESC, id DESC) AS {pick} "
            "  FROM v WHERE {pub} <= %(known_at)s"
            ") SELECT * FROM c WHERE {pick} = 1 ORDER BY {ident}, {pub}, id"
        ).format(versions=versions, ident=ident, pub=pub, pick=sql.Identifier(_PICK_COL))
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(query, params)
            columns = [d.name for d in cur.description]
            raw = cur.fetchall()
        # the rewrite (``to_public_row``'s, on tuple rows): recorded_at := the public instant, the honest
        # ingest instant appended as ``ingested_at``, the helper columns dropped
        rec_i, pub_i = columns.index("recorded_at"), columns.index(PUBLIC_COL)
        keep = [i for i, c in enumerate(columns) if c not in (PUBLIC_COL, _PICK_COL)]
        rows = [(*(r[pub_i] if i == rec_i else r[i] for i in keep), r[rec_i]) for r in raw]
        out_cols = [columns[i] for i in keep] + [INGESTED_COL]
        return first_knowable_versions(table, out_cols, rows, partition)
    query = sql.SQL(
        "WITH v AS ({versions}) "
        "SELECT DISTINCT ON ({ident}) * FROM v WHERE {pub} <= %(known_at)s "
        "ORDER BY {ident}, {pub} DESC, recorded_at DESC, id DESC"
    ).format(versions=versions, ident=ident, pub=pub)
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [to_public_row(r) for r in cur.fetchall()]


def as_of(
    conn: psycopg.Connection,
    table: str,
    *,
    security_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
    clock: Clock = "record",
) -> list[dict[str, Any]]:
    """Bitemporal as-of read for a SECURITY-scoped fact table. **Behavior-identical** to the original
    single-function ``as_of`` (same inputs, same rows); it delegates to the shared ``_as_of``. The only
    SQL difference versus that original is cosmetic: the scope column is rendered quoted via
    ``sql.Identifier`` (``"security_id"``), semantically identical for the lowercase fact-table columns.
    ``clock="public"`` is the history read (``_as_of``).
    """
    return _as_of(
        conn,
        table,
        scope_col="security_id",
        scope_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
        clock=clock,
    )


def as_of_many(
    conn: psycopg.Connection,
    table: str,
    *,
    security_ids: Collection[UUID],
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
    valid_from_lower: date | None = None,
    clock: Clock = "record",
) -> dict[UUID, list[dict[str, Any]]]:
    """The BATCH twin of ``as_of``: ONE query for a whole basket of securities, the SAME bitemporal gate.

    Row-for-row equivalent to calling ``as_of`` once per id — same WHERE gate (``valid_from <= asof AND
    <knowability> <= known_at``, tenant-filtered exactly like ``_as_of``), the same latest-version-per-
    natural-key pick, the same deterministic ``recorded_at DESC, id DESC`` tiebreak, and the same
    within-security row ORDER: the ``DISTINCT ON`` partition is the per-security natural key with
    ``security_id`` prefixed, so each security's partition (and its ``ORDER BY`` prefix) is exactly what
    the scoped read sees. Returns an entry for EVERY requested id — ``[]`` when it has no rows — so a
    caller can memoize "nothing on file" and never re-query.

    ``valid_from_lower`` is an OPTIONAL event-time floor (``valid_from >= lower``): the horizon-registry
    read bound (``signals/horizons.py``). It is version-safe on the tables it is used for because every
    version of one logical fact shares its ``valid_from`` (``fact_price_eod``: ``valid_from = d`` and
    ``d`` is the natural key; ``fact_insider_txn``: ``valid_from`` IS a natural-key column), so a floor
    can only drop whole facts older than the floor — never change which VERSION of a fact wins. The
    floor is never applied by this function on its own initiative: it is a caller-supplied, registry-
    derived bound (or None), and a caller that trims further does so in Python (the memo rule).

    A ``_FIRST_KNOWABILITY`` table reads every visible version in ONE query and picks + annotates in Python
    (``first_knowable_versions``) — row-for-row the scoped ``as_of``. Its floor (never derived today:
    ``fact_fundamentals`` is not a ``BOUNDED_TABLES`` member) is applied AFTER the pick, to the picked row's
    ``valid_from``: ``valid_from`` varies across that table's versions, so a SQL floor could cut the version
    history the run walks and date a fact later than it was knowable. Post-pick it is exactly the per-security
    ``as_of`` trimmed in Python — the PIT fallback's own rule.

    ``fact_insider_txn`` is resolved against the Form 4/A amendments exactly as the scoped ``as_of`` does
    (R8-A), in ONE load for the whole basket, with the floor applied to the amended rows too.

    ``clock="public"`` is the history read — row-for-row the scoped public ``as_of`` per id (``_public_read``).
    Its floor rides in the SQL for every table but a first-knowability one: on the two tables the registry
    bounds (price, insider) ``valid_from`` is part of the identity, so the floor admits or drops a fact's
    versions together and the cross-version public instant is unchanged.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    ids = list(dict.fromkeys(security_ids))  # de-duplicated, order-preserving
    out: dict[UUID, list[dict[str, Any]]] = {sid: [] for sid in ids}
    if not ids:
        return out
    partition = ["security_id"] + [c for c in _FACT_IDENTITY[table] if c != "security_id"]
    if clock == "public":
        first_knowable = has_first_knowability(table)
        floor = (
            sql.SQL(" AND valid_from >= %(lower)s")
            if valid_from_lower is not None and not first_knowable
            else sql.SQL("")
        )
        rows = _public_read(
            conn,
            table,
            where=sql.SQL(
                "tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) "
                "AND valid_from <= %(asof)s{floor}"
            ).format(floor=floor),
            params={"tenant_id": tenant_id, "ids": ids, "asof": asof, "lower": valid_from_lower},
            known_at=known_at,
            partition=partition,
        )
        for row in rows:
            if first_knowable and valid_from_lower is not None:
                if row["valid_from"] < valid_from_lower:
                    continue  # the post-pick floor (the record read's first-knowability rule)
            out[row["security_id"]].append(row)
        if table == insider_resolution.RESOLVED_TABLE:
            inputs = insider_resolution.load_inputs(
                conn, ids, known_at=known_at, tenant_id=tenant_id, clock=clock
            )
            for sid in ids:
                out[sid] = inputs.resolve(sid, out[sid], asof=asof, lower=valid_from_lower)
        return out
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in partition)
    knowability = sql.SQL(knowability_expr(table))  # noqa: S608 — hardcoded map, never caller input
    params = {
        "tenant_id": tenant_id,
        "ids": ids,
        "asof": asof,
        "known_at": known_at,
        "lower": valid_from_lower,
    }
    if has_first_knowability(table):
        query = sql.SQL(
            "SELECT * FROM {table} "
            "WHERE tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) "
            "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s{set_aside} "
            "ORDER BY {ident}, {version_order}"
        ).format(
            ident=ident,
            table=sql.Identifier(table),
            knowability=knowability,
            set_aside=exclusion_sql(table),  # migration 0062 — skipped before the pick
            version_order=sql.SQL(first_knowable_order(table)),  # noqa: S608 — trusted literal
        )
        for row in _first_knowable_read(conn, table, query, params, partition):
            if valid_from_lower is None or row["valid_from"] >= valid_from_lower:
                out[row["security_id"]].append(row)
        return out
    lower = sql.SQL(" AND valid_from >= %(lower)s") if valid_from_lower is not None else sql.SQL("")
    query = sql.SQL(
        "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s{lower}{set_aside} "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    ).format(
        ident=ident,
        table=sql.Identifier(table),
        knowability=knowability,
        lower=lower,
        set_aside=exclusion_sql(table),
    )
    with conn.cursor() as cur:
        cur.execute(query, params)
        for row in cur.fetchall():
            out[row["security_id"]].append(row)
    if table == insider_resolution.RESOLVED_TABLE:
        # R8-A — the SAME resolution the scoped ``as_of`` applies, over the whole basket in one load; the
        # floor rides along so an amended row dated before it stays out (row-for-row the scoped read
        # trimmed in Python — the PIT fallback's rule)
        inputs = insider_resolution.load_inputs(conn, ids, known_at=known_at, tenant_id=tenant_id)
        for sid in ids:
            out[sid] = inputs.resolve(sid, out[sid], asof=asof, lower=valid_from_lower)
    return out


def as_of_thesis(
    conn: psycopg.Connection,
    table: str,
    *,
    thesis_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
    clock: Clock = "record",
) -> list[dict[str, Any]]:
    """Bitemporal as-of read for a THESIS-scoped fact table (e.g. ``fact_theme_conviction``) — the same
    honesty as ``as_of`` but keyed by thesis, since a theme conviction is basket-level, not co-located
    on a security."""
    return _as_of(
        conn,
        table,
        scope_col="thesis_id",
        scope_id=thesis_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
        clock=clock,
    )


def _fact_exists(
    conn: psycopg.Connection,
    table: str,
    *,
    scope_col: str,
    scope_id: UUID,
    tenant_id: UUID,
    key: dict[str, Any],
) -> bool:
    """Shared "is this natural key stored AT ALL?" probe — see ``fact_exists`` for the contract.

    ``scope_col`` is a TRUSTED literal from the wrappers below, never caller input. Every ``key`` column
    must belong to the table's ``_FACT_IDENTITY`` — the SAME whitelist ``_as_of`` renders from, so the
    dynamic SQL stays injection-safe.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    if not key:
        raise ValueError(f"fact_exists on {table!r} needs at least one natural-key column")
    allowed = set(_FACT_IDENTITY[table])
    unknown = sorted(set(key) - allowed)
    if unknown:
        raise ValueError(
            f"{unknown} is not part of {table!r}'s natural key ({sorted(allowed)}) — "
            "an existence probe must key on the identity, nothing else"
        )
    where = sql.SQL(" AND ").join(
        sql.SQL("{col} = {val}").format(col=sql.Identifier(c), val=sql.Placeholder(c)) for c in key
    )
    query = sql.SQL(
        "SELECT 1 FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s AND {where} LIMIT 1"
    ).format(table=sql.Identifier(table), scope=sql.Identifier(scope_col), where=where)
    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id, "scope_id": scope_id, **key})
        return cur.fetchone() is not None


def fact_exists(
    conn: psycopg.Connection,
    table: str,
    *,
    security_id: UUID,
    tenant_id: UUID,
    **key: Any,
) -> bool:
    """Is ANY version of this natural key already stored for (tenant, security)?

    **This is NOT an as-of read** and must never stand in for one. It is deliberately version- AND
    time-agnostic: no ``valid_from <= asof`` gate, no ``recorded_at <= known_at`` gate, no ``DISTINCT ON``
    version pick. It answers exactly one question — "have we stored this fact at all?" — which is the right
    question for a caller replaying a STATIC fixture that never restates a value (``pipeline.seed``, whose
    demo fixtures re-run at every container boot). Anything that needs to know what a fact SAYS at a point
    in time must call ``as_of`` / ``as_of_thesis``.

    It is a guard for the CALLER, never for the writers: the ``ingest_*`` functions stay unguarded, so a
    genuine restatement through the normal ingest paths still appends a new version (later ``recorded_at``,
    latest-version-wins on the as-of read) — the bitemporal contract is untouched.

    ``key`` names the table's ``_FACT_IDENTITY`` columns (e.g. ``source_ref=…``, ``accession=…``); a column
    outside that identity raises.
    """
    return _fact_exists(
        conn, table, scope_col="security_id", scope_id=security_id, tenant_id=tenant_id, key=key
    )


def fact_exists_thesis(
    conn: psycopg.Connection,
    table: str,
    *,
    thesis_id: UUID,
    tenant_id: UUID,
    **key: Any,
) -> bool:
    """The THESIS-scoped form of ``fact_exists`` (e.g. ``fact_theme_conviction``, which is basket-level and
    so is not co-located on a security). Same contract, same caveat: NOT an as-of read."""
    return _fact_exists(
        conn, table, scope_col="thesis_id", scope_id=thesis_id, tenant_id=tenant_id, key=key
    )


def append_fact(conn: psycopg.Connection, table: str, values: dict[str, Any]) -> UUID:
    """Append a fact row. Corrections are new rows with a later ``recorded_at`` — never UPDATEs."""
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    cols = list(values.keys())
    query = sql.SQL("INSERT INTO {table} ({cols}) VALUES ({vals}) RETURNING id").format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in cols),
    )
    with conn.cursor() as cur:
        cur.execute(query, list(values.values()))
        return cur.fetchone()["id"]

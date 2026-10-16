from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from decimal import Decimal
from operator import itemgetter
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import tuple_row

from domain.market_time import market_tz

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
}

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
# A version's own knowable date is ``max(valid_from, market date of recorded_at)``: never before the filing,
# and never before THIS system held the version. MEASURED on prod 2026-10-16, every one of the 120,810
# ``fact_fundamentals`` rows has ``recorded_at = valid_from 00:00 UTC`` (initial-backfill and later nightly
# insert transactions alike), so the second term is inert today; it exists so a future honest live stamp
# (``recorded_at = now()``, a late ingest after an outage) can never date a fact before it was held.
#
# Equality is EXACT on the stored number (compared as float on both engines, which is lossless for these
# integer XBRL values and keeps the Postgres numeric and the mirror's float64 in lockstep). Any nonzero
# difference counts as new knowledge at its own date — the only choice under which an event is never dated
# before the exact number it rests on was public. MEASURED: 0 consecutive version pairs differ by <= 1e-9
# relative (there is no float jitter to absorb), while 7 revenue pairs differ by <= 1e-6 (a precision change:
# 84,182,954 re-reported as 84,183,000) — those stay new knowledge, a named residual.
#
# The read stays the latest-version pick (``recorded_at DESC, id DESC``, byte-for-byte the ``DISTINCT ON``
# pick); the row just carries two more keys. Detectors date, cite and age a fact by these, never by the
# picked version's ``valid_from``.
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


# The tables whose as-of read carries the first-knowability annotation — a trusted whitelist like
# ``_FACT_IDENTITY``. Both engines (Postgres here, DuckDB ``replay/pit.py``) branch on it and post-process
# through the SAME pure ``first_knowable_versions`` below, so replay parity holds by construction.
_FIRST_KNOWABILITY: dict[str, _FirstKnowability] = {
    "fact_fundamentals": _FirstKnowability(value_cols=("value",), source_col="accession"),
}


def has_first_knowability(table: str) -> bool:
    """Does the as-of read of ``table`` carry the D3a first-knowability annotation?"""
    return table in _FIRST_KNOWABILITY


def _same_number(v: Any) -> Any:
    """The equality key for one value column: numerics as float (Postgres numeric -> Decimal, the mirror ->
    float64 — one representation on both engines), anything else as-is."""
    return float(v) if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool) else v


def _market_day(recorded_at: Any, tz: tzinfo) -> date:
    """The market-time date of a transaction instant. A naive ``recorded_at`` is refused — a transaction time
    with no zone cannot be placed on a day, and guessing one is how a date slides."""
    if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
        raise ValueError(f"recorded_at must be a timezone-aware datetime, got {recorded_at!r}")
    return recorded_at.astimezone(tz).date()


def version_knowable_date(row: dict[str, Any], tz: tzinfo | None = None) -> date:
    """One version's own knowable date: ``max(valid_from, the MARKET date of recorded_at)``.

    Market date, not UTC: ``asof`` is a market-time trading day (``domain.market_time``), and a fact the
    22:30 ET pass recorded at 02:30 UTC was in hand for THAT day's call."""
    return max(row["valid_from"], _market_day(row["recorded_at"], tz or market_tz()))


def first_knowable_versions(
    table: str,
    columns: Sequence[str],
    versions: Iterable[Sequence[Any]],
    partition: Sequence[str],
) -> list[dict[str, Any]]:
    """Pure: every as-of-VISIBLE version of ``table`` (already gated on ``valid_from <= asof`` and the
    knowability column ``<= known_at``) -> the latest version per natural key, annotated (D3a).

    - **The pick** is the as-of read's own: the last version in ``recorded_at, id`` order — identical to the
      ``DISTINCT ON ... ORDER BY recorded_at DESC, id DESC`` row, all of its columns untouched.
    - **The run** is the trailing stretch of versions, ending at the pick, whose value columns equal the
      pick's. A different value anywhere before it ends the run (a restatement, or a restatement reverted —
      each is new knowledge at its own date).
    - **The annotation** is the run's earliest ``version_knowable_date`` (``KNOWABLE_FROM``) and that
      version's filing (``KNOWABLE_SOURCE``); a tie keeps the earliest-recorded version.

    ``versions`` are TUPLE rows in ``columns`` order — what both engines' cursors return natively — and only
    the picks become dicts (``{column: value}``, exactly the shape the ``dict_row`` read returned). Output
    order follows the first appearance of each natural key — the callers' SQL orders by the partition,
    exactly the ``DISTINCT ON`` output order. Inputs are not mutated.

    Written for the basket prefetch's volume (~38k versions for a 190-name thesis): tuple rows,
    ``itemgetter`` keys, one market-day conversion per distinct ``recorded_at``, a ``recorded_at``-only sort
    that falls back to the full ``(recorded_at, id)`` key only on a tie, and no walk for a single version.
    """
    spec = _FIRST_KNOWABILITY[table]
    tz = market_tz()
    columns = list(columns)
    at = {c: i for i, c in enumerate(columns)}
    rec_i, vf_i, id_i, src_i = at["recorded_at"], at["valid_from"], at["id"], at[spec.source_col]
    key_of = itemgetter(*(at[c] for c in partition))
    rec_of = itemgetter(rec_i)
    value_of = itemgetter(*(at[c] for c in spec.value_cols))  # a scalar for one column
    multi = len(spec.value_cols) > 1
    # recorded_at -> its market day (versions share filing-date instants)
    day_of: dict[Any, date] = {}

    def knowable(row: Sequence[Any]) -> date:
        rec = row[rec_i]
        day = day_of.get(rec)
        if day is None:
            day = day_of[rec] = _market_day(rec, tz)
        valid_from = row[vf_i]
        return valid_from if valid_from >= day else day

    def same_value(row: Sequence[Any]) -> Any:
        v = value_of(row)
        return tuple(_same_number(x) for x in v) if multi else _same_number(v)

    def version_order(row: Sequence[Any]) -> tuple[Any, str]:
        # ascending (recorded_at, id) — the reverse of the read's ``recorded_at DESC, id DESC``. ``str(id)``
        # because the mirror stores uuids as VARCHAR; canonical lowercase-hex text sorts exactly like
        # Postgres's byte-wise uuid order, so both engines break a tie identically.
        return (row[rec_i], str(row[id_i]))

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
            first_day = knowable(first)
        else:
            group.sort(key=rec_of)
            if any(rec_of(a) == rec_of(b) for a, b in zip(group, group[1:])):
                group.sort(key=version_order)  # a recorded_at tie: the id decides, as in the SQL
            pick = group[-1]
            target = same_value(pick)
            start = len(group) - 1
            while start > 0 and same_value(group[start - 1]) == target:
                start -= 1
            # ascending version order: a strictly-earlier day wins, so a tie keeps the earliest-recorded
            first, first_day = group[start], knowable(group[start])
            for candidate in group[start + 1 :]:
                day = knowable(candidate)
                if day < first_day:
                    first, first_day = candidate, day
        row = dict(zip(columns, pick))
        row[KNOWABLE_FROM] = first_day
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
) -> list[dict[str, Any]]:
    """Shared bitemporal point-in-time read, scoped by ``scope_col`` — no lookahead on either axis.

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
    and picks in Python (``first_knowable_versions``) — the same row, plus the D3a annotation.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in _FACT_IDENTITY[table])
    # the knowability gate — recorded_at for every table (the strict "what we held" no-lookahead axis;
    # accepted is a display/metrics column, never a gate); the version-pick ORDER BY is unchanged
    knowability = sql.SQL(knowability_expr(table))  # noqa: S608 — hardcoded map, never caller input
    first_knowable = has_first_knowability(table)
    template = (
        # every visible version (the run walk needs the history), ordered for the Python pick
        "SELECT * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s "
        "ORDER BY {ident}, recorded_at, id"
        if first_knowable
        else "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    )
    query = sql.SQL(template).format(
        ident=ident,
        table=sql.Identifier(table),
        scope=sql.Identifier(scope_col),
        knowability=knowability,
    )
    params = {"tenant_id": tenant_id, "scope_id": scope_id, "asof": asof, "known_at": known_at}
    if first_knowable:
        return _first_knowable_read(conn, table, query, params, _FACT_IDENTITY[table])
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


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


def as_of(
    conn: psycopg.Connection,
    table: str,
    *,
    security_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Bitemporal as-of read for a SECURITY-scoped fact table. **Behavior-identical** to the original
    single-function ``as_of`` (same inputs, same rows); it delegates to the shared ``_as_of``. The only
    SQL difference versus that original is cosmetic: the scope column is rendered quoted via
    ``sql.Identifier`` (``"security_id"``), semantically identical for the lowercase fact-table columns.
    """
    return _as_of(
        conn,
        table,
        scope_col="security_id",
        scope_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
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
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    ids = list(dict.fromkeys(security_ids))  # de-duplicated, order-preserving
    out: dict[UUID, list[dict[str, Any]]] = {sid: [] for sid in ids}
    if not ids:
        return out
    partition = ["security_id"] + [c for c in _FACT_IDENTITY[table] if c != "security_id"]
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
            "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s "
            "ORDER BY {ident}, recorded_at, id"
        ).format(ident=ident, table=sql.Identifier(table), knowability=knowability)
        for row in _first_knowable_read(conn, table, query, params, partition):
            if valid_from_lower is None or row["valid_from"] >= valid_from_lower:
                out[row["security_id"]].append(row)
        return out
    lower = sql.SQL(" AND valid_from >= %(lower)s") if valid_from_lower is not None else sql.SQL("")
    query = sql.SQL(
        "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s{lower} "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    ).format(ident=ident, table=sql.Identifier(table), knowability=knowability, lower=lower)
    with conn.cursor() as cur:
        cur.execute(query, params)
        for row in cur.fetchall():
            out[row["security_id"]].append(row)
    return out


def as_of_thesis(
    conn: psycopg.Connection,
    table: str,
    *,
    thesis_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
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

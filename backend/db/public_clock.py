"""THE PUBLIC CLOCK — "when could ANYONE have known this?" — defined ONCE, per table.

**The rule (operator decision 2026-10-21; CLAUDE.md invariant #1, ``docs/INVARIANTS.md`` §4).** History is
judged on the PUBLIC clock: a fact version counts only from when it became public — its filing / acceptance
time, its settled session; a correction from its OWN publication. ``recorded_at`` (when THIS system ingested a
row) stays honest bookkeeping — stamped now, never backdated — but it is not the history gate: gating a past
view on it only replays our own outages and the order companies were onboarded (MEASURED by the backtest
builder: 61 nights 2026-07-13..09-11 read differently on the record clock than on a filing-date view).

**Who reads this module.** Every history view and the backtest's public mirror, so the two can never drift:

- the SERVE path for a PAST asof (``/theses/{id}/call``, ``/display-signals``, ``/workbench/.../scored``), the
  Scoreboard drawer's dated overlays, ``pipeline.backfill`` (a missed night) and ``pipeline.run`` for a past
  asof — through ``db.bitemporal`` (``clock="public"``) and ``db.insider_resolution``;
- ``replay.export.export_snapshot(clock="public")`` — the SAME SQL, materialized once into the Parquet mirror.

The LIVE read (``asof`` = today: the nightly call-of-record, the live Board) stays on the record clock with
``known_at = now``: there the two clocks coincide (everything public by now and held by now is the same set),
and the live path is byte-identical to what it was.

**The table** (``PUBLIC_TIME`` below). Each version of a fact gets a PUBLIC INSTANT:

============================  =========  ==================================================================
table                          kind       a version's public instant
============================  =========  ==================================================================
fact_price_eod                 settle     the FIRST version: the bar's session settle (``d`` at
                                          ``Settings.price_settle_time``, market tz — when a close is final).
                                          A RE-version (a vendor correction, a refetch): its own
                                          ``recorded_at`` (never before the settle) — it carries no
                                          publication time of its own, so we cannot know it earlier.
                                          EXCEPT a bar a LISTED SPLIT re-scales (split plan S2, below):
                                          every version at the session settle, the newest read.
fact_insider_txn               instant    SEC ACCEPTANCE (``accepted``) — the FACT's, on every version: the
                                          newest version that carries one (all versions are one filing, so
                                          one publication: a re-parse shares it, and a version re-dating a
                                          mis-captured time moves the older ones with it — the
                                          ``backfill_accepted --drift`` repair, 2026-10-24). A fact with no
                                          acceptance on ANY version: the serve
                                          path counts it from the FIRST time we held it (``min(recorded_at)``
                                          — an upper bound: a filing we held was public by then, so it is
                                          never early, and never dropped — #9); the backtest export keeps its
                                          rule of excluding + COUNTING it.
fact_corporate_event           date       ``filed`` (the 8-K's filing date, start of that market day)
fact_activist_stake            date       ``filed`` (the 13D/G's filing date)
fact_filing_index              date       ``filed`` (the EDGAR index row's filing date; display only, 0070)
fact_fundamentals              date       ``valid_from`` (= the 10-Q/10-K ``filed`` date; a restatement is a
                                          new version with its own ``filed``, so it counts from its OWN
                                          publication)
fact_catalyst / fact_dilution  date       ``valid_from`` — the announcement date by construction (the
/ fact_theme_conviction                   backtest's existing operator decision; the ratification itself is
                                          later — the named hindsight caveat on the decision axis)
fact_revenue_mix /             recorded   ``recorded_at`` — operator-RATIFIED Workbench facts whose
fact_shares_outstanding /                 ``valid_from`` is the filing PERIOD / cover date (``event_date``),
fact_cash_burn                            earlier than the filing was public, and whose row carries no
                                          filing date: the ratification instant is the only never-early bound
                                          (an operator decision counts from when the operator made it)
fact_fund_shares               recorded   ``recorded_at`` — a scraped issuer page states an as-of ``d`` but no
                                          publication time; we cannot know it earlier than we read it
fact_term_mentions             date       ``valid_from`` (= the counted month's LAST day; THESIS-scoped,
                                          display only, 0071)
============================  =========  ==================================================================

THE SPLIT LIST (``security_split``, split plan S2 — not a fact table; ``split_versions_sql``): every version
of one logical split counts from the split's OWN publication — the start of the market day of the earliest
``valid_from`` (its first SEC filing, else its first session: S1 dates it so) or ``effective_date`` any of its
versions carries — never from when we added the row. A view reads the NEWEST version (today's reading: the
backfill's and the operator's confirm / revoke are readings of one public corporate act, so they count from
it). This deliberately differs from the 4/A rule below (an operator decision from when it was made): a split
gated on the day the operator confirmed it would leave every view between the split and the confirm reading
the repaired bars on one scale and the list on another — the mixed-scale history S2 exists to remove.

THE PRICE TAPE'S SCALE (S2). A vendor re-version of a bar BEFORE a listed split is overwhelmingly the split
itself, not market information (MEASURED on the dev copy of prod 2026-10-22: of 29,057 bars before an in-force
split, 22,654 were first stored AFTER it — an onboarding pull, already split-adjusted — so their FIRST version,
public at the settle, carried a split that had not happened yet, and a view before the split read it). So for
a bar ``d`` before a live listed split (``live_splits_sql``: confirmed / announced, the newest version of each)
whose first session is on or before the security's TAPE EDGE (its latest stored bar), every version is public
at the session settle and the newest is read. Every price row carries ``split_store_through`` = that edge —
the scale the stored values are on: the store keeps the whole tape on the list's scale as of its edge (the S2
price leg rescales the history in the same leg that stores a split's first post-split bar; the repair tool
restores it; the ``splits_applied`` audit names a split it does not hold — a per-security invariant the
system checks nightly, chosen over "a version carries the splits in force when it was written", which a
vendor that adjusts a night EARLY breaks invisibly: MEASURED, BTOC's 2024-10-15/16 bars re-based at 22:49 ET
the evening before its 2026-10-15 split). The VIEW then puts each bar on ITS scale
(``domain.splits.on_view_scale``, shared by both engines): x the splits in force by the view's day, / the
splits the store carries — so a view before a split reads the pre-split scale, a view after it one consistent
scale, and nothing a view reads carries a split that was not in force by its day. What this gives up, NAMED: a
non-split vendor correction to such a bar counts from the settle, not from its own publication (MEASURED: 34
version-to-version changes beyond 3% and 740 between 0.5% and 3% — most of them a vendor's own split-factor
rounding, SMX / ATRO — across those 29,057 bars).

The Form 4/A tables (``db.insider_resolution``): an amendment and the system's link count from the 4/A's SEC
acceptance (else its filing date); a SYSTEM decision about it (``basis`` ``system:*`` — the system's reading
of a public filing under today's rules) counts from that same publication; an OPERATOR decision counts from
when the operator made it (its ``recorded_at``).

**Version picking on the public clock.** The visible version at a past asof is the NEWEST version that was
public by then: ``ORDER BY public DESC, recorded_at DESC, id DESC`` among versions with ``public <= known_at``
— so a pure re-parse of one filing (the same public instant) resolves to our best parse, and a correction
counts only from its own publication. The rows a public read returns carry ``recorded_at`` = the version's
PUBLIC instant (the axis the read was judged on — exactly what the backtest mirror stores), so a downstream
reader of ``recorded_at`` (D3a's knowable date) reads the public clock too.

**Monotone, so the live read is unchanged by construction.** Within one fact, a version's public instant never
decreases as ``recorded_at`` increases (a re-version's is ``max(settle, recorded_at)``; every other kind is
constant per fact — an insider fact's acceptance is its newest statement, on every version — or IS
``recorded_at``), so with every version visible the public pick IS the record pick.
A split re-scaled bar puts every version at one instant (the settle), so its pick is the newest by
``recorded_at`` — the record pick again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from psycopg import sql

from db.set_aside import exclusion_sql, has_set_aside
from domain.market_time import market_tz
from domain.settings import get_settings
from domain.splits import LIVE_STATUSES
from domain.splits import STORE_THROUGH_COL as _STORE_THROUGH

# Which axis a history read is judged on. ``record`` = the LIVE read (``recorded_at <= known_at``, today's
# behavior byte-for-byte); ``public`` = a history view (the public instant below ``<= known_at``).
Clock = Literal["record", "public"]

PublicKind = Literal["date", "instant", "settle", "recorded"]


@dataclass(frozen=True)
class PublicTime:
    """How one table's versions get their public instant (see the module table)."""

    kind: PublicKind
    # the source column: a DATE (date / settle kinds), a timestamptz (instant), ``recorded_at`` (recorded)
    column: str


PUBLIC_TIME: dict[str, PublicTime] = {
    "fact_price_eod": PublicTime("settle", "d"),
    "fact_insider_txn": PublicTime("instant", "accepted"),
    "fact_corporate_event": PublicTime("date", "filed"),
    "fact_activist_stake": PublicTime("date", "filed"),
    "fact_filing_index": PublicTime("date", "filed"),
    "fact_fundamentals": PublicTime("date", "valid_from"),
    "fact_catalyst": PublicTime("date", "valid_from"),
    "fact_dilution": PublicTime("date", "valid_from"),
    "fact_theme_conviction": PublicTime("date", "valid_from"),
    "fact_revenue_mix": PublicTime("recorded", "recorded_at"),
    "fact_shares_outstanding": PublicTime("recorded", "recorded_at"),
    "fact_cash_burn": PublicTime("recorded", "recorded_at"),
    "fact_fund_shares": PublicTime("recorded", "recorded_at"),
    "fact_term_mentions": PublicTime("date", "valid_from"),
}

# The fact tables keyed by THESIS, not co-located on a security (the theme conviction is basket-level; the
# term-mentions count is a card's own term set, 0071). Every other fact table is security-scoped.
THESIS_SCOPED_FACT_TABLES: frozenset[str] = frozenset(
    {"fact_theme_conviction", "fact_term_mentions"}
)

# The SCOPE each fact is partitioned within — a table's identity is unique only WITHIN one scope
# (``fact_insider_txn``'s is shared across securities — the 0037 lesson). Keyed on the registry, which a test
# pins equal to ``db.bitemporal._FACT_IDENTITY`` (every fact table has a public clock).
SCOPE_COL: dict[str, str] = {
    t: "thesis_id" if t in THESIS_SCOPED_FACT_TABLES else "security_id" for t in PUBLIC_TIME
}

# The extra column every public read adds (the version's public instant) — stripped before a row is returned.
PUBLIC_COL = "_public"
# On a public-clock row ``recorded_at`` carries the PUBLIC instant; the honest ingest instant rides here.
INGESTED_COL = "ingested_at"


def partition_cols(table: str) -> list[str]:
    """``tenant_id, <scope>, <identity minus scope>`` — ONE logical fact, scoped, exactly as the as-of reads
    partition it."""
    from db.bitemporal import _FACT_IDENTITY  # local: db.bitemporal imports this module

    ident = _FACT_IDENTITY[table]
    scope = SCOPE_COL[table]
    return ["tenant_id", scope] + [c for c in ident if c != scope]


def public_params(known_by: datetime | None = None) -> dict[str, Any]:
    """The bound parameters every public-clock query needs: the market zone a DATE is anchored in (a trading
    day is a domain fact — ``domain.market_time``) and the price settle time — plus the FREEZE CEILING when a
    caller freezes the store at a cut (``known_by``; see ``CEILING_PARAM``)."""
    params: dict[str, Any] = {
        "pc_tz": market_tz().key,
        "pc_settle": get_settings().price_settle_time,
    }
    if known_by is not None:
        params[CEILING_PARAM] = known_by
    return params


# --- the FREEZE CEILING (the Scoreboard editions' cut, ``editions/``) -------------------------------------
#
# NOT a history gate, and no history view passes it: on the public clock no version is hidden for having been
# ingested late. The ceiling answers a different question — "the store AS IT STOOD at a cut" — so an edition
# computed from a mirror cut at ``known_by`` can be recomputed later on exactly the rows it saw (the store is
# append-only): every row recorded AFTER the cut is absent, and everything the public instant reads ACROSS
# versions (a Form 4's NULL-filled acceptance, a bar's first version, the price tape's edge and the split list
# that decides a bar's scale, a 4/A's acceptance) is read from the rows held at the cut — exactly what this
# SQL would have computed had it run then. Every builder takes ``ceiling=False`` by default and is then
# byte-identical to the SQL it was before the ceiling existed; ``ceiling=True`` binds ``%(pc_known_by)s``.
CEILING_PARAM = "pc_known_by"


def _ceil(col: sql.Composable, ceiling: bool) -> sql.Composable:
    """`` AND {col} <= %(pc_known_by)s`` when ``ceiling``, else nothing."""
    if not ceiling:
        return sql.SQL("")
    return sql.SQL(" AND {} <= {}").format(col, sql.Placeholder(CEILING_PARAM))


def _start_of_day(col: sql.Composable) -> sql.Composable:
    """A DATE column as the START of that market day (an aware instant) — the backtest's established anchor:
    inert on visibility, because a date-clocked fact's ``valid_from`` already gates the day."""
    return sql.SQL("({}::timestamp AT TIME ZONE %(pc_tz)s)").format(col)


def public_time_sql(table: str, *, alias: str = "t", serve: bool = True) -> sql.Composable:
    """The SQL expression for one version's PUBLIC instant over ``{table} AS {alias}``, inside a query that
    defines the window ``pc`` (``PARTITION BY partition_cols(table)`` — ``public_versions_sql`` does).

    ``serve=True`` is the history-view definition (an ``instant`` fact with no acceptance on any version counts
    from the first time we held it — never early, never dropped); ``serve=False`` is the backtest export's,
    which leaves that instant NULL so the export can exclude + count the row (its existing contract).
    """
    spec = PUBLIC_TIME[table]
    col = sql.Identifier(alias, spec.column)
    rec = sql.Identifier(alias, "recorded_at")
    if spec.kind == "date":
        return _start_of_day(col)
    if spec.kind == "recorded":
        return rec
    if spec.kind == "settle":
        settle = sql.SQL("(({} + %(pc_settle)s::time) AT TIME ZONE %(pc_tz)s)").format(col)
        return sql.SQL(
            "CASE WHEN row_number() OVER (pc ORDER BY {rec}, {id}) = 1 THEN {settle} "
            "ELSE GREATEST({settle}, {rec}) END"
        ).format(rec=rec, id=sql.Identifier(alias, "id"), settle=settle)
    # instant: the FACT's acceptance, on every version — our NEWEST stored statement of it. Every version of one
    # fact is one filing, so one publication: a re-parse shares it, and a re-dated version (a mis-captured time
    # corrected, ``pipeline.backfill_accepted --drift``) moves the older versions with it — so an early capture
    # stops being visible early, and with every version visible the public pick is still the record pick.
    newest = sql.SQL(
        "first_value({col}) OVER (pc ORDER BY ({col} IS NULL), {rec} DESC, {id} DESC)"
    ).format(col=col, rec=rec, id=sql.Identifier(alias, "id"))
    if not serve:
        return newest
    return sql.SQL("COALESCE({newest}, min({rec}) OVER pc)").format(newest=newest, rec=rec)


def needs_window(table: str) -> bool:
    """Does this table's public instant read across the fact's versions (the ``pc`` window)?"""
    return PUBLIC_TIME[table].kind in ("settle", "instant")


# --- the split list (split plan S2) ------------------------------------------------------------------------

SPLIT_TABLE = "security_split"
# the statuses a reader applies (``domain.splits.is_applicable``): a split the operator has not settled
# (needs_review) or has set aside (rejected / revoked) re-scales nothing
LIVE_SPLIT_STATUSES = LIVE_STATUSES
# the column every public-clock price row carries: the market date of the bar's newest stored version — the
# scale its stored value is on (the module note); the view converts from it (``domain.splits.on_view_scale``)
STORE_THROUGH_COL = _STORE_THROUGH


def split_public_sql(alias: str = "s") -> sql.Composable:
    """The PUBLIC instant of a ``security_split`` version over ``security_split AS {alias}`` inside a window
    ``ps`` partitioned by the logical split (``split_versions_sql`` defines it): the start of the market day of
    the earliest ``valid_from`` / ``effective_date`` any version of the split carries (the module note).
    """
    return _start_of_day(
        sql.SQL("min(LEAST({vf}, {eff})) OVER ps").format(
            vf=sql.Identifier(alias, "valid_from"), eff=sql.Identifier(alias, "effective_date")
        )
    )


def split_versions_sql(*, where: sql.Composable, ceiling: bool = False) -> sql.Composable:
    """``SELECT s.*, <public instant> AS _public FROM security_split s WHERE {where}`` — the NEWEST version of
    every logical split ``where`` admits, with the split's public instant. ``where`` must admit a split's
    versions together (tenant + security do). The ONE builder the history read (``split_repo``) and the
    backtest's public mirror (``replay.export``) both run; neither gates on ``recorded_at``. ``ceiling`` =
    the list as it stood at a cut (the freeze ceiling above — an edition mirror's, never a history view's).
    """
    return sql.SQL(
        "SELECT DISTINCT ON (s.tenant_id, s.security_id, s.key_date) s.*, {pub} AS {col} "
        "FROM security_split s WHERE {where}{ceil} "
        "WINDOW ps AS (PARTITION BY s.tenant_id, s.security_id, s.key_date) "
        "ORDER BY s.tenant_id, s.security_id, s.key_date, s.recorded_at DESC, s.id DESC"
    ).format(
        pub=split_public_sql("s"),
        col=sql.Identifier(PUBLIC_COL),
        where=where,
        ceil=_ceil(sql.Identifier("s", "recorded_at"), ceiling),
    )


def live_splits_sql(*, ceiling: bool = False) -> sql.Composable:
    """The split list's CURRENT reading: the newest version of each logical split, live statuses only —
    ``(tenant_id, security_id, effective_date)``. What decides which price bars a listed split re-scales.
    ``ceiling`` = the reading at a cut (the freeze ceiling above).
    """
    return sql.SQL(
        "SELECT tenant_id, security_id, effective_date FROM ("
        "SELECT DISTINCT ON (tenant_id, security_id, key_date) tenant_id, security_id, effective_date, "
        "status FROM security_split{ceil} "
        "ORDER BY tenant_id, security_id, key_date, recorded_at DESC, id DESC"
        ") z WHERE status IN ({live})"
    ).format(
        live=sql.SQL(", ").join(sql.Literal(s) for s in LIVE_SPLIT_STATUSES),
        ceil=(
            sql.SQL(" WHERE recorded_at <= {}").format(sql.Placeholder(CEILING_PARAM))
            if ceiling
            else sql.SQL("")
        ),
    )


def _price_versions_sql(
    *, where: sql.Composable, extra: sql.Composable | None, ceiling: bool = False
) -> sql.Composable:
    """``public_versions_sql`` for ``fact_price_eod``: the table's rule, except a bar a live listed split
    re-scales — one dated before a split whose first session is on or before the security's TAPE EDGE (its
    latest stored bar) — has every version public at the session settle; every row carries
    ``split_store_through`` = that edge (the module note). The edge is the store's, read whole (never bounded
    by ``where``'s as-of): the scale the stored values are on now. ``where`` bounds the bar scan only, and
    admits a bar's versions together. ``ceiling`` (the freeze ceiling) reads the scan, the edge and the split
    list as they stood at the cut — the scale the stored values were on THEN."""
    settle = sql.SQL("((t.d + %(pc_settle)s::time) AT TIME ZONE %(pc_tz)s)")
    window = sql.SQL(", ").join(sql.Identifier("t", c) for c in partition_cols("fact_price_eod"))
    return sql.SQL(
        "WITH _ps AS ("
        " SELECT DISTINCT tenant_id AS ps_tenant, security_id AS ps_security FROM fact_price_eod"
        " WHERE {where}{ceil_ps}"
        "), _pe AS ("
        " SELECT ps_tenant, ps_security, (SELECT max(e.d) FROM fact_price_eod e"
        " WHERE e.tenant_id = ps_tenant AND e.security_id = ps_security{ceil_e}) AS ps_edge FROM _ps"
        "), _pl AS ("
        " SELECT pe.ps_tenant AS pl_tenant, pe.ps_security AS pl_security, pe.ps_edge AS pl_edge,"
        " max(ls.effective_date) AS pl_last"
        " FROM _pe pe LEFT JOIN ({live}) ls ON ls.tenant_id = pe.ps_tenant"
        " AND ls.security_id = pe.ps_security AND ls.effective_date <= pe.ps_edge"
        " GROUP BY pe.ps_tenant, pe.ps_security, pe.ps_edge"
        ") "
        "SELECT t.*, CASE WHEN t.d < pl.pl_last THEN {settle} ELSE {rule} END AS {pub}, "
        "pl.pl_edge AS {through}{extra} "
        "FROM fact_price_eod t "
        "JOIN _pl pl ON pl.pl_tenant = t.tenant_id AND pl.pl_security = t.security_id "
        "WHERE {where}{ceil_t} WINDOW pc AS (PARTITION BY {window})"
    ).format(
        where=where,
        ceil_ps=_ceil(sql.Identifier("recorded_at"), ceiling),
        ceil_e=_ceil(sql.Identifier("e", "recorded_at"), ceiling),
        ceil_t=_ceil(sql.Identifier("t", "recorded_at"), ceiling),
        live=live_splits_sql(ceiling=ceiling),
        settle=settle,
        rule=public_time_sql("fact_price_eod"),
        pub=sql.Identifier(PUBLIC_COL),
        through=sql.Identifier(STORE_THROUGH_COL),
        extra=sql.SQL(", ") + extra if extra is not None else sql.SQL(""),
        window=window,
    )


def public_versions_sql(
    table: str,
    *,
    where: sql.Composable,
    serve: bool = True,
    extra: sql.Composable | None = None,
    ceiling: bool = False,
) -> sql.Composable:
    """``SELECT t.*, <public instant> AS _public [, extra] FROM {table} t WHERE {where}`` — every version of
    every fact ``where`` admits, each with its public instant. The ONE builder the history read
    (``db.bitemporal``) and the backtest export (``replay.export``) both run.

    ``where`` must admit or drop a fact's versions TOGETHER (scope, tenant, and ``valid_from`` bounds do — on
    the two tables whose instant reads across versions, price and insider, ``valid_from`` is part of the
    identity), so the cross-version window sees each fact whole. Never a ``recorded_at`` filter: on the public
    clock no version is hidden for having been ingested late. (``ceiling`` is not one either: it is the
    freeze ceiling above — the store as it stood at a cut, for an edition mirror; no history view passes it.)

    ``fact_price_eod`` is built by ``_price_versions_sql`` (a bar a listed split re-scales, S2).

    A SET-ASIDE version (migration 0062, ``db.set_aside`` — a stored fundamentals row the data-quality screen
    would have refused) is left out HERE, in the one builder, so the history read and the public mirror skip it
    alike; the version it superseded is then the one each (identity, public instant) keeps. Every other table's
    SQL is byte-for-byte unchanged. Under the freeze ceiling the set-asides are judged AS THEY STOOD AT THE
    CUT (``db.set_aside.active_ids``): a set-aside or a revoke recorded after the cut changes nothing in that
    edition's mirror. With no ceiling the exclusion is exactly 0062's."""
    if table == "fact_price_eod":
        return _price_versions_sql(where=where, extra=extra, ceiling=ceiling)
    if has_set_aside(table):
        at = sql.Placeholder(CEILING_PARAM) if ceiling else None
        where = sql.SQL("({}){}").format(where, exclusion_sql(table, alias="t", at=at))
    expr = public_time_sql(table, serve=serve)
    window = (
        sql.SQL(" WINDOW pc AS (PARTITION BY {})").format(
            sql.SQL(", ").join(sql.Identifier("t", c) for c in partition_cols(table))
        )
        if needs_window(table) or extra is not None
        else sql.SQL("")
    )
    return sql.SQL(
        "SELECT t.*, {expr} AS {pub}{extra} FROM {table} t WHERE {where}{ceil}{window}"
    ).format(
        expr=expr,
        pub=sql.Identifier(PUBLIC_COL),
        extra=sql.SQL(", ") + extra if extra is not None else sql.SQL(""),
        table=sql.Identifier(table),
        where=where,
        ceil=_ceil(sql.Identifier("t", "recorded_at"), ceiling),
        window=window,
    )


def to_public_row(row: dict[str, Any]) -> dict[str, Any]:
    """A fetched public-read row -> the returned shape: ``recorded_at`` := its public instant (the axis the
    read was judged on — the backtest mirror's rewrite), the honest ingest instant kept beside it as
    ``ingested_at`` (bookkeeping: "were we late?" — the drawer's "ingested" line), the helper column dropped.
    Mutates + returns."""
    row[INGESTED_COL] = row["recorded_at"]
    row["recorded_at"] = row.pop(PUBLIC_COL)
    return row


# --- the Form 4/A tables (db.insider_resolution; replay.export's public aux) ----------------------------

SYSTEM_BASIS_PREFIX = "system:"  # ``domain.insider_amendments.is_system_basis``'s prefix


def _amendment_public(alias: str) -> sql.Composable:
    """The public instant of the 4/A record ``alias`` — its SEC acceptance, else the start of its EDGAR
    filing day, else (neither known) when we first held it."""
    return sql.SQL("COALESCE({acc}, {filed}, {rec})").format(
        acc=sql.Identifier(alias, "accepted"),
        filed=_start_of_day(sql.Identifier(alias, "filed")),
        rec=sql.Identifier(alias, "recorded_at"),
    )


def _amendment_public_of(alias: str, ceiling: bool = False) -> sql.Composable:
    """The public instant of the amendment a link / decision row ``alias`` names — the earliest across the
    stored versions of its 4/A record (they share the acceptance), NULL when no record is stored.
    ``ceiling`` = the versions stored by the cut (the freeze ceiling)."""
    return sql.SQL(
        "(SELECT min({pub}) FROM insider_amendment a "
        "WHERE a.tenant_id = {al}.tenant_id AND a.security_id = {al}.security_id "
        "AND a.accession = {al}.amendment_accession{ceil})"
    ).format(
        pub=_amendment_public("a"),
        al=sql.Identifier(alias),
        ceil=_ceil(sql.Identifier("a", "recorded_at"), ceiling),
    )


def aux_public_sql(table: str, alias: str = "x", *, ceiling: bool = False) -> sql.Composable:
    """The public instant of one row of a Form 4/A table (``insider_amendment`` / ``_link`` / ``_decision``)
    over ``{table} AS {alias}``. Never NULL: each falls back to the row's own ``recorded_at``. ``ceiling``
    reads the OTHER rows it consults (the 4/A record, the link) as stored by the cut (the freeze ceiling).
    """
    al = sql.Identifier(alias)
    rec = sql.Identifier(alias, "recorded_at")
    if table == "insider_amendment":
        return _amendment_public(alias)
    if table == "insider_amendment_link":
        # the link's ``accepted`` IS the amendment's acceptance (stored for display)
        return sql.SQL("COALESCE({acc}, {amend}, {rec})").format(
            acc=sql.Identifier(alias, "accepted"),
            amend=_amendment_public_of(alias, ceiling),
            rec=rec,
        )
    if table == "insider_amendment_decision":
        link_acc = sql.SQL(
            "(SELECT min(l.accepted) FROM insider_amendment_link l "
            "WHERE l.tenant_id = {al}.tenant_id AND l.security_id = {al}.security_id "
            "AND l.amendment_accession = {al}.amendment_accession{ceil})"
        ).format(al=al, ceil=_ceil(sql.Identifier("l", "recorded_at"), ceiling))
        return sql.SQL(
            "CASE WHEN starts_with({basis}, {system}) THEN COALESCE({amend}, {link_acc}, {rec}) "
            "ELSE {rec} END"
        ).format(
            basis=sql.Identifier(alias, "basis"),
            system=sql.Literal(SYSTEM_BASIS_PREFIX),
            amend=_amendment_public_of(alias, ceiling),
            link_acc=link_acc,
            rec=rec,
        )
    raise ValueError(f"no public clock for aux table {table!r}")

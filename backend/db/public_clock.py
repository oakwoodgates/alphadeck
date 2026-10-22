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
fact_insider_txn               instant    SEC ACCEPTANCE (``accepted``), filled from the fact's other
                                          versions when one lacks it (a re-parse of one filing shares its
                                          acceptance). A fact with no acceptance on ANY version: the serve
                                          path counts it from the FIRST time we held it (``min(recorded_at)``
                                          — an upper bound: a filing we held was public by then, so it is
                                          never early, and never dropped — #9); the backtest export keeps its
                                          rule of excluding + COUNTING it.
fact_corporate_event           date       ``filed`` (the 8-K's filing date, start of that market day)
fact_activist_stake            date       ``filed`` (the 13D/G's filing date)
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
============================  =========  ==================================================================

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
constant per fact or IS ``recorded_at``), so with every version visible the public pick IS the record pick.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from psycopg import sql

from domain.market_time import market_tz
from domain.settings import get_settings

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
    "fact_fundamentals": PublicTime("date", "valid_from"),
    "fact_catalyst": PublicTime("date", "valid_from"),
    "fact_dilution": PublicTime("date", "valid_from"),
    "fact_theme_conviction": PublicTime("date", "valid_from"),
    "fact_revenue_mix": PublicTime("recorded", "recorded_at"),
    "fact_shares_outstanding": PublicTime("recorded", "recorded_at"),
    "fact_cash_burn": PublicTime("recorded", "recorded_at"),
    "fact_fund_shares": PublicTime("recorded", "recorded_at"),
}

# The SCOPE each fact is partitioned within — a table's identity is unique only WITHIN one scope
# (``fact_insider_txn``'s is shared across securities — the 0037 lesson). Keyed on the registry, which a test
# pins equal to ``db.bitemporal._FACT_IDENTITY`` (every fact table has a public clock).
SCOPE_COL: dict[str, str] = {
    t: "thesis_id" if t == "fact_theme_conviction" else "security_id" for t in PUBLIC_TIME
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


def public_params() -> dict[str, Any]:
    """The bound parameters every public-clock query needs: the market zone a DATE is anchored in (a trading
    day is a domain fact — ``domain.market_time``) and the price settle time."""
    return {"pc_tz": market_tz().key, "pc_settle": get_settings().price_settle_time}


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
    # instant: the version's own, else the fact's (a re-parse of one filing shares its acceptance)
    filled = sql.SQL("COALESCE({col}, max({col}) OVER pc)").format(col=col)
    if not serve:
        return filled
    return sql.SQL("COALESCE({filled}, min({rec}) OVER pc)").format(filled=filled, rec=rec)


def needs_window(table: str) -> bool:
    """Does this table's public instant read across the fact's versions (the ``pc`` window)?"""
    return PUBLIC_TIME[table].kind in ("settle", "instant")


def public_versions_sql(
    table: str, *, where: sql.Composable, serve: bool = True, extra: sql.Composable | None = None
) -> sql.Composable:
    """``SELECT t.*, <public instant> AS _public [, extra] FROM {table} t WHERE {where}`` — every version of
    every fact ``where`` admits, each with its public instant. The ONE builder the history read
    (``db.bitemporal``) and the backtest export (``replay.export``) both run.

    ``where`` must admit or drop a fact's versions TOGETHER (scope, tenant, and ``valid_from`` bounds do — on
    the two tables whose instant reads across versions, price and insider, ``valid_from`` is part of the
    identity), so the cross-version window sees each fact whole. Never a ``recorded_at`` filter: on the public
    clock no version is hidden for having been ingested late."""
    expr = public_time_sql(table, serve=serve)
    window = (
        sql.SQL(" WINDOW pc AS (PARTITION BY {})").format(
            sql.SQL(", ").join(sql.Identifier("t", c) for c in partition_cols(table))
        )
        if needs_window(table) or extra is not None
        else sql.SQL("")
    )
    return sql.SQL(
        "SELECT t.*, {expr} AS {pub}{extra} FROM {table} t WHERE {where}{window}"
    ).format(
        expr=expr,
        pub=sql.Identifier(PUBLIC_COL),
        extra=sql.SQL(", ") + extra if extra is not None else sql.SQL(""),
        table=sql.Identifier(table),
        where=where,
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


def _amendment_public_of(alias: str) -> sql.Composable:
    """The public instant of the amendment a link / decision row ``alias`` names — the earliest across the
    stored versions of its 4/A record (they share the acceptance), NULL when no record is stored."""
    return sql.SQL(
        "(SELECT min({pub}) FROM insider_amendment a "
        "WHERE a.tenant_id = {al}.tenant_id AND a.security_id = {al}.security_id "
        "AND a.accession = {al}.amendment_accession)"
    ).format(pub=_amendment_public("a"), al=sql.Identifier(alias))


def aux_public_sql(table: str, alias: str = "x") -> sql.Composable:
    """The public instant of one row of a Form 4/A table (``insider_amendment`` / ``_link`` / ``_decision``)
    over ``{table} AS {alias}``. Never NULL: each falls back to the row's own ``recorded_at``."""
    al = sql.Identifier(alias)
    rec = sql.Identifier(alias, "recorded_at")
    if table == "insider_amendment":
        return _amendment_public(alias)
    if table == "insider_amendment_link":
        # the link's ``accepted`` IS the amendment's acceptance (stored for display)
        return sql.SQL("COALESCE({acc}, {amend}, {rec})").format(
            acc=sql.Identifier(alias, "accepted"), amend=_amendment_public_of(alias), rec=rec
        )
    if table == "insider_amendment_decision":
        link_acc = sql.SQL(
            "(SELECT min(l.accepted) FROM insider_amendment_link l "
            "WHERE l.tenant_id = {al}.tenant_id AND l.security_id = {al}.security_id "
            "AND l.amendment_accession = {al}.amendment_accession)"
        ).format(al=al)
        return sql.SQL(
            "CASE WHEN starts_with({basis}, {system}) THEN COALESCE({amend}, {link_acc}, {rec}) "
            "ELSE {rec} END"
        ).format(
            basis=sql.Identifier(alias, "basis"),
            system=sql.Literal(SYSTEM_BASIS_PREFIX),
            amend=_amendment_public_of(alias),
            link_acc=link_acc,
            rec=rec,
        )
    raise ValueError(f"no public clock for aux table {table!r}")

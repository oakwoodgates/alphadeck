"""SET-ASIDE VERSIONS (migrations 0062 and 0205) — the stored fact versions every read skips, defined ONCE.

TWO SOURCES feed the one registry below:

1. ``fundamentals_set_aside`` (0062) for ``fact_fundamentals`` — the data-quality screen's judgments (below).
2. ``platform_value_decision`` (0205, V2 W2a) for ``fact_shares_outstanding`` and ``fact_cash_burn`` — the fact
   version a steward approve or correct wrote, once a later ``undo`` names that decision. The read then falls
   back to the version the steward's replaced (the earlier version of the same filing, or the next pick across
   filings). A dismiss or an undo writes no fact: their NULL ``fact_id`` is filtered out, or ``NOT IN`` would blank
   every read.

Every reader (below) already branches on ``has_set_aside``, so registering a table here is the whole change.

A stored ``fact_fundamentals`` version the data-quality screen would have refused at ingest (#417's screen,
``ingest.fundamentals._label_series``), written by the OLD ingest before the screen existed, is named in the
append-only ``fundamentals_set_aside`` table (``pipeline.set_aside_fundamentals``). While that judgment is
ACTIVE (no revoke names it), the version is invisible to every read of the table:

- the Postgres as-of read on BOTH clocks (``db.bitemporal`` — the live record read, ``as_of_many``'s basket
  prefetch, and the public-clock history read through ``db.public_clock.public_versions_sql``);
- the replay mirror (``replay.export`` leaves the version out of the Parquet, on both clocks), so the DuckDB
  engine reads exactly what Postgres reads.

The version is skipped BEFORE the pick and before D3a's first-knowability walk, so a version it superseded — a
later filing's restatement of the same period, or a same-filing re-parse — falls back to the earlier one, as if
the bad row had never been stored. It applies at EVERY asof: the screen judges a row only against filings on or
before the row's own filing, so setting it aside is exactly what the ingest screen would have done the day the
row was filed — no lookahead, nothing decided by a later filing (INVARIANTS.md §4).

What it never does: delete or rewrite a fact (the table is append-only and keeps every row), or hide a version
from the WRITERS — the ingest's idempotency map (``ingest.fundamentals.held_versions``) still sees every stored
version, so a set-aside row is never re-appended and the 0060 chain stays whole.

Trusted literals only: the table names and the predicate below are module constants, never caller input.
"""

from __future__ import annotations

from psycopg import sql

SET_ASIDE_TABLE = "fundamentals_set_aside"

# THE ACTIVE PREDICATE over ``fundamentals_set_aside AS s``, stated once: a set_aside no revoke references. The
# one revoke per set_aside (0062's partial unique index) and the same-version / never-a-revoke trigger make this
# single NOT EXISTS exact. The repository and every read use it, so "active" cannot mean two things.
ACTIVE_SQL = (
    "s.action = 'set_aside' "
    "AND NOT EXISTS (SELECT 1 FROM fundamentals_set_aside r WHERE r.revokes = s.id)"
)

# The fact tables a set-aside can name -> the ids of their ACTIVE set-aside versions: fundamentals (0062), and the
# two platform value tables (0205, ``_undone_steward_writes``).
_SET_ASIDE_IDS: dict[str, str] = {
    "fact_fundamentals": f"SELECT s.fact_id FROM fundamentals_set_aside s WHERE {ACTIVE_SQL}",
}


def _undone_steward_writes(fact_type: str, *, at: bool = False) -> str:
    """THE SECOND SOURCE (V2 W2a, migration 0205): the fact versions written by a steward approve or correct that a
    later ``undo`` names. Only non-NULL ``fact_id`` s are selected: a dismiss or an undo wrote no fact (NULL), and a
    NULL inside ``NOT IN`` makes the test never true, which would blank EVERY read of the table. ``at`` = the cut
    form (``{at}``, the caller's bound placeholder): the decision AND its undo were both recorded by the cut.
    """
    cut_d = " AND d.recorded_at <= {at}" if at else ""
    cut_u = " AND u.recorded_at <= {at}" if at else ""
    return (
        "SELECT d.fact_id FROM platform_value_decision d "
        f"WHERE d.fact_type = '{fact_type}' AND d.action IN ('approve', 'correct') "
        f"AND d.fact_id IS NOT NULL{cut_d} "
        "AND EXISTS (SELECT 1 FROM platform_value_decision u "
        f"WHERE u.revokes = d.id AND u.action = 'undo'{cut_u})"
    )


# The two platform value tables (``domain.platform_values.TABLE_OF``): their set-asides are undone steward writes.
_PLATFORM_VALUE_TYPES: dict[str, str] = {
    "fact_shares_outstanding": "shares_outstanding",
    "fact_cash_burn": "cash_burn",
}
_SET_ASIDE_IDS.update({t: _undone_steward_writes(ft) for t, ft in _PLATFORM_VALUE_TYPES.items()})


def has_set_aside(table: str) -> bool:
    """Can a stored version of ``table`` be set aside (does its read carry the exclusion)?"""
    return table in _SET_ASIDE_IDS


def active_ids_sql(table: str) -> str:
    """``SELECT s.fact_id ...`` — the ids of ``table``'s ACTIVE set-aside versions (a trusted literal)."""
    return _SET_ASIDE_IDS[table]


# THE SAME JUDGMENT AS IT STOOD AT A CUT (the Scoreboard editions' freeze ceiling, ``db.public_clock``): a
# set-aside counts only if it was RECORDED by the cut, and a revoke undoes it only if the revoke was recorded by
# the cut too — so a set-aside, or a revoke, written after an edition was cut can never change that edition's
# mirror (the store is append-only, so the cut reproduces). ``{at}`` is the caller's bound placeholder.
_SET_ASIDE_IDS_AT: dict[str, str] = {
    "fact_fundamentals": (
        "SELECT s.fact_id FROM fundamentals_set_aside s WHERE s.action = 'set_aside' "
        "AND s.recorded_at <= {at} AND NOT EXISTS (SELECT 1 FROM fundamentals_set_aside r "
        "WHERE r.revokes = s.id AND r.recorded_at <= {at})"
    ),
}
_SET_ASIDE_IDS_AT.update(
    {t: _undone_steward_writes(ft, at=True) for t, ft in _PLATFORM_VALUE_TYPES.items()}
)


def active_ids(table: str, *, at: sql.Composable | None = None) -> sql.Composable:
    """The ids of ``table``'s ACTIVE set-aside versions as a composable: ``at`` = None is exactly
    ``active_ids_sql`` (active now); ``at`` = a bound placeholder for a cut = active AS OF that cut.
    """
    if at is None:
        return sql.SQL(_SET_ASIDE_IDS[table])  # noqa: S608 — a module constant, never caller input
    return sql.SQL(_SET_ASIDE_IDS_AT[table]).format(at=at)  # noqa: S608 — a module constant


def exclusion_sql(
    table: str, *, alias: str | None = None, at: sql.Composable | None = None
) -> sql.Composable:
    """`` AND <id> NOT IN (<active set-aside ids>)`` for ``table`` (``<id>`` qualified by ``alias`` when the read
    aliases the table), else the empty SQL — appended to a read's WHERE, so a table with no set-aside reads
    byte-for-byte as before. Every registered list selects only non-NULL ids (``fundamentals_set_aside.fact_id``
    is NOT NULL; the steward source filters ``fact_id IS NOT NULL``), so ``NOT IN`` is exact (no NULL trap). ``at`` (a bound
    placeholder, the freeze ceiling's) judges the set-asides as they stood at that cut (``active_ids``); with
    ``at`` = None the SQL is byte-for-byte what it was before the ceiling existed."""
    if table not in _SET_ASIDE_IDS:
        return sql.SQL("")
    col = sql.Identifier(alias, "id") if alias else sql.Identifier("id")
    return sql.SQL(" AND {col} NOT IN ({ids})").format(col=col, ids=active_ids(table, at=at))


__all__ = [
    "SET_ASIDE_TABLE",
    "ACTIVE_SQL",
    "has_set_aside",
    "active_ids_sql",
    "active_ids",
    "exclusion_sql",
]

"""SET-ASIDE VERSIONS (migration 0062) — the stored fact versions every read skips, defined ONCE.

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

# The fact tables a set-aside can name -> the ids of their ACTIVE set-aside versions. Only fundamentals today.
_SET_ASIDE_IDS: dict[str, str] = {
    "fact_fundamentals": f"SELECT s.fact_id FROM fundamentals_set_aside s WHERE {ACTIVE_SQL}",
}


def has_set_aside(table: str) -> bool:
    """Can a stored version of ``table`` be set aside (does its read carry the exclusion)?"""
    return table in _SET_ASIDE_IDS


def active_ids_sql(table: str) -> str:
    """``SELECT s.fact_id ...`` — the ids of ``table``'s ACTIVE set-aside versions (a trusted literal)."""
    return _SET_ASIDE_IDS[table]


def exclusion_sql(table: str, *, alias: str | None = None) -> sql.Composable:
    """`` AND <id> NOT IN (<active set-aside ids>)`` for ``table`` (``<id>`` qualified by ``alias`` when the read
    aliases the table), else the empty SQL — appended to a read's WHERE, so a table with no set-aside reads
    byte-for-byte as before. ``fact_id`` is NOT NULL, so ``NOT IN`` is exact (no NULL trap)."""
    if table not in _SET_ASIDE_IDS:
        return sql.SQL("")
    col = sql.Identifier(alias, "id") if alias else sql.Identifier("id")
    return sql.SQL(" AND {col} NOT IN ({ids})").format(
        col=col,
        ids=sql.SQL(_SET_ASIDE_IDS[table]),  # noqa: S608 — a module constant, never caller input
    )


__all__ = ["SET_ASIDE_TABLE", "ACTIVE_SQL", "has_set_aside", "active_ids_sql", "exclusion_sql"]

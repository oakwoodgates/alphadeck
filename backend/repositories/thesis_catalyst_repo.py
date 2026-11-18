"""V2 W6a: the counting catalyst entries of a card (``fact_thesis_catalyst``, migration 0212) — the SOLE writer.

An entry on a card's catalyst list is a calendar item (``catalyst``: label, kind, typed date) and nothing more,
unless the operator makes it count (the signed rule R2): a grade, a citation, optionally named members and a
"relevant until" date, and the switch "counts toward the call". Each change of that statement is one appended
VERSION of this append-only, thesis-scoped WORKSPACE table; nothing is ever updated in place.

**Nothing in the call path reads this module yet** (W6c does). Until then no call can differ by one byte
(``tests/pipeline/test_w6_card_identity.py``).

**When a version counts from (invariant 1, R5).** The PUBLIC instant of an operator version is the moment it was
SAVED, set by the database: ``recorded_at`` is the column default ``now()`` and ``entered_at`` is the SQL
``now()`` in the same statement (a CHECK pins them equal). No caller can supply a time: ``append_version`` takes
none, and the typed calendar date (``catalyst.when_date``) is never read here. ``valid_from`` (the night it counts
from) is ``entry_night`` of that instant, the existing ``domain.market_time.edgar_night`` (a save between 06:00
and 22:00 ET on a weekday is that day's night; one before 06:00, the weekday before; a weekend save, Friday's).

**The version rules (the W6 plan's Q2, operator decision N5 (a)).**
- Turning the switch on, or changing the grade or the members, re-dates the entry: it counts from that save's
  night.
- Changing only the label, the citation or the relevant-until date keeps the entry's night (the version carries
  the previous ``valid_from``).
- Turning the switch off appends a version with ``counts = false`` from that save's night, the other fields
  copied from the version it supersedes, so the form re-opens filled and earlier nights still read the counting
  version.
- A save that changes nothing appends nothing (store-on-diff); so does switching off an entry that does not
  count.

The checks every version passes: a known calendar row of this card (``entry_id`` = ``catalyst.id``), a grade of
``core`` / ``flip``, a non-empty citation and label, members that are on this card (any segment: a silent link is
accepted and has no vote, W6c), a whole-card entry (no members) graded flip only (N2), and a relevant-until date
after tonight's night when it is set or changed on a counting version. The database refuses the rest
(0212's CHECKs). A thesis-scoped workspace table changes no company's stored data, so no ``mark_changed``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import psycopg

from db.bitemporal import as_of_thesis
from db.public_clock import Clock
from domain.market_time import edgar_night
from domain.scopes import DATA_TENANT_ID, tenants_for

TABLE = "fact_thesis_catalyst"
GRADES: frozenset[str] = frozenset({"core", "flip"})
SOURCE = (
    "thesis_catalyst"  # an operator version; 'ratified' = the one converted theme row (W6c, 0213)
)


class CountingEntryError(ValueError):
    """A counting statement the store refuses (W6b maps it to 422)."""


CITATION_NEEDED = "Add a citation: a link (https://…) or an SEC accession number."


class UnknownEntry(LookupError):
    """No such card, or no such calendar row on it (W6b maps it to 404)."""


_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def is_citation(text: str) -> bool:
    """The citation's form (operator decision N9 (a)): an http(s) link or an SEC accession number
    (``0001193125-26-234847``). Recorded, never fetched or verified. Pure; the counting route checks it.
    """
    t = text.strip()
    if _ACCESSION.match(t):
        return True
    if not t or any(c.isspace() for c in t):
        return False
    parts = urlsplit(t)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def entry_night(at: datetime) -> date:
    """The night a version saved at ``at`` counts from: ``domain.market_time.edgar_night`` (no new rule)."""
    return edgar_night(at)


def _members(security_ids: Iterable[UUID]) -> list[UUID]:
    """Sorted and unique: the stored order is canonical, so a reorder is not a change and the mirror's JSON
    array is the same bytes for the same set."""
    return sorted({UUID(str(s)) for s in security_ids})


def members_of(value: Any) -> frozenset[UUID]:
    """The named members of a stored version, as a set of ``UUID``, on either engine: a Postgres ``uuid[]`` (a
    list of ``UUID``), the mirror's decoded JSON array (a list of ``str``) or its raw JSON string.
    """
    if value is None:
        return frozenset()
    if isinstance(value, str):
        value = json.loads(value)
    return frozenset(UUID(str(v)) for v in value)


def _thesis_tenant(cur: psycopg.Cursor, thesis_id: UUID) -> UUID:
    cur.execute("SELECT tenant_id FROM thesis WHERE id = %s", (thesis_id,))
    row = cur.fetchone()
    if row is None:
        raise UnknownEntry(f"thesis {thesis_id} not found")
    return row["tenant_id"]


def _newest(cur: psycopg.Cursor, tenant: UUID, thesis_id: UUID, entry_id: UUID) -> dict | None:
    cur.execute(
        "SELECT * FROM fact_thesis_catalyst WHERE tenant_id = %s AND thesis_id = %s AND entry_id = %s "
        "ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (tenant, thesis_id, entry_id),
    )
    return cur.fetchone()


def append_version(
    conn: psycopg.Connection,
    thesis_id: UUID,
    entry_id: UUID,
    *,
    counts: bool,
    grade: str,
    security_ids: Iterable[UUID],
    citation: str,
    relevant_until: date | None,
    label: str,
    user_id: UUID | None,
) -> UUID | None:
    """Append one version of entry ``entry_id``'s counting statement on card ``thesis_id`` when it changes;
    return the new row's id, or ``None`` when nothing changed (nothing written). The rules are the module
    docstring's. Runs in the caller's transaction (a savepoint when one is open), so the version's instant, its
    night and its row come from ONE database ``now()``. Raises ``UnknownEntry`` / ``CountingEntryError``.

    There is deliberately no time parameter: when a version counts from is the database's business (R5).
    """
    with conn.transaction(), conn.cursor() as cur:
        tenant = _thesis_tenant(cur, thesis_id)
        cur.execute(
            "SELECT 1 FROM catalyst WHERE id = %s AND thesis_id = %s AND tenant_id = %s",
            (entry_id, thesis_id, tenant),
        )
        if cur.fetchone() is None:
            raise UnknownEntry(f"catalyst entry {entry_id} is not on thesis {thesis_id}")
        cur.execute("SELECT now() AS t")
        night = entry_night(cur.fetchone()["t"])
        prev = _newest(cur, tenant, thesis_id, entry_id)

        if not counts:
            if prev is None or not prev["counts"]:
                return None  # nothing counts, so there is nothing to switch off
            state = {
                "counts": False,
                "security_ids": _members(prev["security_ids"]),
                "grade": prev["grade"],
                "label": prev["label"],
                "citation": prev["citation"],
                "relevant_until": prev["relevant_until"],
                "valid_from": night,
            }
        else:
            members = _members(security_ids)
            citation, label = citation.strip(), label.strip()
            if grade not in GRADES:
                raise CountingEntryError("Pick a grade.")
            if not citation:
                raise CountingEntryError(CITATION_NEEDED)
            if not label:
                raise CountingEntryError("Give the entry a label before it counts toward the call.")
            if not members and grade != "flip":
                raise CountingEntryError("A whole-card entry can only be Flip.")
            if members:
                cur.execute(
                    "SELECT DISTINCT security_id FROM basket_member "
                    "WHERE thesis_id = %s AND security_id = ANY(%s)",
                    (thesis_id, members),
                )
                on_card = {r["security_id"] for r in cur.fetchall()}
                if missing := [m for m in members if m not in on_card]:
                    raise CountingEntryError(
                        "That company is not on this card."
                        if len(missing) == 1
                        else "Those companies are not on this card."
                    )
            was_counting = prev is not None and prev["counts"]
            if (
                relevant_until is not None
                and not (was_counting and relevant_until == prev["relevant_until"])
                and relevant_until <= night
            ):
                raise CountingEntryError(
                    f"“Relevant until” must be after {night.isoformat()}, the night it would count from."
                )
            if (
                was_counting
                and prev["grade"] == grade
                and _members(prev["security_ids"]) == members
                and prev["label"] == label
                and prev["citation"] == citation
                and prev["relevant_until"] == relevant_until
            ):
                return None  # store-on-diff: the same statement appends nothing
            redate = (
                not was_counting
                or prev["grade"] != grade
                or _members(prev["security_ids"]) != members
            )
            state = {
                "counts": True,
                "security_ids": members,
                "grade": grade,
                "label": label,
                "citation": citation,
                "relevant_until": relevant_until,
                "valid_from": night if redate else prev["valid_from"],
            }

        # recorded_at: the column default now(); entered_at: the SQL now() — the same transaction instant
        cur.execute(
            """INSERT INTO fact_thesis_catalyst
                   (tenant_id, thesis_id, entry_id, counts, security_ids, grade, label, citation,
                    relevant_until, source, entered_at, entered_by, valid_from, supersedes)
               VALUES (%(tenant)s, %(thesis)s, %(entry)s, %(counts)s, %(security_ids)s, %(grade)s, %(label)s,
                       %(citation)s, %(relevant_until)s, %(source)s, now(), %(user)s, %(valid_from)s,
                       %(supersedes)s)
               RETURNING id, recorded_at""",
            {
                **state,
                "tenant": tenant,
                "thesis": thesis_id,
                "entry": entry_id,
                "source": SOURCE,
                "user": user_id,
                "supersedes": prev["id"] if prev is not None else None,
            },
        )
        row = cur.fetchone()
        if entry_night(row["recorded_at"]) != night:  # one now() for the night and the row, always
            raise RuntimeError("the version's instant and its night disagree")
        return row["id"]


def current(conn: psycopg.Connection, thesis_id: UUID) -> list[dict[str, Any]]:
    """The newest version of every entry on the card (the record clock, every version visible): what the editor
    shows, switched off or not. Not a history read. Ordered by ``entry_id``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (entry_id) * FROM fact_thesis_catalyst "
            "WHERE thesis_id = %s AND tenant_id = (SELECT tenant_id FROM thesis WHERE id = %s) "
            "ORDER BY entry_id, recorded_at DESC, id DESC",
            (thesis_id, thesis_id),
        )
        return cur.fetchall()


def counting_entry_ids(conn: psycopg.Connection, thesis_id: UUID) -> set[UUID]:
    """The entries whose newest version counts (W6b's wipe guard: such a calendar row cannot be dropped)."""
    return {r["entry_id"] for r in current(conn, thesis_id) if r["counts"]}


def versions_asof(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
    asof: date,
    known_at: datetime,
    workspace: UUID,
    clock: Clock = "record",
) -> list[dict[str, Any]]:
    """The live point-in-time read: per entry, the newest version visible to night ``asof`` (``valid_from <=
    asof``) on ``clock`` at ``known_at`` — the ordinary thesis-scoped as-of read (``db.bitemporal.as_of_thesis``),
    the workspace's rows only. On the public clock a history view of night N (``known_at`` =
    ``known_at_for_asof(N)``) sees exactly the versions saved before EDGAR's next open. The mirror twin is
    ``replay.pit.ReplayPointInTimeData.thesis_catalyst_facts``. No call reads it yet (W6c)."""
    return as_of_thesis(
        conn,
        TABLE,
        thesis_id=thesis_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenants_for(TABLE, data=DATA_TENANT_ID, workspace=workspace),
        clock=clock,
    )


def counting(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The counting entries among as-of rows from EITHER engine: ``counts`` true, ``security_ids`` normalized to
    a ``frozenset[UUID]`` (``members_of``), ordered by ``entry_id``. Pure."""
    out = [{**r, "security_ids": members_of(r["security_ids"])} for r in rows if r["counts"]]
    return sorted(out, key=lambda r: str(r["entry_id"]))

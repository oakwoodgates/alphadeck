"""JOIN DATES (editions S1) — the night each company is taken to have entered each thesis's basket.

An edition re-runs a thesis only over the nights a company was actually in it. ``basket_member`` has no
time axis (full-replace on promote), so the join date is DERIVED, never stored on the member:

- **Exact** — the ``taken_at`` of the first ``basket_snapshot`` (migration 0043) that contains the company.
  That covers every company added by a promote after the 0043 deploy, including the whole first roster of
  a thesis created after it.
- **Estimated** — only the members of a 0043 DEPLOY-SEED snapshot (the roster as it stood when the
  snapshot table was created) predate the snapshot record, so their join date is unknowable and is
  estimated by the operator's rule (option A, 2026-10-19): the night we first pulled that company's data,
  never earlier than the thesis's creation. Then two refinements the operator signed off (2026-10-20):

  * **own batch** — a company pulled earlier for ANOTHER thesis would otherwise collapse onto this
    thesis's creation date, which can sit weeks before this thesis's own companies arrived. It takes the
    date this thesis's own companies (those first pulled on/after its creation) first arrived instead.
  * **card proof** — a recorded call-of-record card naming the company on this thesis is proof it was
    there that night, so the estimate never sits AFTER the first card that names it.

  And one cap: a seed member was demonstrably in the basket when the seed snapshot was taken, so its
  estimate is never later than that.

NO LOOKAHEAD (#1): every input is read as known at ``known_by`` (a system-time watermark — the edition's
cut): snapshots taken, facts recorded and cards recorded after it are invisible, so an edition's join dates
are fixed at its cut and reproducible later (the store is append-only).

``first_pull`` reads PRICE and INSIDER rows only. A price row stamped before its own bar date + 1 day is the
one-shot historical backfill's backdated stamp, not a pull, and is skipped; fundamentals are skipped
entirely (their ``recorded_at`` is backdated to ``valid_from``, D3a). Read-only throughout.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from domain.market_time import market_tz

SEED_MIGRATION = "0043_basket_snapshot.sql"


@dataclass(frozen=True)
class Snapshot:
    """One ``basket_snapshot`` row: the roster as written by one promote (or the 0043 deploy seed)."""

    taken_at: datetime
    members: tuple[dict[str, Any], ...]

    @property
    def security_ids(self) -> frozenset[UUID]:
        return frozenset(
            UUID(str(m["security_id"])) for m in self.members if m.get("security_id") is not None
        )


@dataclass(frozen=True)
class ThesisRoster:
    """Everything the join-date rule needs about one thesis, as known at the watermark."""

    thesis_id: UUID
    tenant_id: UUID
    created_at: datetime
    archived_at: datetime | None
    snapshots: tuple[Snapshot, ...]  # ascending by taken_at
    live_security_ids: frozenset[
        UUID
    ]  # today's basket_member (the fallback when no snapshot exists)
    seed: (
        Snapshot | None
    )  # the 0043 deploy-seed snapshot, when this thesis predates the snapshot table


@dataclass(frozen=True)
class Entry:
    """One company's join date on one thesis. ``estimated`` = derived by the option-A rule (a seed
    member); ``method`` names the rule that produced the date; ``evidence`` the inputs it read."""

    thesis_id: UUID
    security_id: UUID
    entry_date: date
    estimated: bool
    method: str  # snapshot | first_pull | own_batch | thesis_created | no_pull (+ "+card" / "+seed_cap")
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "thesis_id": str(self.thesis_id),
            "security_id": str(self.security_id),
            "entry_date": self.entry_date.isoformat(),
            "estimated": self.estimated,
            "method": self.method,
            "evidence": self.evidence,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Entry":
        return cls(
            thesis_id=UUID(d["thesis_id"]),
            security_id=UUID(d["security_id"]),
            entry_date=date.fromisoformat(d["entry_date"]),
            estimated=bool(d["estimated"]),
            method=d["method"],
            evidence=dict(d.get("evidence") or {}),
        )


def _mday(ts: datetime, tz: ZoneInfo) -> date:
    return ts.astimezone(tz).date()


def estimate_entries(
    roster: ThesisRoster,
    *,
    first_pulls: dict[UUID, datetime],
    first_cards: dict[UUID, date],
    tz: ZoneInfo | None = None,
) -> dict[UUID, Entry]:
    """The join date of every company this thesis has ever held (seed, later snapshots, or — with no
    snapshot at all — the live basket). PURE: a function of its inputs, which the caller has already read
    as known at the watermark. ``first_pulls`` / ``first_cards`` are keyed by security id (the thesis's own
    cards only)."""
    tz = tz or market_tz()
    created = _mday(roster.created_at, tz)
    out: dict[UUID, Entry] = {}

    # EXACT: every company first appearing in a NON-seed snapshot joined when that snapshot was taken.
    seed_ids = roster.seed.security_ids if roster.seed is not None else frozenset()
    for snap in roster.snapshots:
        if roster.seed is not None and snap is roster.seed:
            continue
        for sid in sorted(snap.security_ids - seed_ids, key=lambda u: u.int):
            if sid in out:
                continue
            out[sid] = Entry(
                thesis_id=roster.thesis_id,
                security_id=sid,
                entry_date=max(_mday(snap.taken_at, tz), created),
                estimated=False,
                method="snapshot",
                evidence={"snapshot_taken_at": snap.taken_at.isoformat()},
            )

    # ESTIMATED: the seed's members — or, for a thesis with no snapshot at all, the live basket.
    if roster.seed is not None:
        pre = seed_ids
    elif not roster.snapshots:
        pre = roster.live_security_ids
    else:
        pre = frozenset()
    pull_dates = {s: _mday(first_pulls[s], tz) for s in pre if s in first_pulls}
    own = [d for d in pull_dates.values() if d >= created]
    own_batch = min(own) if own else None
    seed_day = _mday(roster.seed.taken_at, tz) if roster.seed is not None else None

    for sid in sorted(pre, key=lambda u: u.int):
        evidence: dict[str, Any] = {"created": created.isoformat()}
        fp = pull_dates.get(sid)
        if fp is None:
            entry, method = created, "no_pull"
        elif fp >= created:
            entry, method = fp, "first_pull"
            evidence["first_pull"] = fp.isoformat()
        else:  # pulled before this thesis existed — for another thesis, or a Workbench pull
            evidence["first_pull"] = fp.isoformat()
            if own_batch is not None:
                entry, method = own_batch, "own_batch"
                evidence["own_batch"] = own_batch.isoformat()
            else:
                entry, method = created, "thesis_created"
        card = first_cards.get(sid)
        if card is not None:
            evidence["first_card"] = card.isoformat()
            if card < entry:
                entry, method = card, method + "+card"
        if seed_day is not None and entry > seed_day:
            entry, method = seed_day, method + "+seed_cap"
            evidence["seed"] = seed_day.isoformat()
        out[sid] = Entry(
            thesis_id=roster.thesis_id,
            security_id=sid,
            entry_date=max(entry, created),
            estimated=True,
            method=method,
            evidence=evidence,
        )
    return out


# --- the reads (all as known at ``known_by``) ------------------------------------------------------


def seed_cutoff(conn: psycopg.Connection) -> datetime | None:
    """When the snapshot table was created (the 0043 deploy) — its seed snapshots were written in that
    transaction, so their ``taken_at`` equals this. ``None`` when 0043 is somehow unapplied."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT applied_at FROM schema_migrations WHERE filename = %s", (SEED_MIGRATION,)
        )
        row = cur.fetchone()
    return row["applied_at"] if row else None


def load_rosters(
    conn: psycopg.Connection, *, known_by: datetime, cutoff: datetime | None
) -> list[ThesisRoster]:
    """Every thesis (archived included — the record is not erased by archiving) with its snapshots taken
    at/before ``known_by``. A thesis created before ``cutoff`` whose FIRST snapshot was taken at/before the
    cutoff carries that snapshot as its ``seed``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, tenant_id, created_at, archived_at FROM thesis "
            "WHERE created_at <= %s ORDER BY created_at, id",
            (known_by,),
        )
        theses = cur.fetchall()
        cur.execute(
            "SELECT thesis_id, taken_at, members FROM basket_snapshot "
            "WHERE taken_at <= %s ORDER BY thesis_id, taken_at, id",
            (known_by,),
        )
        snaps: dict[UUID, list[Snapshot]] = defaultdict(list)
        for r in cur.fetchall():
            snaps[r["thesis_id"]].append(Snapshot(r["taken_at"], tuple(r["members"] or ())))
        cur.execute(
            "SELECT thesis_id, security_id FROM basket_member WHERE security_id IS NOT NULL"
        )
        live: dict[UUID, set[UUID]] = defaultdict(set)
        for r in cur.fetchall():
            live[r["thesis_id"]].add(r["security_id"])
    out = []
    for t in theses:
        ss = tuple(snaps.get(t["id"], ()))
        seed = None
        if cutoff is not None and ss and t["created_at"] < cutoff and ss[0].taken_at <= cutoff:
            seed = ss[0]
        out.append(
            ThesisRoster(
                thesis_id=t["id"],
                tenant_id=t["tenant_id"],
                created_at=t["created_at"],
                archived_at=t["archived_at"],
                snapshots=ss,
                live_security_ids=frozenset(live.get(t["id"], ())),
                seed=seed,
            )
        )
    return out


def load_first_pulls(
    conn: psycopg.Connection, *, known_by: datetime
) -> dict[tuple[UUID, UUID], datetime]:
    """``(tenant_id, security_id) -> the earliest non-backdated recorded_at`` over price + insider rows
    recorded at/before ``known_by``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, security_id, min(fp) AS fp FROM ("
            "  SELECT tenant_id, security_id, min(recorded_at) AS fp FROM fact_price_eod "
            "  WHERE recorded_at >= (d + interval '1 day') AND recorded_at <= %(kb)s GROUP BY 1, 2 "
            "  UNION ALL "
            "  SELECT tenant_id, security_id, min(recorded_at) FROM fact_insider_txn "
            "  WHERE recorded_at <= %(kb)s GROUP BY 1, 2"
            ") x GROUP BY 1, 2",
            {"kb": known_by},
        )
        return {(r["tenant_id"], r["security_id"]): r["fp"] for r in cur.fetchall()}


def load_first_cards(
    conn: psycopg.Connection, *, known_by: datetime
) -> dict[tuple[UUID, UUID], date]:
    """``(thesis_id, security_id) -> the first honest (non-reconstructed) call-of-record as-of whose card
    names the company`` — armed, watch, a fired trigger or a risk signal — recorded at/before ``known_by``.
    A card names a company only when it is armed or on watch, so this proves presence, never absence.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.thesis_id, x.sid, min(c.asof) AS first_asof
            FROM calls c
            CROSS JOIN LATERAL (
              SELECT e->>'security_id' AS sid
                FROM jsonb_array_elements(COALESCE(c.card->'armed_members', '[]'::jsonb)) e
              UNION
              SELECT e->>'security_id'
                FROM jsonb_array_elements(COALESCE(c.card->'watch_members', '[]'::jsonb)) e
              UNION
              SELECT e->>'security_id'
                FROM jsonb_array_elements(COALESCE(c.card->'triggers_fired', '[]'::jsonb)) e
              UNION
              SELECT e->>'security_id'
                FROM jsonb_array_elements(COALESCE(c.card->'risk_signals', '[]'::jsonb)) e
            ) x
            WHERE NOT c.reconstructed AND c.recorded_at <= %(kb)s AND x.sid IS NOT NULL
            GROUP BY 1, 2
            """,
            {"kb": known_by},
        )
        return {(r["thesis_id"], UUID(r["sid"])): r["first_asof"] for r in cur.fetchall()}


def derive_all(
    conn: psycopg.Connection, *, known_by: datetime
) -> tuple[list[ThesisRoster], dict[UUID, dict[UUID, Entry]]]:
    """Every thesis's roster history and its join dates, as known at ``known_by``. Read-only."""
    cutoff = seed_cutoff(conn)
    rosters = load_rosters(conn, known_by=known_by, cutoff=cutoff)
    pulls = load_first_pulls(conn, known_by=known_by)
    cards = load_first_cards(conn, known_by=known_by)
    entries: dict[UUID, dict[UUID, Entry]] = {}
    for r in rosters:
        entries[r.thesis_id] = estimate_entries(
            r,
            first_pulls={sid: ts for (ten, sid), ts in pulls.items() if ten == r.tenant_id},
            first_cards={sid: d for (tid, sid), d in cards.items() if tid == r.thesis_id},
        )
    return rosters, entries


# --- the report CLI ---------------------------------------------------------------------------------


def report(conn: psycopg.Connection, *, known_by: datetime) -> str:
    """The operator-facing list: per thesis, how many join dates are exact / estimated, by which rule, the
    span, and whether today's basket equals the latest snapshot (G1's sanity check: "no basket changed
    since the seed" must be a fact, not a broken snapshot writer)."""
    rosters, entries = derive_all(conn, known_by=known_by)
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM thesis")
        names = {r["id"]: r["name"] for r in cur.fetchall()}
    lines = [f"JOIN DATES as known at {known_by.isoformat()}"]
    total = Counter()
    for r in rosters:
        es = entries[r.thesis_id]
        methods = Counter(e.method for e in es.values())
        n_est = sum(1 for e in es.values() if e.estimated)
        latest = r.snapshots[-1].security_ids if r.snapshots else None
        sync = (
            "no snapshot"
            if latest is None
            else (
                "basket == latest snapshot"
                if latest == r.live_security_ids
                else "BASKET != LATEST SNAPSHOT"
            )
        )
        span = (
            f"{min(e.entry_date for e in es.values())} .. {max(e.entry_date for e in es.values())}"
            if es
            else "-"
        )
        lines.append(
            f"  {names.get(r.thesis_id, str(r.thesis_id))[:34]:34} companies={len(es):4} "
            f"estimated={n_est:4} snapshots={len(r.snapshots)} joins {span} | {sync} | "
            + ", ".join(f"{m}={n}" for m, n in sorted(methods.items()))
        )
        total.update(methods)
        total["_estimated"] += n_est
        total["_all"] += len(es)
    lines.append(
        f"TOTAL companies-in-theses={total['_all']} estimated={total['_estimated']} | "
        + ", ".join(f"{m}={n}" for m, n in sorted(total.items()) if not m.startswith("_"))
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from db.session import connect

    ap = argparse.ArgumentParser(description="Print every thesis's join dates (read-only).")
    ap.add_argument(
        "--report", action="store_true", help="print the join-date report (the default)"
    )
    ap.add_argument(
        "--known-by", type=datetime.fromisoformat, default=None, help="watermark (default now)"
    )
    args = ap.parse_args(argv)
    conn = connect()
    conn.read_only = True
    try:
        print(report(conn, known_by=args.known_by or datetime.now(timezone.utc)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

"""Repair CLI: re-date the stored DOE award catalysts that were dated by their WORK PERIOD's start.

THE GAP (MEASURED on prod, 2026-11-11). Until rule ``2026-11-catalysts-dated-by-signing`` the DOE feed set a
catalyst's event date from the award's ``period_of_performance.start_date``. ``fact_catalyst.valid_from`` is
that date, and the public clock reads it as the day the catalyst became public (``db.public_clock``:
``fact_catalyst`` is the ``date`` kind on ``valid_from``). An award can be SIGNED months after its work period
is dated to start, and until it is signed there is no award. The committed payloads
(``seed_data/doe/award/*.json``) show it for 3 of the 6 seeded awards:

- ``ASST_NON_DENE0009589_089``: start 2026-02-09, signed 2026-05-18 (98 days)
- ``ASST_NON_DENE0000530_089``: start 2012-06-01, signed 2012-11-30
- ``ASST_NON_DESC0003997_089``: start 2010-03-23, signed 2010-12-21
- the three ``CONT_AWD_*`` contracts: start == signed

``signals/catalyst_conviction.py`` fires at ``valid_from``, so a history view or a backtest between an award's
start and its signing read a catalyst that did not exist yet: an invariant #1 lookahead.

THE RULE is ``ingest.doe.feed.parse_award`` (its ``_event_date``: the signing date, else the work period's
start) — IMPORTED and RUN on the award's payload, never re-stated here, so this tool and the feed cannot
drift apart. The feed already writes new rows under the rule; this tool is the one-time repair for the rows
stored before it.

WHAT IT DOES, per stored natural key ``(tenant, security_id, source_ref)``:

- it re-parses the award from the COMMITTED payload on disk (``seed_data/doe``, the same offline client
  ``pipeline.seed.seed_doe_catalysts`` builds). **Never the network.** A key with no payload on disk is
  listed as "no payload, left as stored" and is never changed;
- a key is MIS-DATED when any stored version's ``valid_from`` differs from the rule's event date;
- ``--apply`` DELETES every version of a mis-dated key whose ``valid_from`` is not the rule's date, then, if
  no version dated at the rule's date remains, APPENDS one corrected version through
  ``ingest.catalyst.ingest_catalyst`` (grade, label and ``horizon_end`` from the parse; ``recorded_at`` = now,
  never backdated). The stored ``source_ref`` is kept, so the corrected version is the same logical fact.

WHY IT DELETES, and does not only append a corrected version. On this table a version's date IS its
``valid_from``, and both clocks gate a read on ``valid_from <= asof`` per VERSION: on the public clock each
version counts from its OWN ``valid_from``. A corrected version dated at the signing is invisible to a view
between the two dates, so that view would fall back to the early-dated version and still read the catalyst
before it existed. Appending alone would leave the lookahead exactly where it is. Only removing the
early-dated versions closes it. (``fact_catalyst`` carries a ``no_update`` trigger, so a date cannot be fixed
in place; that trigger guards UPDATE only.) The delete leaves the table in the state a fresh feed run under
the rule would produce, and it is journaled as a deleting repair so the editions cut re-reads history.

SCOPE: rows the DOE feed wrote and nobody ratified (``source = 'doe_usaspending'`` and ``ratified_by IS
NULL``). A ratified row (``source = 'ratified'`` or a ``ratified_by``) is the operator's and is NEVER
touched. One that sits on an award this tool re-dates, at a different date, is LISTED so it is not missed:
it is still read from its own date.

SAFETY (the ``repair_impossible_txn_dates`` shape):
- **Dry-run is the DEFAULT**: it prints one line per key and writes nothing (its connection is read-only).
- ``DATABASE_URL`` MUST be set explicitly (no dev-default fallback for a destructive tool); the target is
  printed (host:port/db, credentials redacted) as the first line of every run.
- ``--apply`` holds the REPAIR lock (call-of-record + the fact-writer gate; exit 75 when busy) and writes in
  ONE transaction. The counts must match the plan (deleted == planned, appended == planned, the table moved
  by exactly appended - deleted) or the run ROLLS BACK and exits non-zero.
- Every company touched gets a change stamp (``db.change_stamp.mark_changed``), so dashboards re-read it.
- One ``data_repair`` journal row per ``--apply``. A second ``--apply`` finds nothing to change.

    DATABASE_URL=... python -m pipeline.repair_catalyst_dates            # dry-run (default)
    DATABASE_URL=... python -m pipeline.repair_catalyst_dates --apply    # delete + append + counts

Never run against prod by an agent — the operator runs it, after a backup.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from db.change_stamp import mark_changed
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import CatalystType
from ingest import CacheMiss
from ingest.catalyst import ingest_catalyst
from ingest.doe.client import UsaSpendingClient
from ingest.doe.feed import _SOURCE as FEED_SOURCE
from ingest.doe.feed import DoeCatalyst, parse_award, usaspending_award_url
from pipeline.dedup_identical_versions import require_database_url
from pipeline.run_lock import repair_lock_or_exit
from repositories import data_repair_repo

TOOL = "repair_catalyst_dates"
TABLE = "fact_catalyst"

# The committed USASpending payloads — the directory ``pipeline.seed.seed_doe_catalysts`` reads (a test pins
# the two equal). The repair re-parses from here and nowhere else.
PAYLOAD_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "doe"

# What the rule says about one stored key:
# - re-date: a stored version is dated off the rule's date (delete those; append one if none is left)
# - keep: every stored version is already dated at the rule's date
# - no payload: the award's payload is not on disk (left as stored, never changed)
# - no date: the payload carries neither date, so the rule cannot date it (left as stored)
RE_DATE = "re-date"
KEEP = "keep"
NO_PAYLOAD = "no payload"
NO_DATE = "no date"
ACTIONS = (RE_DATE, KEEP, NO_PAYLOAD, NO_DATE)


@dataclass
class KeyPlan:
    """One stored natural key and what the rule says about it — everything the report prints."""

    tenant_id: UUID
    security_id: UUID
    ticker: str | None
    source_ref: str
    award_id: str
    versions: list[dict[str, Any]]  # every stored feed version of the key, oldest first
    parsed: DoeCatalyst | None  # the award under the CURRENT rule (``None``: no payload / no date)
    action: str
    appended_id: UUID | None = None  # set by ``apply``

    @property
    def stored_dates(self) -> list[date]:
        return sorted({v["valid_from"] for v in self.versions})

    @property
    def rule_date(self) -> date | None:
        return self.parsed.event_date if self.parsed is not None else None

    @property
    def to_delete(self) -> list[UUID]:
        """The versions dated off the rule's date (empty unless the key is mis-dated)."""
        if self.action != RE_DATE:
            return []
        return [v["id"] for v in self.versions if v["valid_from"] != self.rule_date]

    @property
    def needs_append(self) -> bool:
        """A mis-dated key with NO version at the rule's date: one corrected version is appended."""
        return self.action == RE_DATE and all(
            v["valid_from"] != self.rule_date for v in self.versions
        )


@dataclass
class ApplyResult:
    deleted: int = 0
    appended: int = 0
    rows_before: int = 0
    rows_after: int = 0
    securities: list[UUID] = field(default_factory=list)


def award_id_of(source_ref: str) -> str:
    """The USASpending award id behind a feed row's ``source_ref`` — the inverse of the feed's
    ``usaspending_award_url`` (``<base>/<id>``). A row stored under another base URL falls back to the last
    path segment."""
    prefix = usaspending_award_url("")
    if source_ref.startswith(prefix) and len(source_ref) > len(prefix):
        return source_ref[len(prefix) :]
    return source_ref.rstrip("/").rsplit("/", 1)[-1]


def offline_client() -> UsaSpendingClient:
    """The committed payloads, live pulls OFF — a missing payload raises ``CacheMiss``, never fetches."""
    return UsaSpendingClient(cache_dir=PAYLOAD_DIR, allow_live=False)


def _count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {TABLE}")  # noqa: S608 — a module constant
        return cur.fetchone()["n"]


def scan(
    conn: psycopg.Connection,
    *,
    client: UsaSpendingClient | None = None,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> list[KeyPlan]:
    """Every stored DOE-feed key with the rule's reading of it. Read-only: one SELECT, then the pure parse.

    ALL versions of each key, not the latest-version view: on this table every version counts from its own
    ``valid_from``, so an early-dated older version is still read by a view before the newer one's date.
    """
    client = client or offline_client()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.id, f.tenant_id, f.security_id, f.source_ref, f.valid_from, f.recorded_at, "
            "       f.grade, f.label, f.horizon_end, sm.ticker "
            f"  FROM {TABLE} f "  # noqa: S608 — a module constant
            "  LEFT JOIN security_master sm ON sm.id = f.security_id "
            " WHERE f.source = %s AND f.ratified_by IS NULL "
            " ORDER BY sm.ticker NULLS LAST, f.source_ref, f.tenant_id, f.security_id, "
            "          f.recorded_at, f.id",
            (FEED_SOURCE,),
        )
        rows = cur.fetchall()
    grouped: dict[tuple[UUID, UUID, str], list[dict[str, Any]]] = {}
    for r in rows:
        grouped.setdefault((r["tenant_id"], r["security_id"], r["source_ref"]), []).append(r)

    plans: list[KeyPlan] = []
    for (tenant_id, security_id, source_ref), versions in grouped.items():
        ticker = versions[0]["ticker"]
        award_id = award_id_of(source_ref)
        parsed: DoeCatalyst | None = None
        try:
            parsed = parse_award(client, award_id, ticker or "", cfg)
        except CacheMiss:
            action = NO_PAYLOAD
        else:
            if parsed is None:
                action = NO_DATE
            elif any(v["valid_from"] != parsed.event_date for v in versions):
                action = RE_DATE
            else:
                action = KEEP
        plans.append(
            KeyPlan(
                tenant_id=tenant_id,
                security_id=security_id,
                ticker=ticker,
                source_ref=source_ref,
                award_id=award_id,
                versions=versions,
                parsed=parsed,
                action=action,
            )
        )
    return plans


def ratified_beside(conn: psycopg.Connection, plans: list[KeyPlan]) -> list[dict[str, Any]]:
    """The RATIFIED rows that sit on an award this tool re-dates, at a date other than the rule's. Never
    touched (the operator's) — listed so the operator sees that a history view still reads them from their
    own date."""
    rule_dates = {(p.security_id, p.source_ref): p.rule_date for p in plans if p.action == RE_DATE}
    if not rule_dates:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.id, f.security_id, f.source_ref, f.valid_from, f.source, f.ratified_by, sm.ticker "
            f"  FROM {TABLE} f "  # noqa: S608 — a module constant
            "  LEFT JOIN security_master sm ON sm.id = f.security_id "
            " WHERE f.source_ref = ANY(%s) AND (f.source = 'ratified' OR f.ratified_by IS NOT NULL) "
            " ORDER BY sm.ticker NULLS LAST, f.source_ref, f.recorded_at, f.id",
            (sorted({ref for _, ref in rule_dates}),),
        )
        rows = cur.fetchall()
    return [
        r
        for r in rows
        if (r["security_id"], r["source_ref"]) in rule_dates
        and r["valid_from"] != rule_dates[(r["security_id"], r["source_ref"])]
    ]


def apply(conn: psycopg.Connection, plans: list[KeyPlan]) -> ApplyResult:
    """Write the plan in the caller's open transaction and COMMIT it — or roll back and raise.

    Deletes first (one statement, so a ``supersedes`` chain inside the deleted set goes together), then the
    appends, then the change stamps, then the three count checks. Any mismatch, and any database error (a
    kept version that ``supersedes`` a deleted one would raise a foreign-key error), rolls the whole
    transaction back and raises ``RuntimeError``: nothing is written.
    """
    result = ApplyResult(rows_before=_count(conn))
    delete_ids = [i for p in plans for i in p.to_delete]
    # a key that needs an append is mis-dated, so it has a parse (``scan``)
    to_append = [(p, p.parsed) for p in plans if p.needs_append and p.parsed is not None]
    try:
        if delete_ids:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {TABLE} WHERE id = ANY(%s)",  # noqa: S608 — a module constant
                    (delete_ids,),
                )
                result.deleted = cur.rowcount
        for p, parsed in to_append:
            p.appended_id = ingest_catalyst(
                conn,
                p.security_id,
                catalyst_type=CatalystType.GOV_FUNDING,
                grade=parsed.grade,
                label=parsed.label,
                source=FEED_SOURCE,
                source_ref=p.source_ref,  # the STORED key, so this is the same logical fact
                event_date=parsed.event_date,
                horizon_end=parsed.horizon_end,
                tenant_id=p.tenant_id,
            )
            result.appended += 1
        # the append stamps its company; a delete changes a company's stored data too, so stamp every one
        result.securities = sorted({p.security_id for p in plans if p.to_delete})
        mark_changed(conn, result.securities)
        result.rows_after = _count(conn)
    except psycopg.Error as exc:
        conn.rollback()
        for p in plans:
            p.appended_id = None
        raise RuntimeError(
            f"the database refused the write ({exc.__class__.__name__}: {exc}) — ROLLED BACK, "
            "nothing written"
        ) from exc

    problems = []
    if result.deleted != len(delete_ids):
        problems.append(
            f"planned to delete {len(delete_ids)} row(s), the DELETE reported {result.deleted}"
        )
    if result.appended != len(to_append):
        problems.append(f"planned to append {len(to_append)} row(s), appended {result.appended}")
    if result.rows_after != result.rows_before + result.appended - result.deleted:
        problems.append(
            f"{TABLE} went {result.rows_before} -> {result.rows_after}, expected "
            f"{result.rows_before + result.appended - result.deleted}"
        )
    if problems:
        conn.rollback()
        for p in plans:
            p.appended_id = None
        raise RuntimeError("; ".join(problems) + " — ROLLED BACK, nothing written")
    conn.commit()
    return result


# --- the report ------------------------------------------------------------------------------------------


def _redact(url: str) -> str:
    """host:port/dbname only — NEVER the credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}{parts.path}"


def _line(p: KeyPlan) -> str:
    name = p.ticker or str(p.security_id)
    stored = ", ".join(d.isoformat() for d in p.stored_dates)
    head = f"  {name} {p.award_id}: stored {stored}"
    if p.action == NO_PAYLOAD:
        return f"{head} · rule n/a · start n/a · signed n/a -> no payload, left as stored"
    if p.parsed is None:
        return (
            f"{head} · rule n/a · start n/a · signed n/a -> no date in the payload, left as stored"
        )
    c = p.parsed
    dates = (
        f" · rule {c.event_date.isoformat()} · start {c.start_date or 'n/a'} · "
        f"signed {c.date_signed or 'n/a'}"
    )
    if p.action == KEEP:
        return f"{head}{dates} -> keep"
    return (
        f"{head}{dates} -> re-date (delete {len(p.to_delete)} version(s), "
        f"append {1 if p.needs_append else 0})"
    )


def report(plans: list[KeyPlan], ratified: list[dict[str, Any]]) -> Counter[str]:
    for p in plans:
        print(_line(p))
    for r in ratified:
        print(
            f"  NOTE {r['ticker'] or r['security_id']} {award_id_of(r['source_ref'])}: a RATIFIED version "
            f"dated {r['valid_from'].isoformat()} (source={r['source']}, ratified_by={r['ratified_by']}) "
            f"is the operator's — never touched, still read from that date [row {r['id']}]"
        )
    return Counter(p.action for p in plans)


def _summary_counts(plans: list[KeyPlan], per_action: Counter[str]) -> str:
    return (
        f"{len(plans)} key(s): {per_action[RE_DATE]} re-date, {per_action[KEEP]} keep, "
        f"{per_action[NO_PAYLOAD]} no payload, {per_action[NO_DATE]} no date"
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Re-date the stored DOE award catalysts that were dated by the work period's start: "
        "under the current rule a catalyst counts from the award's signing date. Re-parses the committed "
        "payloads (never the network). Dry-run by default; --apply deletes the early-dated versions and "
        "appends the corrected ones under the repair lock."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="delete + append in one transaction (default: dry-run, print only)",
    )
    args = p.parse_args(argv)

    url = require_database_url()
    print(f"target DB : {_redact(url)}")

    # W1 — a writing run excludes the nightly pass AND every company writer (pipeline.run_lock); exit 75 if busy
    lock = repair_lock_or_exit(holder=TOOL, conninfo=url) if args.apply else None
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            with conn.cursor() as cur:
                cur.execute("SELECT current_database() AS db")
                print(f"connected : current_database() = {cur.fetchone()['db']}")
            print(f"payloads  : {PAYLOAD_DIR} (offline — no network)")

            plans = scan(conn)
            ratified = ratified_beside(conn, plans)
            planned_delete = sum(len(pl.to_delete) for pl in plans)
            planned_append = sum(1 for pl in plans if pl.needs_append)

            if not args.apply:
                print(f"\n=== {TOOL} — DRY-RUN (nothing written) ===")
                per_action = report(plans, ratified)
                conn.rollback()  # read-only; nothing to commit
                print(
                    f"\nSUMMARY: dry-run only, nothing written — {_summary_counts(plans, per_action)} · "
                    f"would delete {planned_delete} row(s) and append {planned_append}; rerun with "
                    "--apply to write"
                )
                return

            print(f"\n=== {TOOL} — APPLY ===")
            per_action = report(plans, ratified)
            try:
                result = apply(conn, plans)
            except RuntimeError as exc:
                print(f"\nABORTED: {exc}")
                raise SystemExit(1) from None
            print(
                f"\nSUMMARY: {_summary_counts(plans, per_action)} · total_deleted={result.deleted} "
                f"total_appended={result.appended} · {TABLE} rows {result.rows_before} -> "
                f"{result.rows_after} · {len(result.securities)} company change stamp(s)"
            )
            # S7 — the data-repair journal: one row per --apply (a DELETE + an append), under the lock
            data_repair_repo.journal(
                conn,
                tool=TOOL,
                summary=(
                    f"re-dated {per_action[RE_DATE]} DOE award catalyst(s) to the award's signing date "
                    f"(deleted {result.deleted} early-dated row(s), appended {result.appended})"
                ),
                rows_changed=result.deleted + result.appended,
                deletes=True,
                counts={
                    "deleted": result.deleted,
                    "appended": result.appended,
                    "keys": len(plans),
                    "ratified_left": len(ratified),
                    **{a: per_action[a] for a in ACTIONS},
                },
            )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    main()

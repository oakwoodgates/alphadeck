"""Re-point existing basket members from a non-primary CIK sibling to the canonical instrument — the
ONE-TIME data fix behind the canonical-primary slice (run AFTER the migration + a `populate_master` pass
has flagged `is_primary`).

Before the fix, `ids_for_ciks` collapsed a multi-sibling CIK to an ARBITRARY row, and promote froze
whichever sibling the draft surfaced — so live baskets (the Board's Nuclear theses) may hold a warrant /
foreign-ordinary / dual-class sibling where the operator means the primary US listing. This walks every
thesis, maps each placed member through `master.canonicalize_ids` (the same pick promote now re-asserts),
and UPDATEs `basket_member.security_id` + `ticker` in place.

- **Idempotent** — a second run finds nothing to re-point (COUNT the table, not the read).
- **Duplicate-safe** — if a thesis somehow holds BOTH siblings (the pre-fix re-draft duplicate bug), the
  non-primary one is SKIPPED with a loud warning, never collapsed silently: merging two operator-visible
  rows is the operator's prune, not a script's (#9 — the interface never drops a row behind their back).
- **Facts note** — facts ingested under the old sibling id remain (harmless orphans; nothing reads them
  through the member); the incremental ingest refills under the canonical id on the next daily run.

    python -m pipeline.repoint_canonical            # report + apply
    python -m pipeline.repoint_canonical --dry-run  # report only
"""

from __future__ import annotations

import argparse

from db.session import connect
from repositories import thesis_repo
from securities import master


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Re-point basket members to canonical-primary siblings."
    )
    p.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    a = p.parse_args(argv)

    conn = connect()
    repointed = skipped = 0
    try:
        for t in thesis_repo.list_all(conn):
            ids = [m.security_id for m in t.basket if m.security_id]
            canon = master.canonicalize_ids(conn, ids, tenant_id=t.tenant_id)
            if not canon:
                continue
            held = set(ids)
            for m in t.basket:
                hit = canon.get(m.security_id) if m.security_id else None
                if hit is None:
                    continue
                primary_id, primary_ticker = hit
                if primary_id in held:
                    # both siblings are in this basket (the pre-fix duplicate bug) — the operator prunes,
                    # a script never merges two visible rows silently
                    print(
                        f"SKIP  {t.name!r}: {m.ticker} -> {primary_ticker} — the canonical row is already "
                        "a member (duplicate pair; prune by hand)"
                    )
                    skipped += 1
                    continue
                print(f"REPOINT  {t.name!r}: {m.ticker} -> {primary_ticker} ({primary_id})")
                repointed += 1
                if not a.dry_run:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE basket_member SET security_id = %s, ticker = %s "
                            "WHERE thesis_id = %s AND security_id = %s",
                            (primary_id, primary_ticker, t.id, m.security_id),
                        )
        if not a.dry_run:
            conn.commit()
        print(
            f"{'DRY RUN — ' if a.dry_run else ''}{repointed} member(s) re-pointed, "
            f"{skipped} duplicate pair(s) skipped"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

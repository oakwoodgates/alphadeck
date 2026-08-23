"""CLI for the SPAC shell sweep — enrich un-enriched SPAC-structured CIKs' SEC SIC (facts-only),
so the genuinely-6770 ones flag as blank-check (triage TYPE + the radar's known-shell set).

    python -m pipeline.spac_sweep --live               # the real catch-up / nightly unit
    python -m pipeline.spac_sweep                      # cache-only (dev; uncached CIKs skip loud)
    python -m pipeline.spac_sweep --live --reenrich    # + re-pull the current Blank Checks set
    python -m pipeline.spac_sweep --live --cap 50      # bound the run; remaining self-continues

``--live`` is OPT-IN (the enrichment-CLI convention — ``enrich_identity`` / ``populate_master``):
the operator's catch-up run is an explicit action, never ambient. Freshness needs no flag:
``submissions/*`` is a mutable cache class on the key-classed 12h TTL, so under ``--live`` a
week-old doc re-fetches by default (the #196 rule — no per-call boolean to forget).

Exit codes (the ``enrich_identity`` scriptable-health precedent, NOT the radar's any-error->1):
0 on a normal run — including offline cache-miss skips (the expected ``--no-live`` dev shape) and
a live PARTIAL run (errors print loud; the wrapper isn't paged for a transient per-CIK fault);
1 when a ``--live`` run enriched NOTHING while candidates existed — that shape is a network/UA/
parse fault wearing a clean exit, not a quiet no-op.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from db.session import connect, current_tenant_id
from radar.shell_sweep import run_shell_sweep


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SPAC shell sweep: enrich un-enriched SPAC-structured CIKs' SEC SIC "
        "(facts-only — the SEC sicDescription is the sole shell-or-not authority)."
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="allow live EDGAR fetches (needs ALPHADECK_USER_AGENT); else cache-only "
        "(an uncached CIK skips, visibly)",
    )
    p.add_argument(
        "--cap",
        type=int,
        default=300,
        help="max CIKs to sweep this run (default 300); a capped run logs remaining=N "
        "and self-continues next run",
    )
    p.add_argument(
        "--reenrich",
        action="store_true",
        help="also re-pull the SIC for the current Blank Checks set (least-recently-enriched "
        "first), so a de-SPAC'd shell's flip is caught and it stops flagging",
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="target tenant UUID; defaults to the deployment tenant ($ALPHADECK_TENANT_ID / demo)",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        result = run_shell_sweep(
            conn,
            allow_live=args.live,
            cap=args.cap,
            reenrich=args.reenrich,
            tenant_id=UUID(args.tenant_id) if args.tenant_id else current_tenant_id(),
        )
    finally:
        conn.close()

    print(f"shell sweep: {result.summary}")
    if result.admitted:
        print(f"  admitted: {', '.join(result.admitted)}")
    if result.flipped:
        print(f"  de-SPAC'd: {', '.join(result.flipped)}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    # The scriptable health gate (the enrich_identity / audit_identity exit-code precedent): a LIVE
    # run that enriched NOTHING while candidates existed is a fault shape, not a quiet no-op —
    # surface it to a wrapper. A cache-first run skipping uncached CIKs is expected and stays 0.
    if args.live and result.attempted > 0 and result.enriched == 0:
        print(
            "SWEEP: --live run enriched nothing while candidates existed — "
            "investigate before trusting"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

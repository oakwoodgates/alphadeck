"""The identity-coherence AUDIT — the standing answer to "is a misbind happening, and how many are there?"

Read-only. Three sweeps, loud only on the exception (a clean run prints three OK lines):

1. **Master health** — ``master.primary_flag_gaps``: a tenant with multi-row CIKs and ZERO ``is_primary``
   flags resolves CIK→security to an ARBITRARY sibling (the warrant/OTC-line class). Named + counted.
2. **The spine** — every ``basket_member`` with a bound ``security_id`` is classified through
   ``securities.coherence`` (the same definition the promote guard enforces): cross-company / label-drift /
   sibling members are printed with BOTH identities and the owning thesis. The spine should read ZERO
   non-OK — promote fail-closes new ones, so anything here is pre-guard damage to repair.
3. **Draft-run logs** (``--draft-runs DIR``) — the persisted draft history (one JSON per completed draft):
   every placement carrying a ``security_id`` is classified the same way; LABEL_DRIFT rows are
   sub-classified against the CURRENT SEC company_tickers file (cache-first; ``--live`` refreshes):
   - shown ticker IS in the SEC file → the master is stale → run ``pipeline.populate_master``;
   - not in the file but the shown NAME matches the bound row's name → the same issuer's non-equity /
     renamed line (a benign label, the ETN/rename class);
   - neither → UNRESOLVED — the operator's review list (a real foreign filer = the 20-F backlog; anything
     else is genuine breakage). Silence is the one thing this bucket is not allowed to do.

Exit code 1 when anything needs a human (a zero-flag tenant, a non-OK spine member, a cross-company draft
row, or an unresolved shown label); 0 on a clean audit — so a cron/CI wrapper can gate on it.

    python -m pipeline.audit_identity
    python -m pipeline.audit_identity --draft-runs data/draft_runs
    python -m pipeline.audit_identity --draft-runs data/draft_runs --live
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from uuid import UUID

from db.session import connect, current_tenant_id
from securities import coherence, master, sec_tickers
from securities.coherence import CoherenceKind

_WS = re.compile(r"[^A-Z0-9]+")
# Trailing legal-form tokens stripped for the display-name equality test ('Cybin' == 'CYBIN INC.'). Only
# TRAILING tokens, iteratively — 'Alpha Compute Corp' vs 'AlphaTON Capital Corp' still differ after both
# lose CORP, so the strip can't merge distinct companies whose names differ before the suffix.
_LEGAL_SUFFIXES = {
    "INC",
    "INCORPORATED",
    "CORP",
    "CORPORATION",
    "LTD",
    "LIMITED",
    "PLC",
    "CO",
    "COMPANY",
    "HOLDINGS",
    "HOLDING",
    "GROUP",
    "LLC",
    "LP",
    "NV",
    "AG",
    "SA",
}


def _norm_name(s: str | None) -> str:
    """Collapse case/punctuation and trailing legal-form tokens so 'MAXLINEAR, INC' == 'MaxLinear Inc.' and
    'Cybin' == 'CYBIN INC.' — a display-name equality test, never an identity decision (#2: only used to
    sort benign labels from review items)."""
    tokens = _WS.sub(" ", (s or "").upper()).split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _walk_placements(obj, out: list[dict]) -> None:
    """Collect placement-shaped dicts (a ``security_id`` + a ``ticker``/``name``) anywhere in a draft-run
    JSON — tolerant of report-shape evolution (the log is write-only history; old shapes must still audit).
    """
    if isinstance(obj, dict):
        if "security_id" in obj and ("ticker" in obj or "name" in obj):
            out.append(obj)
        for v in obj.values():
            _walk_placements(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_placements(v, out)


def audit_spine(conn) -> int:
    """Sweep 2 — classify every bound basket member; print non-OK with both identities. Returns the count
    that needs a human (anything non-OK: the spine is post-guard territory, ALL disagreement is damage).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.name AS thesis, bm.ticker, bm.security_id, bm.tenant_id "
            "FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "WHERE bm.security_id IS NOT NULL ORDER BY t.name, bm.ordinal"
        )
        rows = cur.fetchall()
    bad = 0
    by_tenant: dict = {}
    for r in rows:
        by_tenant.setdefault(r["tenant_id"], []).append(r)
    for tenant_id, members in by_tenant.items():
        findings = coherence.classify_members(
            conn, [(m["ticker"], m["security_id"]) for m in members], tenant_id=tenant_id
        )
        for m, f in zip(members, findings):
            if f.kind is CoherenceKind.OK:
                continue
            bad += 1
            print(
                f"  SPINE {f.kind.upper()}: thesis {m['thesis']!r} member {m['ticker']!r} -> "
                f"bound {f.bound_ticker} ({f.bound_name}, CIK {f.bound_cik}) -- {f.detail}"
            )
    print(f"spine: {len(rows)} bound members, {bad} need review" + (" -- OK" if bad == 0 else ""))
    return bad


def audit_draft_runs(conn, root: Path, *, allow_live: bool) -> int:
    """Sweep 3 — classify every draft-run placement; sub-classify LABEL_DRIFT via the SEC file + a
    bound-name match. Returns the count needing a human (cross-company + unresolved labels)."""
    files = sorted(root.rglob("*.json"))
    placements: list[dict] = []
    for f in files:
        try:
            _walk_placements(json.loads(f.read_text(encoding="utf-8")), placements)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  draft-runs: unreadable {f.name}: {e}")
    bound: list[dict] = []
    pairs: list[tuple[str | None, UUID | None]] = []
    for p in placements:
        raw = p.get("security_id")
        if not raw:
            continue
        try:
            sid = UUID(str(raw))  # the JSON log stores ids as strings
        except ValueError:
            continue
        bound.append(p)
        pairs.append((p.get("ticker"), sid))
    # Draft-run logs don't record a tenant; the drafts were produced by THIS deployment, so classify under
    # the deployment tenant (the same resolution the workbench writes under).
    findings = coherence.classify_members(conn, pairs, tenant_id=current_tenant_id())

    counts: Counter[str] = Counter()
    cross: dict[str, None] = {}  # ordered de-dup: re-drafts repeat identical rows; the review list
    drift: list[tuple[dict, coherence.CoherenceFinding]] = []  # shows each DISTINCT item once
    for p, f in zip(bound, findings):
        counts[f.kind] += 1
        if f.kind is CoherenceKind.CROSS_COMPANY:
            cross.setdefault(
                f"  DRAFT CROSS_COMPANY: shown {p.get('name')!r} ({p.get('ticker')}) -> bound "
                f"{f.bound_ticker} ({f.bound_name}, CIK {f.bound_cik})"
            )
        elif f.kind is CoherenceKind.LABEL_DRIFT:
            drift.append((p, f))

    # LABEL_DRIFT sub-classification: SEC-file membership first, then the bound-name match.
    in_file: set[str] = set()
    if drift:
        try:
            in_file = {t for _, t, _, _ in sec_tickers.load_all(allow_live=allow_live)}
        except (
            Exception
        ) as e:  # noqa: BLE001 — the audit still reports, just without the file split
            print(f"  draft-runs: SEC file unavailable ({e}); label-drift rows unsplit")
    stale_master = benign_line = 0
    unresolved: dict[str, None] = {}
    for p, f in drift:
        shown = (p.get("ticker") or "").strip().upper()
        if shown in in_file:
            stale_master += 1  # a current listing the master lacks -> populate_master is behind
        elif _norm_name(p.get("name")) and _norm_name(p.get("name")) == _norm_name(f.bound_name):
            benign_line += 1  # same issuer, non-equity/renamed line label (the ETN class)
        else:
            unresolved.setdefault(
                f"  DRAFT UNRESOLVED label: shown {p.get('name')!r} ({p.get('ticker')}) -> bound "
                f"{f.bound_ticker} ({f.bound_name}, CIK {f.bound_cik}) -- foreign-filer (20-F "
                "backlog) or breakage; review"
            )

    for line in (*cross, *unresolved):
        print(line)
    print(
        f"draft-runs: {len(files)} files, {len(bound)} bound placements -- "
        f"{counts[CoherenceKind.OK]} ok / {counts[CoherenceKind.SIBLING]} sibling / "
        f"{counts[CoherenceKind.CROSS_COMPANY]} CROSS-COMPANY "
        f"({len(cross)} distinct) / {counts[CoherenceKind.LABEL_DRIFT]} label-drift "
        f"(= {stale_master} stale-master + {benign_line} same-issuer line + "
        f"{len(unresolved)} distinct UNRESOLVED) / {counts[CoherenceKind.MISSING_ROW]} missing-row"
    )
    return len(cross) + len(unresolved)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Audit shown-vs-bound identity coherence (read-only).")
    p.add_argument(
        "--draft-runs", default=None, help="directory of persisted draft-run JSONs to sweep"
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="allow a live SEC company_tickers fetch for the label-drift split (else cache-first)",
    )
    a = p.parse_args(argv)

    conn = connect()
    needs_human = 0
    try:
        # Sweep 1 — master health (the arbitrary-sibling state).
        gaps = [g for g in master.primary_flag_gaps(conn) if g["flagged_rows"] == 0]
        for g in gaps:
            print(
                f"  MASTER: tenant {g['tenant_id']}: {g['multi_row_ciks']} multi-row CIKs, ZERO "
                "is_primary flags — run `python -m pipeline.populate_master --live`"
            )
        print(f"master: {'OK' if not gaps else f'{len(gaps)} tenant(s) unflagged'}")
        needs_human += len(gaps)

        needs_human += audit_spine(conn)

        if a.draft_runs:
            needs_human += audit_draft_runs(conn, Path(a.draft_runs), allow_live=a.live)
    finally:
        conn.close()

    if needs_human:
        print(f"AUDIT: {needs_human} item(s) need review")
        raise SystemExit(1)
    print("AUDIT: clean")


if __name__ == "__main__":
    main()

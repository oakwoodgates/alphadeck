"""Identity coherence — does a row's SHOWN ticker agree with the security it is BOUND to?

The misbind class (diagnosed 2026-07-12): EFTS joint-filing display mispairing put one company's label on
another company's ``security_id`` (KLAC↔LRCX, SIMO↔MXL — merger counterparties), and the unflagged-primary
master bound arbitrary siblings (ASML→ASMLF, Blaize→its warrant). The parse fix and the primary backfill
kill the *sources*; this module is the STANDING check that a recurrence — any future path that pairs a
label with an id — is LOUD, never silent: the promote write-guard and the read-side audit
(``pipeline.audit_identity``) both classify through here, so shown-vs-bound disagreement has ONE definition.

Display/diagnosis only (#3): a finding is never a fact, never a number, and never mutates anything — the
caller decides (reject, align, or surface for the operator's pick; #10: the system recommends, the operator
decides).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from securities import master


class CoherenceKind(StrEnum):
    OK = "ok"  # shown == bound (or nothing shown / nothing bound to disagree with)
    SIBLING = (
        "sibling"  # same company (CIK), different instrument line (warrant/preferred/F-ordinary)
    )
    CROSS_COMPANY = (
        "cross_company"  # the shown ticker belongs to a DIFFERENT company than the bound row
    )
    LABEL_DRIFT = "label_drift"  # the shown ticker matches NO current master row (rename artifact /
    #   non-equity line / breakage — the audit CLI sub-classifies; MNMD→DFTX is this class)
    UNBOUND = "unbound"  # no security_id — not a coherence question
    MISSING_ROW = (
        "missing_row"  # security_id has no master row under this tenant (exists() territory)
    )


@dataclass(frozen=True)
class CoherenceFinding:
    """One (shown ticker, bound security) verdict. ``bound_*`` carry the master row's identity so a caller
    can say BOTH sides out loud ("shown SIMO — bound MXL 'MAXLINEAR, INC', CIK 0001288469"): a mismatch is
    only actionable when the operator can see who the row actually is."""

    kind: CoherenceKind
    shown_ticker: str | None
    bound_ticker: str | None = None
    bound_name: str | None = None
    bound_cik: str | None = None
    detail: str = ""


def classify_members(
    conn: psycopg.Connection,
    pairs: list[tuple[str | None, UUID | None]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> list[CoherenceFinding]:
    """Classify ``(shown_ticker, security_id)`` pairs against THIS tenant's master, order-preserving.

    Per pair: no id → UNBOUND; id without a master row → MISSING_ROW; no shown ticker, or shown equals the
    bound row's ticker → OK. Otherwise the shown ticker is looked up across the master: any row carrying it
    with the SAME CIK as the bound row → SIBLING (right company, different line — alignable); rows carrying
    it under only OTHER CIKs → CROSS_COMPANY (the misbind class — never silently acceptable); carried by no
    current row at all → LABEL_DRIFT (a label nothing current answers to). Batch reads (two queries),
    read-only, judges nothing it can't show (#6: the finding carries both identities)."""
    sids = [sid for _, sid in pairs if sid is not None]
    secs = master.get_many(conn, sids, tenant_id=tenant_id)
    shown_set = {t.strip().upper() for t, sid in pairs if t and sid is not None}
    ciks_by_ticker: dict[str, set[str]] = {}
    if shown_set:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ticker, cik FROM security_master "
                "WHERE tenant_id = %s AND ticker = ANY(%s) AND cik IS NOT NULL",
                (tenant_id, list(shown_set)),
            )
            for r in cur.fetchall():
                ciks_by_ticker.setdefault(r["ticker"], set()).add(r["cik"])

    out: list[CoherenceFinding] = []
    for shown, sid in pairs:
        shown_norm = (shown or "").strip().upper()
        if sid is None:
            out.append(CoherenceFinding(kind=CoherenceKind.UNBOUND, shown_ticker=shown))
            continue
        row = secs.get(sid)
        if row is None:
            out.append(
                CoherenceFinding(
                    kind=CoherenceKind.MISSING_ROW,
                    shown_ticker=shown,
                    detail="security_id has no master row under this tenant",
                )
            )
            continue
        bound = dict(
            bound_ticker=row.ticker, bound_name=row.name, bound_cik=row.cik, shown_ticker=shown
        )
        if not shown_norm or (row.ticker or "").upper() == shown_norm:
            out.append(CoherenceFinding(kind=CoherenceKind.OK, **bound))
            continue
        shown_ciks = ciks_by_ticker.get(shown_norm, set())
        if row.cik and row.cik in shown_ciks:
            out.append(
                CoherenceFinding(
                    kind=CoherenceKind.SIBLING,
                    **bound,
                    detail=f"same company (CIK {row.cik}), different instrument line",
                )
            )
        elif shown_ciks:
            out.append(
                CoherenceFinding(
                    kind=CoherenceKind.CROSS_COMPANY,
                    **bound,
                    detail=(
                        f"shown ticker {shown_norm} belongs to CIK "
                        f"{'/'.join(sorted(shown_ciks))}, not the bound row's CIK {row.cik}"
                    ),
                )
            )
        else:
            out.append(
                CoherenceFinding(
                    kind=CoherenceKind.LABEL_DRIFT,
                    **bound,
                    detail=f"shown ticker {shown_norm} matches no current master row",
                )
            )
    return out

"""JOINT REPORTS — one trade reported in SEPARATE Form 4s by several reporting persons, counted ONCE at read.

THE DEFECT (found 2026-10-21; operator-approved rule the same day). ``parse_form4`` stores each Table I
transaction ONCE, under the filing's first reporting owner, so one filing never double-counts. But a trade
held through a fund, a trust or a spouse is often reported AGAIN, in a separate filing, by every other person
with a beneficial interest in it: David Singer's own Form 4 re-reports the Maverick funds' INFQ sales that
Maverick's joint filing already reports; Charles Liang reports his wife's SMCI sale "By Spouse"; five Kim-family
filers each report the same Sujoda Investments purchase of AMKR. Stored side by side, the detectors summed the
trade once per filer and counted each filer as an insider (INFQ: "$95.2M across 2 insiders" for $47.6M sold by
one fund family). The filings prove it is ONE trade — and since migration 0054 the row carries the evidence:
``direct_indirect`` ('D' / 'I') and ``shares_owned_after`` (the holding the trade left).

THE RULE (``collapse_joint_reports``; the operator's four decisions of 2026-10-21):

1. A GROUP is the rows on one security's tape identical on (date, code, shares, price, post-trade holding),
   from >= 2 DIFFERENT reporting owners in >= 2 DIFFERENT filings. Across filings only — never two rows of one
   filing (those are separate lots: several funds each selling the same lot size).
2. NEVER TWO DIRECT HOLDERS. A group in which two different owners each report the holding as DIRECT is left
   alone: those are two persons' own positions, however identical — ALAB's co-founders' identical
   sell-to-cover lots. (The 60 such groups on prod stay as they are, true duplicates among them included.)
3. A FINGERPRINT LINKS FILINGS. A post-trade holding that is neither 0 (sold out) nor the traded shares
   (bought from nothing) identifies the position; a group carrying one proves its filings report the SAME
   holdings and LINKS them (union-find over accessions). A group whose holding is 0 or == shares collapses only
   when its filings are already linked — a sold-out balance alone identifies nothing (four of Singer's twelve
   INFQ sales left their fund at 0).
4. THE SURVIVOR. The DIRECT row when the group has one (the holder's own report: a spouse's sale stays the
   spouse's), else — for an all-indirect group — the row of the owner who reported that owner-set's trades
   FIRST (earliest SEC acceptance, then accession), chosen ONCE per owner-set so every group of one joint
   report keeps the SAME filer and the party is counted once. The survivor keeps its own fields (name, role,
   10b5-1 flag, accession) and carries ``joint_reporters`` (every reporter's name, its own first),
   ``joint_accessions`` (every filing that reported it) and ``joint_roles`` (every reporter's role — the
   seniority test reads them, so a trade a senior officer co-reported stays senior: counted once, never
   demoted). The other owners' rows leave the read.

SAFE ON MISSING EVIDENCE. A row without BOTH ``direct_indirect`` ('D'/'I') and ``shares_owned_after`` is never
grouped: it can neither merge nor link. So a row stored before 0054 and not yet repaired
(``pipeline.repair_insider_ownership``) can only stay double-counted — it never collapses wrongly; and an old
replay mirror (exported before the columns existed) reads exactly as before.

PURE and engine-agnostic: no clock, no DB. Numbers are compared as rounded floats (Postgres hands ``Decimal``,
the DuckDB mirror ``float``) and the first-reporter order reads ``accepted`` (else ``recorded_at``), so the
Postgres read and the replay mirror collapse identically (PIT parity). It runs inside
``domain.insider_amendments.ResolutionInputs.resolve`` — AFTER the Form 4/A resolution, on the resolved rows —
which is the one seam every as-of read of ``fact_insider_txn`` passes through (the live PIT's per-security and
basket reads, the Scoreboard overlays, the replay mirror). A read with nothing to collapse is returned
UNCHANGED (the same list), so no call golden can move on a tape without a joint report.

THE WINDOW EDGE (documented, not a defect). A link proven only by a row OLDER than a bounded read's floor is
not seen by that read. The floor (``signals/horizons.py``) sits ``MARGIN_DAYS`` beyond every reader's horizon,
and one filing's transactions sit days apart, so no reader's window can hold a row whose only fingerprint
falls below the floor; the unbounded display read sees everything.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

# The keys a SURVIVOR row carries (not table columns — like ``amends_accession`` on an emitted 4/A row).
JOINT_REPORTERS = "joint_reporters"  # every reporter's name, the survivor's own first
JOINT_ACCESSIONS = "joint_accessions"  # every filing that reported the trade, sorted
JOINT_ROLES = "joint_roles"  # every reporter's role, sorted — the seniority test reads them
JOINT_REPORT = "joint report"

DIRECT, INDIRECT = "D", "I"


def _num(v: object) -> float | None:
    """A number as a rounded float (``Decimal`` on Postgres, ``float`` on the mirror) — ``None`` when absent or
    not finite. Six places: share counts and per-share prices are stated to at most four."""
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(f, 6) if math.isfinite(f) else None


def owner_key(row: Mapping[str, Any]) -> str:
    """One reporting owner: the CIK when the row carries one (leading zeros dropped), else the casefolded name."""
    cik = str(row.get("rpt_owner_cik") or "").strip().lstrip("0")
    if cik:
        return f"cik:{cik}"
    return "name:" + " ".join(str(row.get("insider_name") or "").casefold().split())


def _instant(row: Mapping[str, Any]) -> float:
    """When the filing became public (``accepted``), else when the system recorded it — as a POSIX instant, so
    the two engines' datetime types compare alike. Unknown sorts last."""
    for col in ("accepted", "recorded_at"):
        v = row.get(col)
        if isinstance(v, datetime):
            return v.timestamp()
    return math.inf


def _order(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (_instant(row), str(row.get("accession")), row.get("txn_seq") or 0)


class _Links:
    """Union-find over accessions: two filings are linked once a fingerprint proves they report one holding."""

    def __init__(self) -> None:
        self._p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._p.setdefault(x, x)
        while self._p[x] != x:
            self._p[x] = self._p[self._p[x]]
            x = self._p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._p[max(ra, rb)] = min(ra, rb)  # deterministic root


def collapse_joint_reports(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """PURE — ONE security's resolved insider rows with every joint report counted ONCE (the rule above).

    Kept rows keep their input order; a survivor is a NEW dict (the input rows are never mutated) in its own
    row's place; the other reporters' rows are gone from the read (they stay in ``fact_insider_txn``). A read
    with nothing to collapse is returned unchanged — the same list."""
    # rule 1's groups. A row is grouped only WITH its evidence — 'D'/'I', a numeric post-trade holding, a
    # positive share count; without all three it is never grouped (safe on missing evidence). Runs on every read.
    groups: dict[tuple[Any, ...], list[int]] = {}
    for i, r in enumerate(rows):
        di = r.get("direct_indirect")
        if di != DIRECT and di != INDIRECT:
            continue
        after = _num(r.get("shares_owned_after"))
        if after is None:
            continue
        sh = _num(r.get("shares"))
        if sh is None or sh <= 0:
            continue
        key = (r.get("valid_from"), r.get("txn_code"), sh, _num(r.get("price")), after)
        groups.setdefault(key, []).append(i)
    if not groups:
        return rows if isinstance(rows, list) else list(rows)

    eligible: list[tuple[tuple[Any, ...], list[int]]] = []
    links = (
        _Links()
    )  # order-independent: a component's root is its smallest accession whatever the order
    for key, idx in groups.items():
        if len(idx) < 2:
            continue  # the common case — a trade only one row reports
        owners = {owner_key(rows[i]) for i in idx}
        accessions = sorted({str(rows[i].get("accession")) for i in idx})
        if len(owners) < 2 or len(accessions) < 2:
            continue
        direct_owners = {
            owner_key(rows[i]) for i in idx if rows[i].get("direct_indirect") == DIRECT
        }
        if len(direct_owners) > 1:
            continue  # rule 2 — two persons' own positions, never merged
        eligible.append((key, idx))
        _date, _code, shares, _price, after = key
        if after != 0 and after != shares:  # rule 3 — a fingerprint links the filings
            for a in accessions[1:]:
                links.union(accessions[0], a)

    collapsing = [
        (key, idx)
        for key, idx in eligible
        if len({links.find(str(rows[i].get("accession"))) for i in idx}) == 1
    ]
    if not collapsing:
        return rows if isinstance(rows, list) else list(rows)

    # rule 4 — one representative per owner-set of the all-indirect groups: whoever reported that set's
    # trades first, so every group of one joint report keeps the same filer (the party counted once)
    first: dict[frozenset[str], tuple[tuple[Any, ...], str]] = {}
    for _key, idx in collapsing:
        if any(rows[i].get("direct_indirect") == DIRECT for i in idx):
            continue
        owner_set = frozenset(owner_key(rows[i]) for i in idx)
        for i in idx:
            cand = (_order(rows[i]), owner_key(rows[i]))
            if owner_set not in first or cand < first[owner_set]:
                first[owner_set] = cand

    drop: set[int] = set()
    survivors: dict[int, dict[str, Any]] = {}
    for _key, idx in collapsing:
        directs = [i for i in idx if rows[i].get("direct_indirect") == DIRECT]
        if directs:
            pool = directs
        else:
            rep = first[frozenset(owner_key(rows[i]) for i in idx)][1]
            pool = [i for i in idx if owner_key(rows[i]) == rep]
        s = min(pool, key=lambda i: _order(rows[i]))
        s_owner = owner_key(rows[s])
        gone = [i for i in idx if owner_key(rows[i]) != s_owner]
        drop.update(gone)
        names: list[str] = [str(rows[s].get("insider_name") or "")]
        seen = {s_owner}
        for i in sorted(gone, key=lambda i: str(rows[i].get("insider_name") or "")):
            ok = owner_key(rows[i])
            if ok not in seen:
                seen.add(ok)
                names.append(str(rows[i].get("insider_name") or ""))
        roles = sorted(
            {str(rows[i].get("insider_role")) for i in idx if rows[i].get("insider_role")}
        )
        survivors[s] = {
            **rows[s],
            JOINT_REPORTERS: tuple(names),
            JOINT_ACCESSIONS: tuple(sorted({str(rows[i].get("accession")) for i in idx})),
            JOINT_ROLES: tuple(roles),
        }
    return [survivors.get(i, r) for i, r in enumerate(rows) if i not in drop]


# --- reading a survivor (the detectors, the display rail, the Scoreboard ledger) ---------------------------


def roles_of(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Every role the row speaks for: its own, plus every co-reporter's when it is a joint-report survivor —
    the seniority test reads all of them (a trade a senior officer co-reported stays senior)."""
    own = (row["insider_role"],) if row.get("insider_role") else ()
    return own + tuple(row.get(JOINT_ROLES) or ())


def joint_label(row: Mapping[str, Any]) -> str | None:
    """``"MAVERICK CAPITAL LTD / SINGER DAVID B (joint report)"`` for a survivor, else ``None``."""
    names = row.get(JOINT_REPORTERS)
    if not names:
        return None
    return f"{' / '.join(names)} ({JOINT_REPORT})"


def display_name(row: Mapping[str, Any]) -> str | None:
    """The name a ledger shows for the row: the joint label for a survivor, else the filer's own name."""
    return joint_label(row) or row.get("insider_name")


def joint_labels(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """One label per distinct SET of joint reporters among ``rows`` (a cluster's), in first-seen order — two
    spouses who each survive for their own sale read once ("A / B (joint report)"), not once per order.
    """
    out: list[str] = []
    seen: set[frozenset[str]] = set()
    for r in rows:
        lbl = joint_label(r)
        key = frozenset(r.get(JOINT_REPORTERS) or ())
        if lbl is not None and key not in seen:
            seen.add(key)
            out.append(lbl)
    return out


def joint_accessions(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every filing behind ``rows`` — their own accessions plus every co-reporter's (the work shown, #6)."""
    out = {str(r["accession"]) for r in rows if r.get("accession")}
    for r in rows:
        out.update(str(a) for a in r.get(JOINT_ACCESSIONS) or ())
    return out

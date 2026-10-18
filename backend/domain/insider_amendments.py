"""Form 4/A amendments (R8-A) — the PURE rules: link an amendment to its original, classify what it changed,
and RESOLVE a security's insider rows against the amendments, links and operator decisions a read can see.

WHY. The Form 4 leg matched the form type exactly (``filings_of(subs, "4")``), so a Form 4/A was never
ingested: an amended-away buy kept firing (FLS — a director buy the broker CANCELLED inside the settlement
window stayed a CORE trigger) and a corrected one never did (ATCH — five 4/As recoding ``A`` -> ``P``). Widening
the match alone would be wrong: ``accession`` is in the insider natural key (0037), so a 4/A's rows would land
BESIDE the original's — a clerical amendment double-counts, and a zero-row void removes nothing. So the 4/A is
stored AS FILED in its own table (migration 0050), linked to its original by an append-only link, and every
as-of read of ``fact_insider_txn`` passes through ``resolve_insider_rows`` below.

ONE resolver, shared by BOTH engines (the D3a ``first_knowable_versions`` precedent): the Postgres as-of read
(``db.insider_resolution``) and the DuckDB replay mirror (``replay.pit``) feed it the SAME visible inputs and
it decides — so replay parity holds by construction, and nothing here touches a database, a clock or the
network.

THE DISPOSITIONS (``classify`` / ``link_amendment``), each deterministic over Table I (the non-derivative
rows — what ``fact_insider_txn`` stores) on the fields the detectors read: code, date, shares, price.

==================  ==========================================  =========================================
disposition         when                                        effect (see ``effect_of``)
==================  ==========================================  =========================================
``no_rows``         no Table I rows on either side              none (inert)
``clerical``        Table I identical                           none (inert) — never double-counts
``unlinked``        no stored original found                    none (inert, listed)
``correction``      same row count, every changed row pairs     AUTO-APPLIED: the amendment's rows replace
                    1:1 (same date+code, else date, else           the original's, as of the link's
                    code+shares) — shares / price / code /         ``recorded_at``
                    date corrections
``void``            zero rows, the original has rows            PENDING ratification; while pending it
                                                                SUPPRESSES the original (the operator's
                                                                condition: a cancelled buy never fires)
``duplicates``      the cascade found several originals with    PENDING; while pending it SUPPRESSES the
                    IDENTICAL Table I (filing-agent duplicates  later duplicates and keeps the earliest
                    — TDG)                                      (1->N void)
``partial``         fewer rows, some original (date, code)      PENDING; no provisional effect
                    missing from the amendment
``consolidation``   fewer rows, same (date, code) set (N->1 —   PENDING; no provisional effect
                    BHVN)
``addition``        more rows than the original                 PENDING; no provisional effect
``restatement``     same row count, rows that do not pair 1:1   PENDING; no provisional effect
``ambiguous``       several candidate originals that differ     NONE — it cannot target a specific
                                                                original; surfaced loudly with the
                                                                candidates until the operator links one
==================  ==========================================  =========================================

Why partial / consolidation / addition / restatement carry NO provisional effect while a void does: the
operator's condition is about CANCELLATIONS, and only a zero-row amendment (or a named duplicate) states
one. A partial restatement usually re-reports just the corrected row (MEASURED on the 85 non-zero partials
of 2026-10-17: every one of the seven that omits an original ``P`` row says why in its own text — a share
count typo, a holding-column correction, an ownership-nature fix — and none is a cancellation), so
suppressing the rows it omits would silence real buys while it waits.

THE OPERATOR'S DECISIONS (append-only, ``insider_amendment_decision``): ``apply`` (ratify the amendment —
for a void/duplicates: keep the suppression; otherwise its rows replace the original's), ``keep`` (the
original stands; the amendment changes nothing — the AZTA case, a zero-row 4/A that only attached a power
of attorney), ``link`` (an AMBIGUOUS amendment: the operator picks the original; the disposition against it
is computed at decision time and STORED on the decision, so a read never recomputes it from a windowed row
set), and ``revoke`` (the Undo of any of them). A decision counts at a read iff it was recorded at/before the
read's ``known_at`` and no revoke recorded at/before ``known_at`` names it — so a replay pinned before a
ratification sees the pending state it actually held.

PIT. Every input is gated on ITS OWN ``recorded_at <= known_at`` by the caller (the amendment, the link, each
decision); ``accepted`` (the SEC acceptance instant) is display + ordering only, never a gate. A read pinned
before the link's ``recorded_at`` therefore sees the original exactly as it was stored.

THE OTHER-ISSUER SCREEN. A Form 4 on a member's tape whose ISSUER (from the XML) is not the member is not that
security's insider flow — the member was the REPORTING OWNER of another company's stock (Novartis selling
Aileron, Honeywell buying Garrett Motion). New ones are no longer stored (the ingest screens them); the rows
already stored are KEPT and screened here, at read (the ADR keep-and-screen precedent). ``own_ciks`` is every
CIK the security has been known by (the master's plus the T7 CIK-change log), so a successor-CIK re-point
never screens the predecessor's own history. An empty ``own_ciks`` or a NULL ``issuer_cik`` keeps the row (#9).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# --- the vocabulary -----------------------------------------------------------------------------------

NO_ROWS = "no_rows"
CLERICAL = "clerical"
UNLINKED = "unlinked"
CORRECTION = "correction"
VOID = "void"
DUPLICATES = "duplicates"
PARTIAL = "partial"
CONSOLIDATION = "consolidation"
ADDITION = "addition"
RESTATEMENT = "restatement"
AMBIGUOUS = "ambiguous"

DISPOSITIONS: tuple[str, ...] = (
    NO_ROWS,
    CLERICAL,
    UNLINKED,
    CORRECTION,
    VOID,
    DUPLICATES,
    PARTIAL,
    CONSOLIDATION,
    ADDITION,
    RESTATEMENT,
    AMBIGUOUS,
)
# inert: nothing to apply and nothing to ratify (listed quietly)
INERT_DISPOSITIONS = frozenset({NO_ROWS, CLERICAL, UNLINKED})
# applied without the operator — a clean 1:1 correction (the operator may still `keep` the original)
AUTO_DISPOSITIONS = frozenset({CORRECTION})
# routed to the operator; a CANCELLATION suppresses its target while it is pending
SUPPRESS_WHILE_PENDING = frozenset({VOID, DUPLICATES})
# routed to the operator; nothing changes until the operator applies it
ROUTED_NO_PROVISIONAL = frozenset({PARTIAL, CONSOLIDATION, ADDITION, RESTATEMENT})
ROUTED_DISPOSITIONS = SUPPRESS_WHILE_PENDING | ROUTED_NO_PROVISIONAL

# the link cascade's methods
METHOD_ORIG_DATE_OWNER = "orig_date_owner"  # issuer (the member) + owner + dateOfOriginalSubmission
METHOD_OWNER_TXN_DATE = "owner_txn_date"  # owner + transaction-date overlap (the fallback)
METHOD_NONE = "none"  # no candidate (unlinked / no rows)
METHODS: tuple[str, ...] = (METHOD_ORIG_DATE_OWNER, METHOD_OWNER_TXN_DATE, METHOD_NONE)

# a link's status as the system classified it (the EFFECTIVE status also reads the decisions)
STATUS_APPLIED = "applied"
STATUS_PENDING = "pending"
STATUS_INERT = "inert"
STATUSES: tuple[str, ...] = (STATUS_APPLIED, STATUS_PENDING, STATUS_INERT)

# the operator's decisions
ACTION_APPLY = "apply"
ACTION_KEEP = "keep"
ACTION_LINK = "link"
ACTION_REVOKE = "revoke"
ACTIONS: tuple[str, ...] = (ACTION_APPLY, ACTION_KEEP, ACTION_LINK, ACTION_REVOKE)
VERDICTS = frozenset({ACTION_APPLY, ACTION_KEEP})

# the key an EMITTED (amended) row carries: the original accession it supersedes (provenance, #6)
AMENDS_ACCESSION = "amends_accession"

# A page names at most this many amendments (the count is always whole; the rest are on the Admin panel and in
# the run artifact): the first pass after deploy finds the whole two-year backlog at once (MEASURED 2026-10-17:
# ~180 owed a decision), and a page listing 180 carries less than one naming the few that move a call — the
# nightly scan orders them buy-touching first, then suppressing.
PAGE_NAMED = 8


def page_text(labels: Sequence[str]) -> str:
    """A page's list of amendment labels: the first ``PAGE_NAMED``, then ``(+N more on the Admin panel)``."""
    shown = ", ".join(labels[:PAGE_NAMED])
    more = len(labels) - PAGE_NAMED
    return f"{shown} (+{more} more on the Admin panel)" if more > 0 else shown


# ``fact_insider_txn``'s columns, in table order — the shape every resolved row has, so an emitted row reads
# exactly like a stored one to every consumer. ``tests/db/test_insider_amendments.py`` pins this against the
# live table, so a new column fails there, never as a silently missing key.
INSIDER_TXN_COLUMNS: tuple[str, ...] = (
    "id",
    "tenant_id",
    "security_id",
    "insider_name",
    "insider_role",
    "txn_code",
    "shares",
    "price",
    "usd",
    "accession",
    "valid_from",
    "valid_to",
    "recorded_at",
    "supersedes",
    "txn_seq",
    "aff_10b5_1",
    "issuer_cik",
    "issuer_name",
    "rpt_owner_cik",
    "accepted",
    "security_title",
    "issuer_foreign_symbol",
)


def status_for(disposition: str) -> str:
    """The status the SYSTEM gives a link of ``disposition`` (before any decision)."""
    if disposition in AUTO_DISPOSITIONS:
        return STATUS_APPLIED
    if disposition in ROUTED_DISPOSITIONS or disposition == AMBIGUOUS:
        return STATUS_PENDING
    return STATUS_INERT


def norm_cik(raw: object) -> str | None:
    """'0001260221' -> '1260221'; blank/None -> None (identity comparison only)."""
    if raw is None:
        return None
    s = str(raw).strip().lstrip("0")
    return s or None


def _num(v: object) -> float | None:
    """A Table I number as float — Postgres numeric (Decimal), the mirror (float) and a parsed filing
    (float) compare equal for the same filed text (no tolerance: an exact decimal string survives the trip).
    """
    if v is None or v == "":
        return None
    return float(v)  # type: ignore[arg-type]


def _as_date(v: object) -> date | None:
    """A row date: a ``date`` passes, an ISO string (the stored JSON) parses; a datetime keeps its date."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _row_date(row: Mapping[str, Any]) -> date | None:
    """A stored row's date is ``valid_from``; an amendment row (as filed) carries ``txn_date``."""
    return _as_date(row.get("valid_from", row.get("txn_date")))


def signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """The Table I identity of one row on the fields the detectors read: (code, date, shares, price)."""
    return (row.get("txn_code"), _row_date(row), _num(row.get("shares")), _num(row.get("price")))


def _pairs_one_to_one(
    amend: Sequence[Mapping[str, Any]], orig: Sequence[Mapping[str, Any]]
) -> bool:
    """Do the rows that DIFFER pair 1:1 — each amended row to one original row, by same (date, code), else
    same date, else same (code, shares) — with none left over? (The 2026-10-17 classification's rule.)
    """
    sa = Counter(signature(r) for r in amend)
    so = Counter(signature(r) for r in orig)
    only_a = list((sa - so).elements())
    only_o = list((so - sa).elements())
    if len(only_a) != len(only_o):
        return False
    used: set[int] = set()
    for a in only_a:
        match = None
        for rule in (
            lambda b: b[1] == a[1] and b[0] == a[0],  # same date + code
            lambda b: b[1] == a[1],  # same date
            lambda b: b[0] == a[0] and b[2] == a[2],  # same code + shares
        ):
            match = next((j for j, b in enumerate(only_o) if j not in used and rule(b)), None)
            if match is not None:
                break
        if match is None:
            return False
        used.add(match)
    return len(used) == len(only_o)


def classify(
    amend_rows: Sequence[Mapping[str, Any]], original_rows: Sequence[Mapping[str, Any]]
) -> str:
    """What one amendment changed against ONE linked original's stored rows (see the module table)."""
    if not amend_rows and not original_rows:
        return NO_ROWS
    if not amend_rows:
        return VOID
    if not original_rows:
        return ADDITION
    if Counter(signature(r) for r in amend_rows) == Counter(signature(r) for r in original_rows):
        return CLERICAL
    if len(amend_rows) == len(original_rows):
        return CORRECTION if _pairs_one_to_one(amend_rows, original_rows) else RESTATEMENT
    if len(amend_rows) > len(original_rows):
        return ADDITION
    keys_a = {(r.get("txn_code"), _row_date(r)) for r in amend_rows}
    keys_o = {(r.get("txn_code"), _row_date(r)) for r in original_rows}
    return CONSOLIDATION if keys_a == keys_o else PARTIAL


# --- the link cascade -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class AmendmentFacts:
    """What the linker needs from one 4/A (as filed). ``owners`` is every reporting owner ``(cik, name)``."""

    accession: str
    owners: tuple[tuple[str | None, str | None], ...]
    date_of_original_submission: date | None
    filed: date | None
    accepted: datetime | None
    rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class LinkResult:
    """One amendment's link: which original(s), how found, what it changed, and its system status."""

    method: str
    disposition: str
    status: str
    original_accessions: tuple[str, ...] = ()
    voided_accessions: tuple[str, ...] = ()
    candidate_accessions: tuple[str, ...] = ()
    owner_ciks: tuple[str, ...] = ()
    why: str = ""


def _owner_keys(owners: Iterable[tuple[str | None, str | None]]) -> tuple[set[str], set[str]]:
    ciks = {c for c in (norm_cik(o[0]) for o in owners) if c}
    names = {(o[1] or "").strip().casefold() for o in owners if (o[1] or "").strip()}
    return ciks, names


def _owned_by(rows: Sequence[Mapping[str, Any]], ciks: set[str], names: set[str]) -> bool:
    for r in rows:
        if norm_cik(r.get("rpt_owner_cik")) in ciks:
            return True
        if (r.get("insider_name") or "").strip().casefold() in names:
            return True
    return False


def _earliest(accessions: Iterable[str], stored: Mapping[str, Sequence[Mapping[str, Any]]]) -> str:
    """The earliest filing among duplicates: by its SEC acceptance (any row's), then the accession."""

    def key(acc: str) -> tuple[int, Any, str]:
        accepted = next((r.get("accepted") for r in stored[acc] if r.get("accepted")), None)
        return (0, accepted, acc) if accepted is not None else (1, None, acc)

    return sorted(accessions, key=key)[0]


def link_amendment(
    amendment: AmendmentFacts,
    stored: Mapping[str, Sequence[Mapping[str, Any]]],
    filed_by_accession: Mapping[str, date],
) -> LinkResult:
    """PURE — find the 4/A's original among the security's STORED Form 4 rows and classify the change.

    ``stored`` maps each stored Form 4 accession of this security to its rows (latest versions, every row);
    ``filed_by_accession`` maps a Form 4 accession to its EDGAR filing date (the submissions index — what
    ``dateOfOriginalSubmission`` names; acceptance after 17:30 ET files the next business day, so the
    acceptance instant is not a substitute).

    The cascade (MEASURED 2026-10-17 on 553 4/As: 422 linked uniquely by step 1, 18 more by step 2, 4
    ambiguous): (1) a stored accession FILED on ``dateOfOriginalSubmission`` whose rows belong to one of the
    amendment's reporting owners — exactly one links; several with IDENTICAL Table I are filing-agent
    DUPLICATES (keep the earliest, void the rest — TDG); several that differ are AMBIGUOUS. (2) Else, an
    accession filed on/before the amendment, of the same owner, sharing transaction dates with it — the one
    with the most shared rows links; a tie is AMBIGUOUS. (3) Else UNLINKED (or NO_ROWS when the amendment
    has no Table I rows: nothing to apply either way)."""
    ciks, names = _owner_keys(amendment.owners)
    owner_ciks = tuple(sorted(ciks))
    amend_rows = list(amendment.rows)
    pool = {acc: rows for acc, rows in stored.items() if acc != amendment.accession and rows}

    def linked(method: str, original: str, why: str) -> LinkResult:
        disp = classify(amend_rows, pool[original])
        return LinkResult(
            method=method,
            disposition=disp,
            status=status_for(disp),
            original_accessions=(original,),
            candidate_accessions=(original,),
            owner_ciks=owner_ciks,
            why=why,
        )

    orig_date = amendment.date_of_original_submission
    if orig_date is not None:
        step1 = sorted(
            acc
            for acc, rows in pool.items()
            if filed_by_accession.get(acc) == orig_date and _owned_by(rows, ciks, names)
        )
        if len(step1) == 1:
            return linked(
                METHOD_ORIG_DATE_OWNER,
                step1[0],
                f"the owner's only Form 4 filed {orig_date.isoformat()} (dateOfOriginalSubmission)",
            )
        if len(step1) > 1:
            sigs = {acc: Counter(signature(r) for r in pool[acc]) for acc in step1}
            first = sigs[step1[0]]
            if all(s == first for s in sigs.values()):
                keep = _earliest(step1, pool)
                voided = tuple(a for a in step1 if a != keep)
                return LinkResult(
                    method=METHOD_ORIG_DATE_OWNER,
                    disposition=DUPLICATES,
                    status=status_for(DUPLICATES),
                    original_accessions=(keep,),
                    voided_accessions=voided,
                    candidate_accessions=tuple(step1),
                    owner_ciks=owner_ciks,
                    why=(
                        f"{len(step1)} Form 4s by the owner filed {orig_date.isoformat()} carry IDENTICAL "
                        f"Table I rows — the earliest ({keep}) stands, {len(voided)} duplicate(s) voided"
                    ),
                )
            return LinkResult(
                method=METHOD_ORIG_DATE_OWNER,
                disposition=AMBIGUOUS,
                status=status_for(AMBIGUOUS),
                candidate_accessions=tuple(step1),
                owner_ciks=owner_ciks,
                why=(
                    f"{len(step1)} different Form 4s by the owner were filed {orig_date.isoformat()} — "
                    "which one this amends is the operator's call"
                ),
            )

    dates = {d for d in (_row_date(r) for r in amend_rows) if d is not None}
    if dates:
        overlap: dict[str, int] = {}
        for acc, rows in pool.items():
            filed = filed_by_accession.get(acc)
            if filed is not None and amendment.filed is not None and filed > amendment.filed:
                continue  # filed after the amendment — it cannot be what the amendment amends
            if not _owned_by(rows, ciks, names):
                continue
            n = sum(1 for r in rows if _row_date(r) in dates)
            if n:
                overlap[acc] = n
        if overlap:
            best = max(overlap.values())
            top = sorted(acc for acc, n in overlap.items() if n == best)
            if len(top) == 1:
                return linked(
                    METHOD_OWNER_TXN_DATE,
                    top[0],
                    f"no Form 4 of the owner on the original date; {best} of its rows share the "
                    "amendment's transaction dates",
                )
            return LinkResult(
                method=METHOD_OWNER_TXN_DATE,
                disposition=AMBIGUOUS,
                status=status_for(AMBIGUOUS),
                candidate_accessions=tuple(top),
                owner_ciks=owner_ciks,
                why=f"{len(top)} Form 4s of the owner tie on shared transaction dates ({best} rows each)",
            )

    disp = NO_ROWS if not amend_rows else UNLINKED
    return LinkResult(
        method=METHOD_NONE,
        disposition=disp,
        status=status_for(disp),
        owner_ciks=owner_ciks,
        why=(
            "no Table I rows — nothing to apply"
            if disp == NO_ROWS
            else "no stored Form 4 of the owner matches (its original predates the stored tape, or stored "
            "no Table I rows)"
        ),
    )


def link_to_candidate(
    amend_rows: Sequence[Mapping[str, Any]],
    target: str,
    target_rows: Sequence[Mapping[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    """The operator's ``link`` of an AMBIGUOUS amendment to ``target``: ``(disposition, originals)`` computed
    against the target's stored rows — stored on the decision, so the read applies it without recomputing.
    """
    return classify(amend_rows, target_rows), (target,)


# --- the effective state at one read -------------------------------------------------------------------


def _ver(row: Mapping[str, Any]) -> tuple[Any, str]:
    """Version order: ``recorded_at`` then ``id`` as text (the mirror stores uuids as VARCHAR; canonical
    lowercase hex sorts like Postgres's uuid order — the ``first_knowable_versions`` tiebreak)."""
    return (row["recorded_at"], str(row["id"]))


def _list(v: object) -> tuple[str, ...]:
    if not v:
        return ()
    return tuple(str(x) for x in v)  # type: ignore[union-attr]


@dataclass(frozen=True)
class Effective:
    """One amendment's state at a read: the disposition and targets in force, the operator's verdict, and
    what it does to the rows. ``status`` is the EFFECTIVE status: ``applied`` / ``pending`` / ``kept`` /
    ``inert``."""

    accession: str
    disposition: str
    method: str
    original_accessions: tuple[str, ...]
    voided_accessions: tuple[str, ...]
    candidate_accessions: tuple[str, ...]
    status: str
    verdict: str | None = None
    verdict_id: Any = None
    link_decision_id: Any = None
    suppress: tuple[str, ...] = ()
    emit_for: tuple[
        str, ...
    ] = ()  # the originals whose rows the amendment's rows replace (non-empty = emit)


def standing_decisions(
    decisions: Iterable[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]]:
    """``amendment accession -> (standing link decision, standing verdict)`` over the decisions a read can
    see: a decision stands iff no visible ``revoke`` names it; the newest standing one of each kind wins.
    """
    decisions = list(decisions)
    revoked = {
        str(d["revokes"]) for d in decisions if d["action"] == ACTION_REVOKE and d.get("revokes")
    }
    out: dict[str, list[Mapping[str, Any] | None]] = {}
    for d in sorted(decisions, key=_ver):
        if d["action"] == ACTION_REVOKE or str(d["id"]) in revoked:
            continue
        slot = out.setdefault(str(d["amendment_accession"]), [None, None])
        if d["action"] == ACTION_LINK:
            slot[0] = d
        elif d["action"] in VERDICTS:
            slot[1] = d
    return {k: (v[0], v[1]) for k, v in out.items()}


def effect_of(
    link: Mapping[str, Any],
    link_decision: Mapping[str, Any] | None,
    verdict: Mapping[str, Any] | None,
) -> Effective:
    """PURE — the effective state of one amendment from its newest visible system link, its standing
    operator link (an ambiguous amendment's pick) and its standing verdict. See the module table."""
    src = link_decision if link_decision is not None else link
    disp = str(src["disposition"])
    originals = _list(src.get("original_accessions"))
    voided = _list(src.get("voided_accessions"))
    v = verdict["action"] if verdict is not None else None
    suppress: tuple[str, ...] = ()
    emit_for: tuple[str, ...] = ()
    if disp in INERT_DISPOSITIONS or (disp == AMBIGUOUS and link_decision is None):
        status = STATUS_INERT if disp != AMBIGUOUS else STATUS_PENDING
        v = None  # nothing to ratify: a stray verdict changes nothing
    elif disp in AUTO_DISPOSITIONS:
        if v == ACTION_KEEP:
            status = "kept"
        else:
            status = STATUS_APPLIED
            suppress, emit_for = originals, originals
    elif disp in SUPPRESS_WHILE_PENDING:
        targets = voided if disp == DUPLICATES else originals
        if v == ACTION_KEEP:
            status = "kept"
        else:
            status = STATUS_APPLIED if v == ACTION_APPLY else STATUS_PENDING
            suppress = targets  # pending OR ratified: the cancellation holds
    elif disp in ROUTED_NO_PROVISIONAL:
        if v == ACTION_APPLY:
            status = STATUS_APPLIED
            suppress, emit_for = originals, originals
        else:
            status = "kept" if v == ACTION_KEEP else STATUS_PENDING
    else:  # an unknown disposition (a newer writer) — inert, never a guess
        status = STATUS_INERT
        v = None
    return Effective(
        accession=str(link["amendment_accession"]),
        disposition=disp,
        method="operator" if link_decision is not None else str(link["method"]),
        original_accessions=originals,
        voided_accessions=voided,
        candidate_accessions=_list(link.get("candidate_accessions")),
        status=status,
        verdict=v,
        verdict_id=verdict["id"] if verdict is not None and v is not None else None,
        link_decision_id=link_decision["id"] if link_decision is not None else None,
        suppress=suppress,
        emit_for=emit_for,
    )


def latest_by_accession(
    rows: Iterable[Mapping[str, Any]], key: str
) -> dict[str, Mapping[str, Any]]:
    """The newest version (``recorded_at``, ``id``) per ``row[key]`` — an append-only table's current row."""
    out: dict[str, Mapping[str, Any]] = {}
    for r in sorted(rows, key=_ver):
        out[str(r[key])] = r
    return out


def effective_states(
    links: Iterable[Mapping[str, Any]], decisions: Iterable[Mapping[str, Any]]
) -> list[Effective]:
    """Every linked amendment's effective state over the visible links + decisions (one security or many —
    the caller scopes). Ordered by amendment accession, so the output is deterministic on both engines.
    """
    latest = latest_by_accession(links, "amendment_accession")
    standing = standing_decisions(decisions)
    out = []
    for acc in sorted(latest):
        link_d, verdict = standing.get(acc, (None, None))
        out.append(effect_of(latest[acc], link_d, verdict))
    return out


# --- the resolver ----------------------------------------------------------------------------------------


@dataclass
class _Emission:
    order: tuple[Any, ...]
    amendment: Mapping[str, Any]
    original: str


def emitted_row(
    amendment: Mapping[str, Any], row: Mapping[str, Any], original: str
) -> dict[str, Any]:
    """One amended row in ``fact_insider_txn``'s shape: the 4/A's own values and accession (the filing that
    states them — the provenance a trigger cites), its acceptance, the amendment record's ``recorded_at``
    (when THIS system held it), plus ``amends_accession`` = the original it supersedes."""
    out: dict[str, Any] = {c: None for c in INSIDER_TXN_COLUMNS}
    out.update(
        {
            "id": amendment["id"],
            "tenant_id": amendment["tenant_id"],
            "security_id": amendment["security_id"],
            "insider_name": row.get("insider_name"),
            "insider_role": row.get("insider_role"),
            "txn_code": row.get("txn_code"),
            "shares": row.get("shares"),
            "price": row.get("price"),
            "usd": row.get("usd"),
            "accession": amendment["accession"],
            "valid_from": _row_date(row),
            "recorded_at": amendment["recorded_at"],
            "txn_seq": row.get("txn_seq"),
            "aff_10b5_1": row.get("aff_10b5_1"),
            "issuer_cik": row.get("issuer_cik"),
            "issuer_name": row.get("issuer_name"),
            "rpt_owner_cik": row.get("rpt_owner_cik"),
            "accepted": amendment.get("accepted"),
            "security_title": row.get("security_title"),
            "issuer_foreign_symbol": row.get("issuer_foreign_symbol"),
        }
    )
    out[AMENDS_ACCESSION] = original
    return out


def is_other_issuer(row: Mapping[str, Any], own_ciks: frozenset[str]) -> bool:
    """A row whose ISSUER is not this security (the member was the reporting owner of another company's
    stock). Keep-when-unknown (#9): no own CIK on file, or no issuer CIK on the row, keeps it."""
    if not own_ciks:
        return False
    issuer = norm_cik(row.get("issuer_cik"))
    return issuer is not None and issuer not in own_ciks


def resolve_insider_rows(
    rows: Sequence[dict[str, Any]],
    *,
    amendments: Iterable[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    own_ciks: Iterable[str] = (),
    asof: date,
    lower: date | None = None,
) -> list[dict[str, Any]]:
    """PURE — ONE security's as-of insider rows, resolved against what the read can see.

    ``rows`` are the as-of ``fact_insider_txn`` rows (the latest version per natural key, already gated on
    ``valid_from <= asof`` / ``recorded_at <= known_at``, and on the event-time floor when the caller applied
    one); ``amendments`` / ``links`` / ``decisions`` are every version of those tables for this security with
    ``recorded_at <= known_at`` (the caller's gate — the resolver picks the newest itself); ``own_ciks`` every
    CIK the security has been known by; ``lower`` the floor the caller applied to ``rows``, applied here to the
    emitted rows too (``asof`` likewise), so a corrected date outside the window stays outside it.

    1. Each amendment's effective state (``effective_states``) — the original accessions it SUPPRESSES and
       the ones whose rows it REPLACES.
    2. A suppressed accession's rows leave the read; for an original several applied amendments replace,
       only the NEWEST statement (by SEC acceptance, then accession) emits — one statement per original.
    3. The other-issuer screen drops a row whose issuer is not this security.

    The kept rows keep their input ORDER (the engines' own natural-key order), and the emitted rows follow,
    ordered by amendment accession then ``txn_seq`` — so a read with nothing to resolve is returned
    UNCHANGED (the same list), and the call goldens cannot move on a security no amendment touches.
    """
    own = frozenset(c for c in (norm_cik(x) for x in own_ciks) if c)
    links = list(links)
    decisions = list(decisions)
    screen = bool(own) and any(is_other_issuer(r, own) for r in rows)
    if not links and not screen:
        return rows if isinstance(rows, list) else list(rows)
    states = effective_states(links, decisions)
    by_acc = latest_by_accession(amendments, "accession")
    suppressed: set[str] = set()
    winners: dict[str, _Emission] = {}
    for st in states:
        suppressed.update(st.suppress)
        am = by_acc.get(st.accession)
        if am is None or not st.emit_for:
            continue  # nothing to emit (or the record is not visible: never invent rows)
        accepted = am.get("accepted")
        order = (accepted if accepted is not None else am["recorded_at"], st.accession)
        for original in st.emit_for:
            cur = winners.get(original)
            if cur is None or order > cur.order:
                winners[original] = _Emission(order, am, original)
    if not suppressed and not winners and not screen:
        return rows if isinstance(rows, list) else list(rows)
    kept = [
        r
        for r in rows
        if str(r.get("accession")) not in suppressed and not (screen and is_other_issuer(r, own))
    ]
    emitted: list[dict[str, Any]] = []
    for original in sorted(winners, key=lambda o: (winners[o].order[1], o)):
        em = winners[original]
        for x in sorted(em.amendment.get("rows") or [], key=lambda x: x.get("txn_seq") or 0):
            d = _row_date(x)
            if d is None or d > asof or (lower is not None and d < lower):
                continue
            out = emitted_row(em.amendment, x, original)
            if not is_other_issuer(out, own):
                emitted.append(out)
    # one amendment that replaces TWO originals emits once (dedup on the amended row's own identity)
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for e in emitted:
        k = (e["accession"], e["txn_seq"])
        if k in seen:
            continue
        seen.add(k)
        unique.append(e)
    return kept + unique


@dataclass
class ResolutionInputs:
    """Every resolver input for a SET of securities, grouped per security id (as the engine renders it)."""

    amendments: dict[Any, list[Mapping[str, Any]]] = field(default_factory=dict)
    links: dict[Any, list[Mapping[str, Any]]] = field(default_factory=dict)
    decisions: dict[Any, list[Mapping[str, Any]]] = field(default_factory=dict)
    own_ciks: dict[Any, set[str]] = field(default_factory=dict)

    def resolve(
        self, sid: Any, rows: Sequence[dict[str, Any]], *, asof: date, lower: date | None
    ) -> list[dict[str, Any]]:
        key = str(sid)
        return resolve_insider_rows(
            rows,
            amendments=self.amendments.get(key, ()),
            links=self.links.get(key, ()),
            decisions=self.decisions.get(key, ()),
            own_ciks=self.own_ciks.get(key, ()),
            asof=asof,
            lower=lower,
        )


def group_inputs(
    amendments: Iterable[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    ciks: Iterable[tuple[Any, Any]],
) -> ResolutionInputs:
    """Group flat engine rows by ``str(security_id)`` (the one key both engines agree on)."""
    out = ResolutionInputs()
    for name, src in (("amendments", amendments), ("links", links), ("decisions", decisions)):
        bucket: dict[Any, list[Mapping[str, Any]]] = getattr(out, name)
        for r in src:
            bucket.setdefault(str(r["security_id"]), []).append(r)
    for sid, cik in ciks:
        c = norm_cik(cik)
        if c:
            out.own_ciks.setdefault(str(sid), set()).add(c)
    return out

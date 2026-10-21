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
``clerical``        Table I identical and no signal row         none (inert) — never double-counts
                    changes in any other field
``field_change``    Table I identical, but a field a call       APPLIED BY THE SYSTEM (R8-B): the
                    signal reads changed on a signal row (the   amendment's rows replace — FRHC's CFO ->
                    owner's role or name, the 10b5-1 flag)      "Special Advisor to CEO"
``unlinked``        no stored original found                    none (inert, listed)
``correction``      same row count, every changed row pairs     APPLIED: the amendment's rows replace the
                    1:1 (same date+code, else date, else           original's, as of the link's
                    code+shares) — shares / price / code /         ``recorded_at`` (with or without the
                    date corrections                               system's decision row)
``void``            zero rows, the original has rows            APPLIED BY THE SYSTEM: SUPPRESSES the
                                                                original (also while pending — the
                                                                operator's condition)
``duplicates``      the cascade found several originals with    APPLIED BY THE SYSTEM: SUPPRESSES the
                    IDENTICAL Table I (filing-agent duplicates  later duplicates, keeps the earliest
                    — TDG)                                      (1->N void; also while pending)
``partial``         fewer rows, some original (date, code)      APPLIED BY THE SYSTEM: rows replace (no
                    missing from the amendment                  provisional effect while pending)
``consolidation``   fewer rows, same (date, code) set (N->1 —   APPLIED BY THE SYSTEM (as partial)
                    BHVN)
``addition``        more rows than the original                 APPLIED BY THE SYSTEM (as partial)
``restatement``     same row count, rows that do not pair 1:1   APPLIED BY THE SYSTEM (as partial)
``ambiguous``       several candidate originals that differ     NONE — it cannot target a specific
                                                                original; OWED the operator when it
                                                                touches a signal row inside the review
                                                                horizon, else "unresolved — historical"
==================  ==========================================  =========================================

"Applied by the system" = the effect the operator's ``apply`` verdict always had (``effect_of`` is unchanged by
R8-B): the system writes that verdict, under its own basis, instead of waiting for the operator.

THE FILER'S OWN CORRECTION IS APPLIED BY DEFAULT (R8-B, operator decision 2026-10-20). Every amendment the
linker resolves to a DEFINITE original — a correction, void, duplicates set, partial, consolidation, addition,
restatement or field change (``RESOLVED_DISPOSITIONS``) — is APPLIED BY THE SYSTEM: an append-only ``apply``
decision whose basis names why (``system_basis``): ``system:no-signal-effect`` when its effect changes no row a
call signal reads (the 2026-10-18 rule, kept), else ``system:filer-correction``. A 4/A is the insider's OWN
legal correction of their own filing, filed under penalty — deterministic data, not a model's recommendation —
so applying it is data handling, never a decision taken from the operator (#3 / #10 untouched), and every one
keeps its exact Undo: the operator's ``keep`` ("keep the original") stands OVER a system application without
revoking it, and undoing that keep restores the system's application. The ingest records the decision in the
SAME transaction as the link (one ``recorded_at``: there is never a pending window for a new 4/A); the
amendments a pre-R8-B ingest left pending are applied by the idempotent nightly SWEEP (``sweep_basis`` —
``repositories.insider_amendment_repo.sweep``), each decision stamped ``recorded_at = now``, so a replay pinned
before the sweep still reads the state the system held then (pending; a void suppressing) — no rewrite of the
past (#1). The sweep acts only on an amendment with NO decision at all: anything the operator did (a verdict,
a link, an Undo) stays the operator's.

ONLY AN AMBIGUOUS LINK IS OWED THE OPERATOR (``review_bucket``) — several differing candidate originals, so
the system cannot know which filing the amendment amends — and only when it MATTERS: it would change a row a
call signal reads (the content test below) AND one of those rows is dated inside the REVIEW HORIZON
(``within_review_horizon``; the dial ``insider_amendment_review_horizon_days``, derived by default as the
two-year backtest window plus the longest detector read horizon — ``signals.horizons
.insider_amendment_review_horizon``; 940 days at today's config). An ambiguous amendment outside that is
"unresolved — historical": no call reads those rows, the originals stand, it never pages, and the operator can
still link it. MEASURED 2026-10-20 (prod, read-only, asof 2026-10-20): of 43 ambiguous, 4 are owed (CVNA, FIS,
HROW, SKYT) and 39 historical.

THE SIGNAL-EFFECT TEST (operator-approved 2026-10-18; since R8-B it picks the system's BASIS and the ambiguous
owed test, never whether a resolvable amendment applies). Decided by CONTENT: the "signal rows" are the rows
whose transaction code a registered call detector reads (``signal_codes`` — derived from each detector's
declared ``txn_codes``, ``signals.horizons.insider_signal_codes``; today ``P`` and ``S``), compared on code,
date, shares, price, the 10b5-1 flag (as the screens read it — only an explicit ``True`` is planned) AND the
reporting owner's role and name (seniority reads the role).

What the PENDING state still means (a pre-R8-B link not yet swept, an operator-linked amendment awaiting the
operator's verdict, or one the operator took back with an Undo): a void / duplicates SUPPRESSES its target
meanwhile (the operator's condition — a canceled buy never fires); a partial / consolidation / addition /
restatement / field change carries NO provisional effect (MEASURED on the 85 non-zero partials of 2026-10-17:
every one of the seven that omits an original ``P`` row says why in its own text and none is a cancellation),
so suppressing the rows it omits would silence real buys while it waits.

THE OPERATOR'S DECISIONS (append-only, ``insider_amendment_decision``): ``apply`` (ratify the amendment —
for a void/duplicates: keep the suppression; otherwise its rows replace the original's), ``keep`` (the
original stands; the amendment changes nothing — the AZTA case, a zero-row 4/A that only attached a power
of attorney), ``link`` (an AMBIGUOUS amendment: the operator picks the original; the disposition against it
is computed at decision time and STORED on the decision, so a read never recomputes it from a windowed row
set), and ``revoke`` (the Undo of any of them). A decision counts at a read iff it was recorded at/before the
read's ``known_at`` and no revoke recorded at/before ``known_at`` names it — so a replay pinned before a
ratification sees the pending state it actually held. The NEWEST standing verdict wins, which is how the
operator's ``keep`` overrules a standing SYSTEM ``apply`` (R8-B) without erasing it: revoke the keep and the
system's application is the newest standing verdict again — the exact inverse pair (WB#1).

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
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

# --- the vocabulary -----------------------------------------------------------------------------------

NO_ROWS = "no_rows"
CLERICAL = "clerical"
FIELD_CHANGE = "field_change"
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
    FIELD_CHANGE,
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
# applied by the LINK itself — a clean 1:1 correction (the operator may still `keep` the original)
AUTO_DISPOSITIONS = frozenset({CORRECTION})
# applied by a decision (the system's since R8-B); a CANCELLATION suppresses its target while pending too
SUPPRESS_WHILE_PENDING = frozenset({VOID, DUPLICATES})
# applied by a decision (the system's since R8-B); nothing changes while one is pending
ROUTED_NO_PROVISIONAL = frozenset({PARTIAL, CONSOLIDATION, ADDITION, RESTATEMENT, FIELD_CHANGE})
ROUTED_DISPOSITIONS = SUPPRESS_WHILE_PENDING | ROUTED_NO_PROVISIONAL
# R8-B — every disposition the linker resolves to a DEFINITE original: the system applies each one
RESOLVED_DISPOSITIONS = AUTO_DISPOSITIONS | ROUTED_DISPOSITIONS

# the authors of a SYSTEM application (an ``apply`` decision). ``system:no-signal-effect`` = its effect changes
# no row a call signal reads (the 2026-10-18 rule); ``system:filer-correction`` = every other amendment resolved
# to a definite original (R8-B, 2026-10-20 — the filer's own legal correction, applied by default)
BASIS_NO_SIGNAL_EFFECT = "system:no-signal-effect"
BASIS_FILER_CORRECTION = "system:filer-correction"
SYSTEM_BASES: tuple[str, ...] = (BASIS_FILER_CORRECTION, BASIS_NO_SIGNAL_EFFECT)
_SYSTEM_PREFIX = "system:"

# R8-B — where an amendment sits in the operator's review (``review_bucket``)
REVIEW_OWED = "owed"  # only the operator can resolve it, and a call can read the rows it touches
REVIEW_AUTO = (
    "auto"  # applied automatically (a standing system apply, or a correction applied by its link)
)
REVIEW_DECIDED = (
    "decided"  # a standing OPERATOR verdict (including a keep over a system application)
)
REVIEW_QUEUED = (
    "queued"  # resolvable, no decision yet: the next pass's sweep applies it (a pre-R8-B link)
)
REVIEW_HISTORICAL = (
    "historical"  # ambiguous, but no call reads the rows it touches: the originals stand
)
REVIEW_INERT = "inert"  # nothing to apply
REVIEWS: tuple[str, ...] = (
    REVIEW_OWED,
    REVIEW_AUTO,
    REVIEW_DECIDED,
    REVIEW_QUEUED,
    REVIEW_HISTORICAL,
    REVIEW_INERT,
)


def is_system_basis(basis: object) -> bool:
    """Was a decision written by the SYSTEM (``system:*``), not the operator (``operator:*``)?"""
    return str(basis or "").startswith(_SYSTEM_PREFIX)


def system_basis(disposition: str, signal_effect: bool) -> str | None:
    """R8-B — the basis the SYSTEM applies an amendment of ``disposition`` with, or ``None`` when it cannot (an
    ambiguous link has no definite original; an inert one has nothing to apply). ``signal_effect`` only picks
    the basis — a resolved amendment is applied either way."""
    if disposition not in RESOLVED_DISPOSITIONS:
        return None
    return BASIS_FILER_CORRECTION if signal_effect else BASIS_NO_SIGNAL_EFFECT


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


def page_text(named: Sequence[str], more: int) -> str:
    """A page's list of NEW amendments owed a decision: the named ones (every in-window one — an item that can
    move tonight's call is never collapsed into a count — then the other owed ones up to the page's cap), the
    rest counted — ``(+N more in Admin)``."""
    if not named:
        return f"none inside tonight's signal windows (+{more} in Admin)" if more else ""
    shown = ", ".join(named)
    return f"{shown} (+{more} more in Admin)" if more else shown


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


def status_for(disposition: str, signal_effect: bool = True) -> str:
    """The status the SYSTEM gives a link of ``disposition`` (before any operator decision). R8-B: every
    disposition resolved to a definite original is APPLIED (the ingest records the system's ``apply`` decision
    in the link's transaction — ``system_basis``); an ambiguous one is pending (it has no target to apply); the
    rest are inert. ``signal_effect`` no longer changes the status (it picks the system's basis)."""
    del signal_effect  # kept in the signature: callers pass it, the status no longer depends on it
    if disposition in RESOLVED_DISPOSITIONS:
        return STATUS_APPLIED
    if disposition == AMBIGUOUS:
        return STATUS_PENDING
    return STATUS_INERT


def auto_resolves(disposition: str, signal_effect: bool) -> bool:
    """Does the system apply this amendment itself? R8-B: every disposition resolved to a definite original."""
    return system_basis(disposition, signal_effect) is not None


def within_review_horizon(
    changed: Iterable[Mapping[str, Any]], asof: date, days: int | None
) -> bool:
    """R8-B — does any CHANGED signal row sit inside the review horizon at ``asof`` (dated on/after ``asof -
    days``; ``None`` = unbounded)? The horizon is the dial ``insider_amendment_review_horizon_days`` (derived:
    the backtest window plus the longest detector read horizon — ``signals.horizons
    .insider_amendment_review_horizon``): a row older than it is read by no call, live or replayed. A row dated
    after ``asof`` is inside (over-include: tomorrow's call reads it). Empty ``changed`` = no signal effect =
    outside."""
    rows = [r for r in changed if _row_date(r) is not None]
    if not rows:
        return False
    if days is None:
        return True
    edge = asof - timedelta(days=days)
    return any(_row_date(r) >= edge for r in rows)  # type: ignore[operator]


def review_bucket(
    eff: Effective,
    *,
    verdict_basis: object,
    ever_decided: bool,
    in_review: bool,
) -> str:
    """R8-B — PURE: where one amendment sits in the operator's review, from its effective state.

    - ``inert`` — nothing to apply.
    - a standing verdict -> ``auto`` when the SYSTEM wrote it, else ``decided`` (the operator's — including a
      keep over a system application). A correction with no verdict is applied by its link -> ``auto``.
    - PENDING: an ambiguous link the operator has not linked is ``owed`` when ``in_review`` (it changes a
      signal row dated inside the review horizon — ``within_review_horizon``), else ``historical`` (no call
      reads those rows; the originals stand). An operator-linked one awaiting its verdict, and one whose
      decision the operator UNDID (``ever_decided`` with nothing standing), are ``owed`` — the operator took
      it. A resolvable one with no decision at all is ``queued``: a pre-R8-B link the next pass's sweep
      applies (a void suppresses meanwhile, as pending always did)."""
    if eff.status == STATUS_INERT:
        return REVIEW_INERT
    if eff.verdict is not None:
        return REVIEW_AUTO if is_system_basis(verdict_basis) else REVIEW_DECIDED
    if eff.status == STATUS_APPLIED:
        return REVIEW_AUTO
    if eff.disposition == AMBIGUOUS and eff.link_decision_id is None:
        return REVIEW_OWED if in_review else REVIEW_HISTORICAL
    if eff.link_decision_id is not None or ever_decided:
        return REVIEW_OWED
    return REVIEW_QUEUED


def sweep_basis(eff: Effective, *, ever_decided: bool, signal_effect: bool) -> str | None:
    """R8-B — the basis the nightly SWEEP applies one amendment with, or ``None`` to leave it alone. It acts
    ONLY on an amendment with no decision at all (``ever_decided`` False — anything the operator did, or an
    earlier system application, stays as it is: re-running appends nothing) whose effective disposition the
    system can apply (``system_basis``)."""
    if ever_decided or eff.verdict is not None or eff.link_decision_id is not None:
        return None
    return system_basis(eff.disposition, signal_effect)


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


def _norm_text(v: object) -> str:
    """casefold + collapse whitespace — the seniority match is a casefolded keyword test, so a pure case or
    spacing difference in a role or name is not a change a signal can see."""
    return " ".join(str(v or "").casefold().split())


def signal_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Everything a CALL signal reads on one row: the Table I identity, the 10b5-1 flag AS THE SCREENS READ IT
    (only an explicit ``True`` is planned — ``None`` and ``False`` read the same), and the reporting owner's
    role and name (seniority and the distinct-insider count read them)."""
    return (
        *signature(row),
        row.get("aff_10b5_1") is True,
        _norm_text(row.get("insider_role")),
        _norm_text(row.get("insider_name")),
    )


def signal_rows(
    rows: Iterable[Mapping[str, Any]], signal_codes: Collection[str]
) -> Counter[tuple[Any, ...]]:
    """The multiset of signal signatures of the rows a call signal reads (code in ``signal_codes``)."""
    return Counter(signal_signature(r) for r in rows if r.get("txn_code") in signal_codes)


def before_after(
    disposition: str,
    *,
    original_accessions: Sequence[str],
    voided_accessions: Sequence[str],
    candidate_accessions: Sequence[str],
    stored: Mapping[str, Sequence[Mapping[str, Any]]],
    amend_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """``(the stored rows the amendment acts on, the rows it would leave in their place)`` — a void/duplicates
    removes its targets, every other disposition replaces its originals with the amendment's rows (an
    ambiguous one with every candidate, its target being unknown)."""
    if disposition == DUPLICATES:
        return [r for a in voided_accessions for r in stored.get(a, ())], []
    if disposition == AMBIGUOUS:
        return [r for a in candidate_accessions for r in stored.get(a, ())], list(amend_rows)
    before = [r for a in original_accessions for r in stored.get(a, ())]
    return before, ([] if disposition == VOID else list(amend_rows))


def changed_signal_rows(
    disposition: str,
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    signal_codes: Collection[str],
) -> list[Mapping[str, Any]]:
    """The signal rows the amendment CHANGES: the rows on either side outside the common multiset (by
    ``signal_signature``). For an AMBIGUOUS link every signal row on either side (its target is unknown).
    Empty = no signal effect."""
    if disposition == AMBIGUOUS:
        return [r for r in (*before, *after) if r.get("txn_code") in signal_codes]
    common = signal_rows(before, signal_codes) & signal_rows(after, signal_codes)
    out: list[Mapping[str, Any]] = []
    for side in (before, after):
        left = Counter(common)
        for r in side:
            if r.get("txn_code") not in signal_codes:
                continue
            s = signal_signature(r)
            if left[s] > 0:
                left[s] -= 1
            else:
                out.append(r)
    return out


def in_window_codes(
    changed: Iterable[Mapping[str, Any]], asof: date, horizons: Mapping[str, int | None]
) -> tuple[str, ...]:
    """The signal codes whose CHANGED rows fall inside the live read horizon at ``asof`` — a row of code ``c``
    dated on/after ``asof - horizons[c]`` (``None`` = unbounded: any date). ``horizons`` is the detectors' own
    declaration (``signals.horizons.insider_signal_codes``). Empty = nothing that can move tonight's call.
    """
    out: set[str] = set()
    for r in changed:
        code = r.get("txn_code")
        d = _row_date(r)
        if code not in horizons or d is None or d > asof:
            continue
        days = horizons[code]
        if days is None or d >= asof - timedelta(days=days):
            out.add(str(code))
    return tuple(sorted(out))


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
    amend_rows: Sequence[Mapping[str, Any]],
    original_rows: Sequence[Mapping[str, Any]],
    signal_codes: Collection[str],
) -> str:
    """What one amendment changed against ONE linked original's stored rows (see the module table). With Table
    I identical it is CLERICAL only when no signal row changed in another field a signal reads (the owner's role
    or name, the 10b5-1 flag) — else FIELD_CHANGE, which routes (never "clerical / no change")."""
    if not amend_rows and not original_rows:
        return NO_ROWS
    if not amend_rows:
        return VOID
    if not original_rows:
        return ADDITION
    if Counter(signature(r) for r in amend_rows) == Counter(signature(r) for r in original_rows):
        if signal_rows(amend_rows, signal_codes) != signal_rows(original_rows, signal_codes):
            return FIELD_CHANGE
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
    # does its effect change a row a call signal reads; does the SYSTEM apply it (R8-B: every disposition
    # resolved to a definite original), and under which basis (``system_basis`` — the ingest records it)
    signal_effect: bool = True
    auto_resolved: bool = False
    basis: str | None = None


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
    signal_codes: Collection[str],
) -> LinkResult:
    """PURE — link the 4/A (``_cascade``), then judge it: ``signal_effect`` = its effect changes a row a call
    signal reads (``signal_codes``); R8-B — a disposition resolved to a definite original is applied by the
    system (``auto_resolved``, status ``applied``, ``basis`` = ``system_basis``); an ambiguous one is pending.
    """
    base = _cascade(amendment, stored, filed_by_accession, signal_codes)
    pool = {acc: rows for acc, rows in stored.items() if acc != amendment.accession and rows}
    before, after = before_after(
        base.disposition,
        original_accessions=base.original_accessions,
        voided_accessions=base.voided_accessions,
        candidate_accessions=base.candidate_accessions,
        stored=pool,
        amend_rows=list(amendment.rows),
    )
    effect = bool(changed_signal_rows(base.disposition, before, after, signal_codes))
    basis = system_basis(base.disposition, effect)
    return replace(
        base,
        signal_effect=effect,
        status=status_for(base.disposition, effect),
        auto_resolved=basis is not None,
        basis=basis,
    )


def _cascade(
    amendment: AmendmentFacts,
    stored: Mapping[str, Sequence[Mapping[str, Any]]],
    filed_by_accession: Mapping[str, date],
    signal_codes: Collection[str],
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
        disp = classify(amend_rows, pool[original], signal_codes)
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
    signal_codes: Collection[str],
) -> tuple[str, tuple[str, ...]]:
    """The operator's ``link`` of an AMBIGUOUS amendment to ``target``: ``(disposition, originals)`` computed
    against the target's stored rows — stored on the decision, so the read applies it without recomputing.
    An operator-linked amendment then waits for the operator's verdict (never resolved by the system: the
    operator is already deciding it)."""
    return classify(amend_rows, target_rows, signal_codes), (target,)


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

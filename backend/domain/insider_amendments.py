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
``void``            zero rows, the original has rows            SETTLED BY THE FILER'S WORDS (THE VOIDS
                                                                below): retraction words + a single
                                                                P/S original -> APPLIED (it is
                                                                hidden); no retraction words -> KEPT
                                                                as filed; retraction against several
                                                                trades -> HELD for the operator.
                                                                SUPPRESSES while pending (unswept)
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

"Applied by the system" = an ``apply`` verdict the system writes under its own basis instead of waiting for the
operator. What an applied partial / consolidation / addition / restatement / field change does to the rows is
the ROW MATCH below — it replaces only the original rows it accounts for.

THE ROW MATCH (R8-B, operator decision 2026-10-21, revised the same day — ``match_rows``): a correction removes
a buy or sale ONLY when it ACCOUNTS for that row; an amendment that merely ADDS an omitted line is not retracting
the sales it does not mention (MRCY 0001049521-26-000071 adds a grant and re-reports none of its original's two
sales). Accounting is EXACT SHAPE only, owner first: identical; same date + code + shares (a price fix); same
date + shares, another code (a recode — BAH's ``S`` -> ``F``); same code + shares, another date (a date fix); a
SUM on the same date + code (a consolidation — BHVN; one sale split into lots — BETR); then same date + code +
price (a share fix). A bare "same date + same code" never matches (IBKR's 4/A adds an omitted 09-19 sale beside
two real 09-19 sales). LEFTOVERS — an unaccounted original and an unmatched amendment row of the same owner AND
code — are settled by the FILER'S OWN WORDS (remarks + footnotes, ``filer_text``; the deterministic phrase lists
below): FIX words -> the amendment's rows replace those leftovers (MRAM, BKSY, IEHC); ADD words -> every original
stays and the amendment's rows are added (IBKR); NEITHER -> UNRESOLVED (YYAI): the system does not apply it, it
is owed to the operator inside the review horizon (historical outside), and the original stands meanwhile; BOTH
-> FIX when, in every leftover group, the amendment re-reports at least as many rows as the original (its rows
restate them all — IEHC), else unresolved. An unaccounted original that no leftover settles STANDS; an unmatched
amendment row is an addition. The match is computed once, against the full stored rows, when the ``apply``
decision is written, and STORED on it (``replaced_rows``, migration 0053): the original row keys it replaces
(``[]`` = it replaces nothing — an Add); NULL (every pre-R8-B decision, and the operator's "Use the correction")
replaces the whole original.

THE VOIDS (R8-B, operator decision 2026-10-21 — ``void_reading``): a zero-row 4/A retracts its original ONLY when
BOTH the filer's text carries explicit retraction language (``RETRACTION_PHRASES``) AND the original holds exactly
ONE transaction, a code-P/S one — the words can then mean only that trade (FLS, AVEX, MNKD, DVLT: APPLIED, the
original hidden). No retraction language -> the system KEEPS the original as filed (a ``keep``, basis
``system:no-retraction`` — a holdings / Table II / power-of-attorney fix: SMR, AZTA, VWAV; quiet, listed as
resolved automatically). Retraction language against several transactions -> HELD (a ``keep``, basis
``system:held``): owed inside the review horizon (historical outside), the original standing meanwhile (FIGR:
"did not in fact occur" beside a $7.43M sale). Words are never mapped to codes (FLS's text says "sale" for a
code-P buy). A duplicates set keeps its own handling (the later duplicates suppressed, the earliest standing).

THE FILER'S OWN CORRECTION IS APPLIED BY DEFAULT (R8-B, operator decision 2026-10-20). Every amendment the
linker resolves to a DEFINITE original — a correction, void, duplicates set, partial, consolidation, addition,
restatement or field change (``RESOLVED_DISPOSITIONS``) — is RESOLVED BY THE SYSTEM (``system_resolution``): an
append-only decision whose basis names why — an ``apply`` carrying its row match (``system:no-signal-effect``
when its effect changes no row a call signal reads — the 2026-10-18 rule, kept — else
``system:filer-correction``), or, for a void its words do not retract, a ``keep`` (``system:no-retraction`` /
``system:held`` — THE VOIDS above); an UNRESOLVED row match gets no decision at all. A 4/A is the insider's OWN
legal correction of their own filing, filed under penalty — deterministic data, not a model's recommendation —
so applying it is data handling, never a decision taken from the operator (#3 / #10 untouched), and every one
keeps its exact Undo: an operator verdict stands OVER a system decision without revoking it, and undoing that
verdict restores the system's resolution. The ingest records the decision in the SAME transaction as the link
(one ``recorded_at``: there is never a pending window for a new 4/A); the amendments a pre-R8-B ingest left
pending are resolved by the idempotent nightly SWEEP (``sweep_resolution`` —
``repositories.insider_amendment_repo.sweep``), each decision stamped ``recorded_at = now``, so a replay pinned
before the sweep still reads the state the system held then (pending; a void suppressing) — no rewrite of the
past (#1). The sweep acts only on an amendment with NO decision at all: anything the operator did (a verdict,
a link, an Undo) stays the operator's.

ONLY WHAT THE SYSTEM CANNOT RESOLVE IS OWED THE OPERATOR (``review_bucket``) — an AMBIGUOUS link (several
differing candidate originals: the system cannot know which filing it amends), an UNRESOLVED row match, or a
HELD void — and only when it MATTERS: it would change a row a call signal reads AND one of those rows is dated
inside the REVIEW HORIZON (``within_review_horizon``; the dial ``insider_amendment_review_horizon_days``, derived
by default as the two-year backtest window plus the longest detector read horizon — ``signals.horizons
.insider_amendment_review_horizon``; 940 days at today's config). Any of them outside that is "unresolved —
historical": no call reads those rows, the originals stand, it never pages, and the operator can still decide
it. MEASURED 2026-10-21 (prod, read-only, asof 2026-10-20, after the first sweep): 6 owed (4 ambiguous — CVNA,
FIS, HROW, SKYT; 1 unresolved match — YYAI; 1 held void — FIGR), none touching a row in tonight's signal
windows; 60 historical (39 ambiguous, 17 unresolved, 4 held).

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

THE OPERATOR'S DECISIONS (append-only, ``insider_amendment_decision``) — three verdicts, each with its exact
Undo: USE THE CORRECTION (``apply`` with no ``replaced_rows`` — for a void/duplicates: the suppression;
otherwise a FULL replace, the amendment's rows replacing the whole original), KEEP THE ORIGINAL (``keep`` — the
original stands; the amendment changes nothing), ADD TO THE ORIGINAL (``apply`` with ``replaced_rows = []`` and
basis ``operator:add`` — every original row stands and the amendment's rows are added; only for an amendment
that has rows); plus ``link`` (an AMBIGUOUS amendment: the operator picks the original; the disposition against
it is computed at decision time and STORED on the decision, so a read never recomputes it from a windowed row
set) and ``revoke`` (the Undo of any of them). A decision counts at a read iff it was recorded at/before the
read's ``known_at`` and no revoke recorded at/before ``known_at`` names it — so a replay pinned before a
ratification sees the pending state it actually held. The NEWEST standing verdict wins, which is how an operator
verdict overrules a standing SYSTEM decision (R8-B) without erasing it: revoke the operator's verdict and the
system's resolution is the newest standing verdict again — the exact inverse pair (WB#1).

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

import itertools
import re
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
# R8-B — the dispositions whose application REPLACES rows: applied by ``match_rows`` — an original row is
# replaced only when the amendment accounts for it (a correction already pairs every changed row 1:1; a void /
# duplicates removes, it does not replace)
MATCHED_DISPOSITIONS = ROUTED_NO_PROVISIONAL

# the authors of a SYSTEM application (an ``apply`` decision). ``system:no-signal-effect`` = its effect changes
# no row a call signal reads (the 2026-10-18 rule); ``system:filer-correction`` = every other amendment resolved
# to a definite original (R8-B, 2026-10-20 — the filer's own legal correction, applied by default)
BASIS_NO_SIGNAL_EFFECT = "system:no-signal-effect"
BASIS_FILER_CORRECTION = "system:filer-correction"
# the SYSTEM's resolutions that KEEP the original (a ``keep`` decision) — a zero-row 4/A (a void) whose filer's
# text retracts nothing (``system:no-retraction``: it fixes holdings / Table II / a power of attorney — inert,
# quiet), or retracts but whose original holds several transactions (``system:held``: the text cannot say which
# one it retracts — the original stands, owed to the operator) — ``void_reading``
BASIS_NO_RETRACTION = "system:no-retraction"
BASIS_HELD = "system:held"
SYSTEM_BASES: tuple[str, ...] = (
    BASIS_FILER_CORRECTION,
    BASIS_NO_SIGNAL_EFFECT,
    BASIS_NO_RETRACTION,
    BASIS_HELD,
)
_SYSTEM_PREFIX = "system:"
# the operator's three verdicts on an owed amendment: Use the correction (``apply`` — the whole original replaced),
# Keep the original (``keep``), Add to the original (an ``apply`` that replaces nothing — ``replaced_rows = []``:
# every original row stands and the amendment's rows are added)
ACTION_ADD = "add"
BASIS_OPERATOR_ADD = "operator:add"

# R8-B — where an amendment sits in the operator's review (``review_bucket``):
# owed       — only the operator can resolve it, and a call can read the rows it touches
# auto       — resolved automatically (a standing system apply or no-retraction keep, or a correction applied
#              by its link)
# decided    — a standing OPERATOR verdict (including one over a system decision)
# queued     — resolvable, no decision yet: the next pass's sweep resolves it (a pre-R8-B link)
# historical — unresolvable (ambiguous, an unresolved row match, or a held void), but no call reads the rows it
#              touches: the originals stand
# inert      — nothing to apply
REVIEW_OWED = "owed"
REVIEW_AUTO = "auto"
REVIEW_DECIDED = "decided"
REVIEW_QUEUED = "queued"
REVIEW_HISTORICAL = "historical"
REVIEW_INERT = "inert"
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
    unresolved: bool = False,
) -> str:
    """R8-B — PURE: where one amendment sits in the operator's review, from its effective state.

    - ``inert`` — nothing to apply.
    - a standing verdict -> ``auto`` when the SYSTEM wrote it, else ``decided`` (the operator's — including a
      keep over a system application). A correction with no verdict is applied by its link -> ``auto``.
    - PENDING: an ambiguous link the operator has not linked is ``owed`` when ``in_review`` (it changes a
      signal row dated inside the review horizon — ``within_review_horizon``), else ``historical`` (no call
      reads those rows; the originals stand). An operator-linked one awaiting its verdict, and one whose
      decision the operator UNDID (``ever_decided`` with nothing standing), are ``owed`` — the operator took
      it. An ``unresolved`` row match (``match_rows`` — a buy/sell whose date AND share count both changed)
      is the ambiguous link's twin: ``owed`` when ``in_review``, else ``historical``; the original stands. A
      resolvable one with no decision at all is ``queued``: a pre-R8-B link the next pass's sweep applies (a
      void suppresses meanwhile, as pending always did). A void the system HELD (``system:held`` — retraction
      language, several trades on the original) is ``owed`` / ``historical`` the same way, its original
      standing."""
    if eff.status == STATUS_INERT:
        return REVIEW_INERT
    if eff.verdict is not None:
        if verdict_basis == BASIS_HELD:  # the system kept the original for the operator to decide
            return REVIEW_OWED if in_review else REVIEW_HISTORICAL
        return REVIEW_AUTO if is_system_basis(verdict_basis) else REVIEW_DECIDED
    if eff.status == STATUS_APPLIED:
        return REVIEW_AUTO
    if eff.disposition == AMBIGUOUS and eff.link_decision_id is None:
        return REVIEW_OWED if in_review else REVIEW_HISTORICAL
    if eff.link_decision_id is not None or ever_decided:
        return REVIEW_OWED
    if unresolved:
        return REVIEW_OWED if in_review else REVIEW_HISTORICAL
    return REVIEW_QUEUED


@dataclass(frozen=True)
class SystemResolution:
    """What the SYSTEM records for one amendment: an ``apply`` (the filer's correction — with the row match it
    accounts for, ``replaced_rows``, for a matched disposition) or a ``keep`` (a void whose text retracts nothing
    — ``system:no-retraction`` — or retracts against several trades — ``system:held``)."""

    action: str
    basis: str
    replaced_rows: tuple[str, ...] | None = None


def system_resolution(
    disposition: str,
    *,
    signal_effect: bool,
    match: RowMatch | None = None,
    void: VoidReading | None = None,
) -> SystemResolution | None:
    """R8-B (revised 2026-10-21) — PURE: the system's resolution of one amendment, or ``None`` when it has none
    (an ambiguous link, an unresolved row match, an inert disposition — the operator's, or nothing to do).

    - a correction / duplicates set -> ``apply`` (a correction pairs 1:1; a duplicate is an exact copy);
    - a void -> by ``void_reading``: a retraction of a single-trade original -> ``apply`` (it is hidden); no
      retraction language -> ``keep`` (``system:no-retraction`` — the original stands, quietly); retraction
      language against several trades -> ``keep`` (``system:held`` — the original stands, owed);
    - a partial / consolidation / addition / restatement / field change -> ``apply`` carrying its row match
      (``match_rows``) — unless the match is unresolved."""
    if disposition not in RESOLVED_DISPOSITIONS:
        return None
    basis = system_basis(disposition, signal_effect) or BASIS_FILER_CORRECTION
    if disposition == VOID:
        kind = void.kind if void is not None else VOID_RETRACT
        if kind == VOID_NO_RETRACTION:
            return SystemResolution(ACTION_KEEP, BASIS_NO_RETRACTION)
        if kind == VOID_HELD:
            return SystemResolution(ACTION_KEEP, BASIS_HELD)
        return SystemResolution(ACTION_APPLY, basis)
    if disposition in MATCHED_DISPOSITIONS:
        if match is None:
            return SystemResolution(ACTION_APPLY, basis)
        if match.unresolved:
            return None
        return SystemResolution(ACTION_APPLY, basis, match.replaced)
    return SystemResolution(ACTION_APPLY, basis)


def sweep_resolution(
    eff: Effective, *, ever_decided: bool, resolution: SystemResolution | None
) -> SystemResolution | None:
    """R8-B — what the nightly SWEEP records for one amendment, or ``None`` to leave it alone. It acts ONLY on an
    amendment with no decision at all (``ever_decided`` False — anything the operator did, or an earlier system
    resolution, stays as it is: re-running appends nothing)."""
    if ever_decided or eff.verdict is not None or eff.link_decision_id is not None:
        return None
    return resolution


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
    replaced: Collection[str] | None = None,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """``(the stored rows the amendment acts on, the rows it would leave in their place)`` — a void/duplicates
    removes its targets, every other disposition replaces its originals with the amendment's rows (an
    ambiguous one with every candidate, its target being unknown). R8-B: with ``replaced`` (the original row
    keys a MATCHED disposition accounts for — ``match_rows``), only those are replaced; every other original row
    stays on the after side too."""
    if disposition == DUPLICATES:
        return [r for a in voided_accessions for r in stored.get(a, ())], []
    if disposition == AMBIGUOUS:
        return [r for a in candidate_accessions for r in stored.get(a, ())], list(amend_rows)
    before = originals_rows(original_accessions, stored)
    if disposition == VOID:
        return before, []
    if replaced is not None and disposition in MATCHED_DISPOSITIONS:
        gone = set(replaced)
        return before, [r for r in before if row_key(r) not in gone] + list(amend_rows)
    return before, list(amend_rows)


def row_key(row: Mapping[str, Any]) -> str:
    """A STORED row's identity inside its filing: ``"<accession>#<txn_seq>"`` (one row per transaction — the
    filing's position — so the pair is unique within the security's tape)."""
    return f"{row.get('accession')}#{row.get('txn_seq')}"


def _same_owner(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """One reporting owner on both rows: by CIK when both carry one, else by the casefolded name."""
    ca, cb = norm_cik(a.get("rpt_owner_cik")), norm_cik(b.get("rpt_owner_cik"))
    if ca and cb:
        return ca == cb
    return _norm_text(a.get("insider_name")) == _norm_text(b.get("insider_name"))


# --- R8-B: what the filer's OWN words say (deterministic phrase lists — a located passage, never a model) ---
# Kept small and explicit; each is matched case-insensitively against the 4/A's remarks + footnotes
# (``filer_text``). Documented in ``docs/FEED_LOOP.md`` §R8-B.
_TXN_NOUN = r"(?:sale|purchase|acquisition|disposition|transaction|grant)s?"
# a zero-row 4/A RETRACTS a trade only in these words (``void_reading``)
RETRACTION_PHRASES: tuple[str, ...] = (
    r"\bcancel(?:l?ed|lation|ing)?\b",
    r"\bdid not (?:in fact )?occur\b",
    r"\bnever occurred\b",
    r"\bno transactions? occurred\b",
    r"\bnot (?:actually )?(?:executed|consummated|effected)\b",
    r"\bshould not have been (?:filed|reported|made)\b",
    r"\b(?:filed|submitted) in error\b",
    r"\berroneously (?:filed|submitted)\b",
    rf"\berroneously reported (?:a|an|the) {_TXN_NOUN}",
    r"\brescind(?:ed)?\b",
    r"\bduplicat(?:e|ive)\b",
    r"\bvoid(?:ed)? ab initio\b",
    r"\bnever (?:held|owned|acquired|purchased|sold)\b",
    r"\bdoes not (?:hold|own) any\b",
    rf"\bmistakenly (?:filed|reported) (?:a|an|the) (?:\w+ )?{_TXN_NOUN}",
    r"\battributable to (?:him|her|them)\b",
)
# the filer FIXES the trade it re-reports (a leftover original is replaced by the amendment's rows)
FIX_PHRASES: tuple[str, ...] = (
    r"\bcorrect(?:s|ed|ing)?\b[^.;]{0,120}?\b(?:dates?|prices?|number of shares (?:sold|purchased|acquired|"
    r"disposed)|amounts? of (?:shares|securities) (?:sold|purchased|acquired|disposed)|transaction codes?)\b",
    r"\bincorrectly reported\b[^.;]{0,60}?\b(?:dates?|prices?|details|transactions?|sales?|purchases?)\b",
)
# the filer ADDS a trade it left out (every original is kept; the amendment's rows are additions)
ADD_PHRASES: tuple[str, ...] = (
    r"\binadvertent(?:ly)? (?:excluded|omitted|omission)\b",
    r"\bto include\b[^.;]{0,80}?\b(?:not (?:previously )?reported|additional|omitted|excluded)\b",
    r"\bto report an? (?:additional|omitted)\b",
    r"\badditional (?:shares|sales?|purchases?|transactions?)\b",
)
_RX_RETRACTION = tuple(re.compile(x, re.I) for x in RETRACTION_PHRASES)
_RX_FIX = tuple(re.compile(x, re.I) for x in FIX_PHRASES)
_RX_ADD = tuple(re.compile(x, re.I) for x in ADD_PHRASES)


def filer_text(remarks: object, footnotes: Iterable[object] | None) -> str:
    """The filer's own words on a 4/A: its remarks plus every footnote (a stored footnote is ``{id, text}``)."""
    parts = [str(remarks or "")]
    for f in footnotes or ():
        parts.append(str(f.get("text") or "") if isinstance(f, Mapping) else str(f or ""))
    return " ".join(" ".join(x.split()) for x in parts if x)


def _passage(text: str, patterns: Sequence[re.Pattern[str]]) -> str | None:
    """The first phrase hit as a short located passage (shown on Admin as the evidence), or ``None``."""
    for rx in patterns:
        m = rx.search(text or "")
        if m:
            a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
            return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")
    return None


# R8-B — the readings
READ_EXACT = "exact"  # every leftover is accounted for by shape (or nothing conflicts)
READ_FIX = "fix"  # the filer's words FIX the trade: leftover originals replaced
READ_ADD = "add"  # the filer's words ADD a trade: every original kept, the amendment's rows added
READ_UNRESOLVED = "unresolved"  # neither (or a both-ways text the tie-break cannot settle): owed
VOID_RETRACT = "retract"  # retraction language + a single-trade original: the void applies (hidden)
VOID_NO_RETRACTION = "no_retraction"  # no retraction language: the original stands (inert)
VOID_HELD = "held"  # retraction language, several trades on the original: owed, the original stands


@dataclass(frozen=True)
class VoidReading:
    """A zero-row 4/A read by the filer's words and its original's shape (``void_reading``)."""

    kind: str
    passage: str | None = None


def void_reading(
    text: str, original_rows: Sequence[Mapping[str, Any]], signal_codes: Collection[str]
) -> VoidReading:
    """PURE — R8-B (operator-approved 2026-10-21): does a zero-row amendment RETRACT its original? Only when BOTH
    hold — the filer's own text carries explicit retraction language (``RETRACTION_PHRASES``) AND the original
    holds exactly ONE transaction, a code-P/S one (so the words can only mean that trade: FLS's text says "sale"
    for a code-P buy, so words are never mapped to codes). No retraction language -> the original stands (a
    holdings / Table II / power-of-attorney fix: SMR, AZTA, VWAV). Retraction language against several trades
    -> held for the operator (FIGR: "shares withheld … did not in fact occur" beside a $7.4M sale).
    """
    passage = _passage(text, _RX_RETRACTION)
    if passage is None:
        return VoidReading(VOID_NO_RETRACTION)
    if len(original_rows) == 1 and original_rows[0].get("txn_code") in signal_codes:
        return VoidReading(VOID_RETRACT, passage)
    return VoidReading(VOID_HELD, passage)


@dataclass(frozen=True)
class RowMatch:
    """What an amendment ACCOUNTS for among its originals' rows (``match_rows``).

    ``replaced`` = the original row keys it replaces when applied; ``kept`` = the original row keys it does not
    (they STAND); ``added`` = how many amendment rows match no original row (additions); ``reading`` = how the
    leftovers were settled (``exact`` / ``fix`` / ``add`` / ``unresolved``) and ``passage`` the filer's words that
    settled them; ``conflicts`` = the rows behind an UNRESOLVED match (an unaccounted original buy/sell and an
    unmatched amendment row of the same owner and code, the filer's words deciding neither way): owed to the
    operator, the original standing meanwhile."""

    replaced: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    added: int = 0
    conflicts: tuple[Mapping[str, Any], ...] = ()
    reading: str = READ_EXACT
    passage: str | None = None

    @property
    def unresolved(self) -> bool:
        return bool(self.conflicts)


_SUM_GROUP_CAP = (
    12  # the most rows one subset-sum search scans (2^12 subsets — a filing's lots, never more)
)


def _eq_shares(a: object, b: object) -> bool:
    x, y = _num(a), _num(b)
    return x is not None and y is not None and abs(x - y) <= 1e-6 * max(1.0, abs(y))


def _subset_summing(
    pool: Sequence[int], rows: Sequence[Mapping[str, Any]], target: object
) -> tuple[int, ...] | None:
    """The smallest subset (two or more, index order) of ``pool`` whose share counts sum to ``target``."""
    t = _num(target)
    if t is None or len(pool) < 2 or len(pool) > _SUM_GROUP_CAP:
        return None
    for size in range(2, len(pool) + 1):
        for combo in itertools.combinations(pool, size):
            total = sum(_num(rows[i].get("shares")) or 0.0 for i in combo)
            if abs(total - t) <= 1e-6 * max(1.0, abs(t)):
                return combo
    return None


def match_rows(
    amend_rows: Sequence[Mapping[str, Any]],
    original_rows: Sequence[Mapping[str, Any]],
    signal_codes: Collection[str],
    text: str = "",
) -> RowMatch:
    """PURE — R8-B (operator-approved 2026-10-21): which of the originals' rows does the amendment ACCOUNT for? A
    correction removes a buy or sale only when it accounts for that row — an amendment that merely ADDS an
    omitted line is not retracting the sales it does not mention. Owner first, then:

    1. EXACT SHAPE, one-to-one, in this order: identical; same date + code + shares (a price fix); same date +
       shares, another code (a recode — BAH's ``S`` -> ``F``); same code + shares, another date (a date fix).
    2. SUMS, same owner + date + code: an amendment row whose shares equal the sum of two or more originals (a
       consolidation — BHVN's 25,000 + 3,400 restated as 28,400; the trust lots it does not mention stand), or an
       original whose shares equal the sum of two or more amendment rows (one sale split into lots — BETR). The
       smallest such subset, in date order.
    3. Then same date + code + price (a share fix) — after the sums, so equal-priced lots consolidate by their
       sum rather than pairing one-to-one. A bare "same date + same code" never matches (IBKR's 4/A adds an
       omitted 09-19 sale beside two real 09-19 sales).
    4. LEFTOVERS — an unaccounted original and an unmatched amendment row of the same owner AND code — are
       settled by the FILER'S WORDS (``text``, the remarks + footnotes): FIX language (``FIX_PHRASES``) -> the
       amendment's rows replace those leftovers (MRAM, BKSY); ADD language (``ADD_PHRASES``) -> every original is
       kept, the amendment's rows are additions (IBKR); BOTH -> FIX when, in every leftover group, the amendment
       re-reports at least as many rows as the original (its rows restate all of them — IEHC), else unresolved;
       NEITHER -> unresolved (YYAI) when a buy/sell is among them (owed, the original standing); a non-signal
       leftover with no decisive words is simply kept beside the addition.

    Every other unaccounted original row STANDS; every other unmatched amendment row is an addition.
    """

    def _key_order(r: Mapping[str, Any]) -> tuple[Any, ...]:
        return (_row_date(r) or date.min, int(r.get("txn_seq") or 0))

    origs = sorted(original_rows, key=_key_order)
    amends = sorted(amend_rows, key=_key_order)
    accounted: set[int] = set()
    used: set[int] = set()

    def same_date(o: Mapping[str, Any], a: Mapping[str, Any]) -> bool:
        return _row_date(o) == _row_date(a)

    def same_code(o: Mapping[str, Any], a: Mapping[str, Any]) -> bool:
        return o.get("txn_code") == a.get("txn_code")

    def same_shares(o: Mapping[str, Any], a: Mapping[str, Any]) -> bool:
        return _eq_shares(o.get("shares"), a.get("shares"))

    def same_price(o: Mapping[str, Any], a: Mapping[str, Any]) -> bool:
        po, pa = _num(o.get("price")), _num(a.get("price"))
        return po is not None and pa is not None and po == pa

    def pair(rule: Any) -> None:  # one-to-one, both sides in date order
        for i, o in enumerate(origs):
            if i in accounted:
                continue
            for j, a in enumerate(amends):
                if j not in used and _same_owner(o, a) and rule(o, a):
                    accounted.add(i)
                    used.add(j)
                    break

    def sums() -> None:
        # an amendment row = the sum of several originals (a consolidation)
        for j, a in enumerate(amends):
            if j in used:
                continue
            pool = [
                i
                for i, o in enumerate(origs)
                if i not in accounted and _same_owner(o, a) and same_date(o, a) and same_code(o, a)
            ]
            combo = _subset_summing(pool, origs, a.get("shares"))
            if combo:
                accounted.update(combo)
                used.add(j)
        # an original = the sum of several amendment rows (one trade split into lots)
        for i, o in enumerate(origs):
            if i in accounted:
                continue
            pool = [
                j
                for j, a in enumerate(amends)
                if j not in used and _same_owner(o, a) and same_date(o, a) and same_code(o, a)
            ]
            combo = _subset_summing(pool, amends, o.get("shares"))
            if combo:
                accounted.add(i)
                used.update(combo)

    # the exact shapes that keep the share count first, then the sums (exact arithmetic), then the share fix
    # (same date + code + price — last, so equal-priced lots consolidate by their sum, never pair one-to-one)
    pair(
        lambda o, a: same_date(o, a) and same_code(o, a) and same_shares(o, a) and same_price(o, a)
    )
    pair(lambda o, a: same_date(o, a) and same_code(o, a) and same_shares(o, a))
    pair(lambda o, a: same_date(o, a) and same_shares(o, a) and not same_code(o, a))
    pair(lambda o, a: same_code(o, a) and same_shares(o, a) and not same_date(o, a))
    sums()
    pair(lambda o, a: same_date(o, a) and same_code(o, a) and same_price(o, a))
    # the leftovers of one owner and one code on both sides — the filer's words settle them
    groups: list[tuple[list[int], list[int]]] = []
    for i, o in enumerate(origs):
        if i in accounted or any(i in g[0] for g in groups):
            continue
        g_orig = [
            k
            for k, x in enumerate(origs)
            if k not in accounted and _same_owner(o, x) and same_code(o, x)
        ]
        g_amend = [
            j
            for j, a in enumerate(amends)
            if j not in used and _same_owner(o, a) and same_code(o, a)
        ]
        if g_amend:
            groups.append((g_orig, g_amend))
    reading, passage = READ_EXACT, None
    conflicts: list[Mapping[str, Any]] = []
    if groups:
        fix, add = _passage(text, _RX_FIX), _passage(text, _RX_ADD)
        if fix and add:
            restates = all(len(ga) >= len(go) for go, ga in groups)
            reading, passage = (READ_FIX, fix) if restates else (READ_UNRESOLVED, None)
        elif fix:
            reading, passage = READ_FIX, fix
        elif add:
            reading, passage = READ_ADD, add
        else:
            reading = READ_UNRESOLVED
        if reading == READ_FIX:
            for go, ga in groups:
                accounted.update(go)
                used.update(ga)
        elif reading == READ_UNRESOLVED:
            for go, ga in groups:
                if origs[go[0]].get("txn_code") in signal_codes:
                    conflicts.extend(origs[k] for k in go)
                    conflicts.extend(amends[j] for j in ga)
            if (
                not conflicts
            ):  # only non-signal leftovers: keep them beside the additions (nothing a call reads)
                reading = READ_ADD
    return RowMatch(
        replaced=tuple(sorted(row_key(o) for i, o in enumerate(origs) if i in accounted)),
        kept=tuple(sorted(row_key(o) for i, o in enumerate(origs) if i not in accounted)),
        added=sum(1 for j in range(len(amends)) if j not in used),
        conflicts=tuple(conflicts),
        reading=reading,
        passage=passage,
    )


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
    # R8-B — the filer's own words (remarks + footnotes, ``filer_text``): what settles a void and a leftover
    text: str = ""


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
    # R8-B — a MATCHED disposition's row match (``match_rows``): the original row keys it replaces when
    # applied (``None`` for a disposition that does not match rows); ``unresolved`` = owed to the operator
    replaced_rows: tuple[str, ...] | None = None
    unresolved: bool = False
    # R8-B — the SYSTEM decision the ingest records (``apply`` / ``keep``; ``None`` = none — the operator's or
    # nothing to do), and a void's reading (``void_reading``)
    system_action: str | None = None
    void_kind: str | None = None


def originals_rows(
    accessions: Iterable[str], stored: Mapping[str, Sequence[Mapping[str, Any]]]
) -> list[Mapping[str, Any]]:
    """The stored rows of ``accessions``, each carrying its ``accession`` (the row key needs it)."""
    return [{**r, "accession": a} for a in accessions for r in stored.get(a, ())]


def match_for(
    disposition: str,
    original_accessions: Iterable[str],
    stored: Mapping[str, Sequence[Mapping[str, Any]]],
    amend_rows: Sequence[Mapping[str, Any]],
    signal_codes: Collection[str],
    text: str = "",
) -> RowMatch | None:
    """``match_rows`` for a MATCHED disposition against its originals' FULL stored rows and the filer's words;
    ``None`` for any other disposition (a correction pairs 1:1 by construction; a void / duplicates /
    ambiguous / inert one replaces nothing)."""
    if disposition not in MATCHED_DISPOSITIONS:
        return None
    return match_rows(amend_rows, originals_rows(original_accessions, stored), signal_codes, text)


def void_for(
    disposition: str,
    original_accessions: Iterable[str],
    stored: Mapping[str, Sequence[Mapping[str, Any]]],
    signal_codes: Collection[str],
    text: str = "",
) -> VoidReading | None:
    """``void_reading`` for a VOID against its original's FULL stored rows; ``None`` for any other disposition."""
    if disposition != VOID:
        return None
    return void_reading(text, originals_rows(original_accessions, stored), signal_codes)


def status_of(disposition: str, res: SystemResolution | None, *, unresolved: bool) -> str:
    """The status the SYSTEM gives a link (R8-B revised): applied when it applies it; inert when it keeps the
    original of a void that retracts nothing; pending when it holds it for the operator, when the match is
    unresolved, or when the link is ambiguous; else the disposition's own (inert)."""
    if res is None:
        if unresolved or disposition == AMBIGUOUS:
            return STATUS_PENDING
        return status_for(disposition)
    if res.action == ACTION_APPLY:
        return STATUS_APPLIED
    return STATUS_INERT if res.basis == BASIS_NO_RETRACTION else STATUS_PENDING


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
    """PURE — link the 4/A (``_cascade``), then judge it: a MATCHED disposition's rows are matched against its
    original's (``match_rows`` — R8-B: only the rows it accounts for are replaced); ``signal_effect`` = its
    effect changes a row a call signal reads (``signal_codes``). A disposition resolved to a definite original
    is applied by the system (``auto_resolved``, status ``applied``, ``basis`` = ``system_basis``) unless its
    row match is UNRESOLVED (then pending — the operator's); an ambiguous one is pending."""
    base = _cascade(amendment, stored, filed_by_accession, signal_codes)
    pool = {acc: rows for acc, rows in stored.items() if acc != amendment.accession and rows}
    match = match_for(
        base.disposition,
        base.original_accessions,
        pool,
        list(amendment.rows),
        signal_codes,
        amendment.text,
    )
    void = void_for(base.disposition, base.original_accessions, pool, signal_codes, amendment.text)
    before, after = before_after(
        base.disposition,
        original_accessions=base.original_accessions,
        voided_accessions=base.voided_accessions,
        candidate_accessions=base.candidate_accessions,
        stored=pool,
        amend_rows=list(amendment.rows),
        replaced=match.replaced if match is not None else None,
    )
    effect = bool(changed_signal_rows(base.disposition, before, after, signal_codes))
    unresolved = match is not None and match.unresolved
    res = system_resolution(base.disposition, signal_effect=effect, match=match, void=void)
    return replace(
        base,
        signal_effect=effect,
        status=status_of(base.disposition, res, unresolved=unresolved),
        auto_resolved=res is not None and res.basis != BASIS_HELD,
        basis=res.basis if res is not None else None,
        replaced_rows=match.replaced if match is not None else None,
        unresolved=unresolved,
        system_action=res.action if res is not None else None,
        void_kind=void.kind if void is not None else None,
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
    # the originals whose rows the amendment's rows replace (non-empty = emit)
    emit_for: tuple[str, ...] = ()
    # R8-B — the individual original rows (``row_key``) an applied MATCHED amendment replaces; with these the
    # rest of the original stands (``suppress`` stays empty for it)
    suppress_rows: tuple[str, ...] = ()


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
    operator link (an ambiguous amendment's pick) and its standing verdict. See the module table. An applied
    MATCHED disposition whose verdict carries ``replaced_rows`` (R8-B — the rows its match accounts for,
    stored when the decision was written) replaces exactly those rows; one whose verdict carries none (every
    pre-R8-B decision, and an operator's apply of an unresolved match) replaces its whole original.
    """
    src = link_decision if link_decision is not None else link
    disp = str(src["disposition"])
    originals = _list(src.get("original_accessions"))
    voided = _list(src.get("voided_accessions"))
    v = verdict["action"] if verdict is not None else None
    suppress: tuple[str, ...] = ()
    emit_for: tuple[str, ...] = ()
    suppress_rows: tuple[str, ...] = ()
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
            emit_for = originals
            rows = verdict.get("replaced_rows") if verdict is not None else None
            if rows is None:
                suppress = originals  # the whole original (R8-A semantics)
            else:
                suppress_rows = _list(rows)
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
        suppress_rows=suppress_rows,
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
    2. A suppressed accession's rows leave the read, and so does each single original row an applied MATCHED
       amendment replaces (``suppress_rows`` — R8-B: the rest of that original stands); for an original several
       applied amendments replace, only the NEWEST statement (by SEC acceptance, then accession) emits — one
       statement per original.
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
    suppressed_rows: set[str] = set()  # R8-B — single original rows a matched amendment replaces
    winners: dict[str, _Emission] = {}
    for st in states:
        suppressed.update(st.suppress)
        suppressed_rows.update(st.suppress_rows)
        am = by_acc.get(st.accession)
        if am is None or not st.emit_for:
            continue  # nothing to emit (or the record is not visible: never invent rows)
        accepted = am.get("accepted")
        order = (accepted if accepted is not None else am["recorded_at"], st.accession)
        for original in st.emit_for:
            cur = winners.get(original)
            if cur is None or order > cur.order:
                winners[original] = _Emission(order, am, original)
    if not suppressed and not suppressed_rows and not winners and not screen:
        return rows if isinstance(rows, list) else list(rows)
    kept = [
        r
        for r in rows
        if str(r.get("accession")) not in suppressed
        and row_key(r) not in suppressed_rows
        and not (screen and is_other_issuer(r, own))
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

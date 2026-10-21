"""The Form 4/A amendment log (migration 0050, R8-A / R8-B) — the Admin panel's reads, the operator's decisions,
and the nightly SWEEP that applies the filer's own corrections.

Three append-only tables (``insider_amendment`` as filed, ``insider_amendment_link``, the
``insider_amendment_decision`` log); this module reads them and writes decisions — plus, for the R8 linker fix's
re-link only, a new LINK version (through the ingest's own ``write_link``). The ingest writes the filings and
links (``ingest.edgar.form4a``, with the system's ``apply`` for every resolvable one since R8-B);
the read path resolves against all three (``db.insider_resolution`` -> ``domain.insider_amendments``).

THE VERBS, each with its visible inverse (WB#1):

- ``decide(..., "apply")`` — ratify: a VOID / DUPLICATES keeps its suppression (now ratified), a routed
  restatement's rows replace the original's. ``decide(..., "keep")`` — the original stands; the amendment
  changes nothing (the AZTA power-of-attorney 4/A). Since R8-B ``keep`` is also the exact UNDO of a SYSTEM
  application (and of a correction applied by its link): it stands OVER the system's ``apply`` without revoking
  it, so revoking the keep restores the system's application.
- ``link_candidate`` — an AMBIGUOUS amendment's original, picked by the operator from the candidates the
  cascade found; the disposition against it is computed HERE (against the full stored rows) and STORED on the
  decision, so a read never recomputes it from a windowed row set.
- ``revoke`` — the Undo of any standing decision: appends a ``revoke`` row naming it.
- ``sweep`` (R8-B, a SYSTEM write) — appends a system ``apply`` for every resolvable amendment that has NO
  decision at all (a pre-R8-B link left pending). Idempotent by construction: it never touches an amendment
  anything was ever decided on.
- ``relink`` (the R8 linker fix, a SYSTEM write — ``pipeline.relink_amendments``) — appends a new link version
  for a stored link the fixed cascade moves (an original made public after its amendment; an ambiguous set the
  row tie-break settles), the system's ``revoke`` (``system:relink``) of its stale decision and its decision
  against the new original — never on an amendment the operator decided or linked. Idempotent: once applied
  the stored link IS the fixed cascade's.
- ``rederive`` (the R8 re-derivation, a SYSTEM write — ``pipeline.rederive_amendment_matches``) — for a LEGACY
  system ``apply`` written before the row match (``replaced_rows`` NULL on a matched disposition) whose row match
  changes the read, appends the system's ``revoke`` (``system:rederive``) of it and the system's ``apply``
  carrying the row match — never on an amendment the operator ever decided. Idempotent: a re-derived decision
  carries its row match, so a re-run finds nothing.

EXACTNESS RULES (why a decision can be trusted):

1. Every write locks the amendment's link rows ``FOR UPDATE`` first, so two clicks on one amendment
   serialize (and the sweep serializes with a click).
2. At most ONE standing link decision and ONE standing OPERATOR verdict per amendment: a second is refused
   (``AmendmentConflict`` — undo the first), and a link cannot be undone while a verdict decided under it
   stands. The one sanctioned stack is the operator's ``keep`` over a standing SYSTEM ``apply`` (R8-B); the
   pure resolver's "newest standing verdict" is then the operator's keep — the one the operator meant.
3. Nothing judges the filing here: which disposition an amendment has is the linker's
   (``domain.insider_amendments``); this module only refuses a verdict that the disposition does not admit.

The caller owns the transaction (commit after a successful write; roll back on an exception).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.insider_resolution import load_inputs
from domain.insider_amendments import (
    ACTION_ADD,
    ACTION_APPLY,
    ACTION_KEEP,
    ACTION_LINK,
    AMBIGUOUS,
    AUTO_DISPOSITIONS,
    BASIS_HELD,
    BASIS_REDERIVE,
    BASIS_RELINK,
    INERT_DISPOSITIONS,
    REVIEW_AUTO,
    REVIEW_OWED,
    ROUTED_DISPOSITIONS,
    ROUTED_NO_PROVISIONAL,
    STATUS_APPLIED,
    VERDICTS,
    AmendmentFacts,
    Effective,
    LinkResult,
    RowMatch,
    SystemResolution,
    VoidReading,
    before_after,
    changed_signal_rows,
    effect_of,
    filer_text,
    in_window_codes,
    is_legacy_whole,
    is_system_basis,
    latest_by_accession,
    link_amendment,
    link_identity,
    link_to_candidate,
    match_for,
    norm_cik,
    rederive_scope,
    relink_rule,
    review_bucket,
    standing_decisions,
    sweep_resolution,
    system_resolution,
    void_for,
    within_review_horizon,
)
from ingest.edgar.form4a import stored_rows_by_accession, system_decision_values, write_link


class AmendmentConflict(Exception):
    """A decision the current state does not allow (nothing to ratify, a decision already stands, the
    candidate is not one the cascade found). The API maps it to **409**; the message is operator-facing.
    """


@dataclass
class AmendmentItem:
    """One amendment as the Admin panel shows it: the filing as filed, its link, its effective state, the
    standing decisions (each undoable), and the ORIGINAL rows it touches beside its own."""

    security_id: UUID
    ticker: str | None
    accession: str
    issuer_cik: str | None
    filed: date | None
    accepted: datetime | None
    recorded_at: datetime
    owners: list[dict[str, Any]]
    date_of_original_submission: date | None
    remarks: str | None
    footnotes: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    derivative_count: int
    effective: Effective
    why: str
    link_recorded_at: datetime
    decisions: list[dict[str, Any]] = field(default_factory=list)  # every decision, oldest first
    # accession -> its stored rows
    originals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # does its effect change a row a call signal reads; was it applied by the SYSTEM (``review == auto``);
    # which signal codes' changed rows sit inside the live read horizon at the read's asof (the urgency); does
    # either side carry a code-P buy
    signal_effect: bool = True
    auto_resolved: bool = False
    in_window: tuple[str, ...] = ()
    touches_buy: bool = False
    # R8-B — the basis of the standing verdict (``system:*`` = the system applied it, ``operator:*`` = yours);
    # does a changed signal row sit inside the review horizon at the read's asof; where it sits in the
    # operator's review (``domain.insider_amendments.review_bucket``)
    verdict_basis: str | None = None
    in_review: bool = False
    review: str = REVIEW_OWED
    # R8-B — a MATCHED disposition's row match against the FULL stored originals (``match_rows``): which original
    # rows it accounts for, which STAND, and whether it is unresolved (owed); ``None`` for any other disposition
    match: RowMatch | None = None
    # the original rows (``row_key``) this amendment does not replace — they STAND beside it: when applied, the
    # originals its stored match did not account for (none for a whole-original replacement); else its match's
    kept_rows: tuple[str, ...] = ()
    # R8-B revised — a void's reading (``void_reading``), and what the SYSTEM records for it now (the sweep's
    # input; ``None`` = nothing — the operator's, or inert)
    void: VoidReading | None = None
    resolution: SystemResolution | None = None

    @property
    def unresolved(self) -> bool:
        return self.match is not None and self.match.unresolved

    @property
    def held(self) -> bool:
        """The system kept the original of a retraction it cannot place (``system:held``) — owed."""
        return self.verdict_basis == BASIS_HELD

    @property
    def reading(self) -> str | None:
        """How the filer's words settled it: a row match's reading, or a void's kind."""
        if self.match is not None:
            return self.match.reading
        return self.void.kind if self.void is not None else None

    @property
    def passage(self) -> str | None:
        """The filer's words that settled it (the located passage shown on Admin)."""
        if self.match is not None:
            return self.match.passage
        return self.void.passage if self.void is not None else None

    @property
    def needs_operator(self) -> bool:
        """OWED an operator decision (R8-B): an ambiguous link touching a signal row inside the review horizon,
        an operator-linked one awaiting its verdict, or one the operator took back with an Undo. Never a
        system application, never a historical ambiguous one."""
        return self.review == REVIEW_OWED

    @property
    def system_basis(self) -> str | None:
        """The basis of the standing SYSTEM application, or ``None`` (not applied by the system, or a
        correction applied by its link alone — a pre-R8-B link the sweep has not recorded yet)."""
        return self.verdict_basis if is_system_basis(self.verdict_basis) else None


def _lock(conn: psycopg.Connection, security_id: UUID, accession: str) -> list[dict[str, Any]]:
    """Lock every link version of one amendment for the caller's transaction; ``LookupError`` if none."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_link WHERE security_id = %s AND amendment_accession = %s "
            "ORDER BY recorded_at, id FOR UPDATE",
            (security_id, accession),
        )
        rows = cur.fetchall()
    if not rows:
        raise LookupError(f"no Form 4/A {accession} linked for security {security_id}")
    return rows


def _decisions(conn: psycopg.Connection, security_id: UUID, accession: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_decision WHERE security_id = %s AND amendment_accession = %s "
            "ORDER BY recorded_at, id",
            (security_id, accession),
        )
        return cur.fetchall()


def _state(
    links: list[dict[str, Any]], decisions: list[dict[str, Any]], accession: str
) -> tuple[Effective, dict[str, Any] | None, dict[str, Any] | None]:
    link = latest_by_accession(links, "amendment_accession")[accession]
    link_d, verdict = standing_decisions(decisions).get(accession, (None, None))
    return effect_of(link, link_d, verdict), link_d, verdict


def _amendment_rows(
    conn: psycopg.Connection, security_id: UUID, accession: str
) -> list[dict[str, Any]]:
    """The 4/A's Table I rows as filed (its newest stored version)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rows FROM insider_amendment WHERE security_id = %s AND accession = %s "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (security_id, accession),
        )
        row = cur.fetchone()
    return list((row or {}).get("rows") or [])


def _insert(conn: psycopg.Connection, values: dict[str, Any]) -> dict[str, Any]:
    cols = list(values)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO insider_amendment_decision ({', '.join(cols)}) "  # noqa: S608 — fixed columns
            f"VALUES ({', '.join('%s' for _ in cols)}) RETURNING *",
            [Json(v) if isinstance(v, dict) else v for v in values.values()],
        )
        return cur.fetchone()


def decide(
    conn: psycopg.Connection,
    security_id: UUID,
    accession: str,
    action: str,
    *,
    reason: str,
    basis: str,
) -> dict[str, Any]:
    """Append the operator's verdict on one amendment (R8-B revised, 2026-10-21) — one of three, each undoable:

    - ``apply`` — **Use the correction**: the amendment as filed REPLACES its whole original (a void / duplicates
      removes its targets) — stored with ``replaced_rows`` NULL;
    - ``keep`` — **Keep the original**: the amendment changes nothing;
    - ``add`` — **Add to the original**: every original row stands and the amendment's rows are added — stored
      as an ``apply`` that replaces nothing (``replaced_rows = []``); a partial / consolidation / addition /
      restatement / field change only (a zero-row 4/A has nothing to add).

    An operator verdict may stand OVER a standing SYSTEM decision (the system's apply or keep stays on record;
    the newest standing verdict wins, so revoking the operator's restores the system's) — never over another
    operator verdict (``AmendmentConflict`` — undo it first). ``LookupError`` for an unknown amendment.
    """
    if action not in (ACTION_APPLY, ACTION_KEEP, ACTION_ADD):
        raise ValueError(f"action must be one of apply / keep / add, got {action!r}")
    if not reason.strip() or not basis.strip():
        raise ValueError("a decision needs a reason and a basis")
    links = _lock(conn, security_id, accession)
    eff, link_d, verdict = _state(links, _decisions(conn, security_id, accession), accession)
    over_system = verdict is not None and is_system_basis(verdict.get("basis"))
    if verdict is not None and not over_system:
        raise AmendmentConflict(
            f"{accession}: your '{verdict['action']}' decision already stands — undo it first"
        )
    disp = eff.disposition
    if disp == AMBIGUOUS and link_d is None:
        raise AmendmentConflict(
            f"{accession} is ambiguous — link it to one of its candidates before ratifying"
        )
    if disp in INERT_DISPOSITIONS:
        raise AmendmentConflict(f"{accession} is {disp} — there is nothing to ratify")
    if disp in AUTO_DISPOSITIONS and action != ACTION_KEEP:
        raise AmendmentConflict(f"{accession} is an auto-applied correction — it already applies")
    if action == ACTION_ADD and disp not in ROUTED_NO_PROVISIONAL:
        raise AmendmentConflict(f"{accession} is a {disp} — it has no rows to add")
    if disp not in ROUTED_DISPOSITIONS and disp not in AUTO_DISPOSITIONS:
        raise AmendmentConflict(f"{accession}: unknown disposition {disp!r}")
    sec = links[-1]
    return _insert(
        conn,
        {
            "tenant_id": sec["tenant_id"],
            "security_id": security_id,
            "amendment_accession": accession,
            "action": ACTION_APPLY if action == ACTION_ADD else action,
            "reason": reason.strip(),
            "basis": basis,
            "replaced_rows": [] if action == ACTION_ADD else None,
            "detail": {
                "disposition": disp,
                "status_before": eff.status,
                "verdict": action,
                # the system decision this verdict stands over (its Undo restores it) — R8-B
                **({"over_system_decision": str(verdict["id"])} if over_system else {}),
            },
        },
    )


def link_candidate(
    conn: psycopg.Connection,
    security_id: UUID,
    accession: str,
    target: str,
    *,
    reason: str,
    basis: str,
    signal_codes: Collection[str] | None = None,
) -> dict[str, Any]:
    """Link an AMBIGUOUS amendment to the candidate original ``target``: the disposition against the target's
    full stored rows is computed now (with the signal codes — a role change on a signal row is a
    ``field_change``, never clerical) and stored on the decision. ``AmendmentConflict`` unless the amendment
    is ambiguous, unlinked by the operator, and ``target`` is one of its candidates."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    if not reason.strip() or not basis.strip():
        raise ValueError("a link needs a reason and a basis")
    links = _lock(conn, security_id, accession)
    link = latest_by_accession(links, "amendment_accession")[accession]
    _, link_d, _ = _state(links, _decisions(conn, security_id, accession), accession)
    if link["disposition"] != AMBIGUOUS:
        raise AmendmentConflict(
            f"{accession} is not ambiguous ({link['disposition']}) — nothing to link"
        )
    if link_d is not None:
        raise AmendmentConflict(
            f"{accession} is already linked to {link_d['target_accession']} — undo that link first"
        )
    if target not in (link["candidate_accessions"] or []):
        raise AmendmentConflict(f"{target} is not a candidate original of {accession}")
    amend_rows = _amendment_rows(conn, security_id, accession)
    held = stored_rows_by_accession(conn, security_id, tenant_id=link["tenant_id"])
    disposition, originals = link_to_candidate(amend_rows, target, held.get(target, []), codes)
    return _insert(
        conn,
        {
            "tenant_id": link["tenant_id"],
            "security_id": security_id,
            "amendment_accession": accession,
            "action": ACTION_LINK,
            "target_accession": target,
            "disposition": disposition,
            "original_accessions": list(originals),
            "reason": reason.strip(),
            "basis": basis,
            "detail": {"candidates": list(link["candidate_accessions"] or [])},
        },
    )


def revoke(
    conn: psycopg.Connection, decision_id: UUID, *, reason: str, basis: str
) -> dict[str, Any]:
    """Undo one STANDING decision by appending a ``revoke`` row naming it. ``LookupError`` when no standing
    decision has that id; ``AmendmentConflict`` when it is a link a verdict still stands under."""
    if not reason.strip() or not basis.strip():
        raise ValueError("a revoke needs a reason and a basis")
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment_decision WHERE id = %s", (decision_id,))
        target = cur.fetchone()
    if target is None or target["action"] == "revoke":
        raise LookupError(f"no decision with id {decision_id}")
    links = _lock(conn, target["security_id"], target["amendment_accession"])
    decisions = _decisions(conn, target["security_id"], target["amendment_accession"])
    if any(d["revokes"] == decision_id for d in decisions):
        raise LookupError(f"decision {decision_id} was already undone")
    _, link_d, verdict = _state(links, decisions, target["amendment_accession"])
    if target["action"] == ACTION_LINK and verdict is not None:
        raise AmendmentConflict(
            f"a '{verdict['action']}' decision stands under this link — undo that one first"
        )
    if target["action"] in VERDICTS and (verdict is None or verdict["id"] != decision_id):
        raise LookupError(f"decision {decision_id} is not the standing verdict")
    if target["action"] == ACTION_LINK and (link_d is None or link_d["id"] != decision_id):
        raise LookupError(f"decision {decision_id} is not the standing link")
    return _insert(
        conn,
        {
            "tenant_id": target["tenant_id"],
            "security_id": target["security_id"],
            "amendment_accession": target["amendment_accession"],
            "action": "revoke",
            "revokes": decision_id,
            "reason": reason.strip(),
            "basis": basis,
        },
    )


# --- the reads ---------------------------------------------------------------------------------------------


_UNSET: Any = object()


def items(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID] | None = None,
    asof: date | None = None,
    horizons: Mapping[str, int | None] | None = None,
    review_days: int | None = _UNSET,
) -> list[AmendmentItem]:
    """Every linked amendment (optionally of these securities) with its CURRENT effective state and where it
    sits in the operator's review (R8-B ``review``) — the Admin panel's whole read, the nightly scan's and the
    sweep's. Ordered by URGENCY: owed a decision AND changing a signal row inside a signal's live read horizon
    at ``asof`` first (``in_window``), then the rest by ticker and acceptance (newest first). ``horizons`` is
    ``{code: days}`` from the detectors' own declarations (``signals.horizons.insider_signal_codes``; its keys
    are the signal codes) — derived when omitted; without ``asof`` nothing is in-window. ``review_days`` is the
    review horizon (``review_horizon_days()`` when omitted; ``None`` = unbounded) — without ``asof`` an
    ambiguous amendment is judged against today's market day. Read-only."""
    if horizons is None:
        horizons = default_horizons()
    if review_days is _UNSET:
        review_days = review_horizon_days()
    codes = frozenset(horizons)
    if asof is None:
        from domain.market_time import market_today  # lazy: the review horizon needs SOME asof

        review_asof = market_today()
    else:
        review_asof = asof
    sid_filter = list(security_ids) if security_ids is not None else None
    where = "" if sid_filter is None else " WHERE x.security_id = ANY(%(ids)s)"
    p: dict[str, Any] = {"ids": sid_filter}
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment_link x" + where, p)  # noqa: S608
        links = cur.fetchall()
        cur.execute("SELECT * FROM insider_amendment_decision x" + where, p)  # noqa: S608
        decisions = cur.fetchall()
        cur.execute(
            "SELECT DISTINCT ON (x.security_id, x.accession) x.*, sm.ticker FROM insider_amendment x "
            "JOIN security_master sm ON sm.id = x.security_id"
            + where
            + " ORDER BY x.security_id, x.accession, x.recorded_at DESC, x.id DESC",  # noqa: S608
            p,
        )
        filings = {(r["security_id"], r["accession"]): r for r in cur.fetchall()}
    by_sid_links: dict[UUID, list[dict[str, Any]]] = {}
    for r in links:
        by_sid_links.setdefault(r["security_id"], []).append(r)
    by_sid_dec: dict[UUID, list[dict[str, Any]]] = {}
    for d in decisions:
        by_sid_dec.setdefault(d["security_id"], []).append(d)
    out: list[AmendmentItem] = []
    held_cache: dict[UUID, dict[str, list[dict[str, Any]]]] = {}
    for sid, sid_links in by_sid_links.items():
        latest = latest_by_accession(sid_links, "amendment_accession")
        standing = standing_decisions(by_sid_dec.get(sid, []))
        for acc, link in latest.items():
            f = filings.get((sid, acc))
            if f is None:
                continue  # a link without its filing record cannot be shown honestly
            link_d, verdict = standing.get(acc, (None, None))
            eff = effect_of(link, link_d, verdict)
            if sid not in held_cache:
                held_cache[sid] = stored_rows_by_accession(conn, sid, tenant_id=link["tenant_id"])
            wanted = (
                set(eff.candidate_accessions)
                | set(eff.original_accessions)
                | set(eff.voided_accessions)
            )
            amend_rows = list(f.get("rows") or [])
            # R8-B — the row match against the FULL stored originals; what an APPLIED one replaces is what its
            # decision stored (whole originals for a pre-R8-B decision), else what the match accounts for
            text = filer_text(f.get("remarks"), f.get("footnotes"))
            match = match_for(
                eff.disposition, eff.original_accessions, held_cache[sid], amend_rows, codes, text
            )
            void = void_for(eff.disposition, eff.original_accessions, held_cache[sid], codes, text)
            replaced: tuple[str, ...] | None
            kept: tuple[str, ...] = ()
            if match is None:
                replaced = None
            elif eff.status == STATUS_APPLIED:
                replaced = None if eff.suppress else eff.suppress_rows
                if replaced is not None:
                    gone = set(replaced)
                    kept = tuple(k for k in sorted((*match.replaced, *match.kept)) if k not in gone)
            else:
                replaced = None if match.unresolved else match.replaced
                kept = match.kept
            before, after = before_after(
                eff.disposition,
                original_accessions=eff.original_accessions,
                voided_accessions=eff.voided_accessions,
                candidate_accessions=eff.candidate_accessions,
                stored=held_cache[sid],
                amend_rows=amend_rows,
                replaced=replaced,
            )
            changed = changed_signal_rows(eff.disposition, before, after, codes)
            own = [d for d in by_sid_dec.get(sid, []) if d["amendment_accession"] == acc]
            unresolved = match is not None and match.unresolved
            in_review = within_review_horizon(
                match.conflicts if unresolved else changed, review_asof, review_days
            )
            review = review_bucket(
                eff,
                verdict_basis=verdict.get("basis") if verdict is not None else None,
                # "the operator took it back" — an OPERATOR decision ever recorded; the system's own revoke
                # of a decision it re-linked away from (``system:relink``) is not the operator's Undo
                ever_decided=any(not is_system_basis(d.get("basis")) for d in own),
                in_review=in_review,
                unresolved=unresolved,
            )
            resolution = system_resolution(
                eff.disposition, signal_effect=bool(changed), match=match, void=void
            )
            out.append(
                AmendmentItem(
                    security_id=sid,
                    ticker=f.get("ticker"),
                    accession=acc,
                    issuer_cik=f.get("issuer_cik"),
                    filed=f.get("filed"),
                    accepted=f.get("accepted"),
                    recorded_at=f["recorded_at"],
                    owners=list(f.get("owners") or []),
                    date_of_original_submission=f.get("date_of_original_submission"),
                    remarks=f.get("remarks"),
                    footnotes=list(f.get("footnotes") or []),
                    rows=list(f.get("rows") or []),
                    derivative_count=int(f.get("derivative_count") or 0),
                    effective=eff,
                    why=str((link.get("detail") or {}).get("why") or ""),
                    link_recorded_at=link["recorded_at"],
                    decisions=sorted(own, key=lambda d: (d["recorded_at"], str(d["id"]))),
                    originals={a: held_cache[sid].get(a, []) for a in sorted(wanted)},
                    signal_effect=bool(changed),
                    auto_resolved=review == REVIEW_AUTO,
                    in_window=(
                        in_window_codes(changed, asof, horizons) if asof is not None else ()
                    ),
                    touches_buy=any(r.get("txn_code") == "P" for r in (*before, *after)),
                    verdict_basis=(
                        str(verdict["basis"])
                        if verdict is not None and eff.verdict is not None
                        else None
                    ),
                    in_review=in_review,
                    review=review,
                    match=match,
                    kept_rows=kept,
                    void=void,
                    resolution=resolution,
                )
            )
    out.sort(
        key=lambda i: (
            not (i.needs_operator and i.in_window),
            i.ticker or "",
            -(i.accepted.timestamp() if i.accepted else i.recorded_at.timestamp()),
            i.accession,
        )
    )
    return out


def default_horizons() -> dict[str, int | None]:
    """``{code: days}`` a CALL signal reads from ``fact_insider_txn`` — the registered detectors' own
    declarations under the config the cron runs (lazy: the detector registry sits above the repository).
    """
    from domain.config import DEFAULT_CONFIG
    from signals.horizons import insider_signal_codes

    return insider_signal_codes(DEFAULT_CONFIG)


def review_horizon_days() -> int | None:
    """R8-B — the review horizon in force: the ``insider_amendment_review_horizon_days`` setting when set (a
    negative value reads as 0), else the DERIVED default — the backtest window plus the longest detector read
    horizon on ``fact_insider_txn`` (``signals.horizons.insider_amendment_review_horizon``; ``None`` =
    unbounded). Lazy imports: the detector registry sits above the repository."""
    from domain.config import DEFAULT_CONFIG
    from domain.settings import get_settings
    from signals.horizons import insider_amendment_review_horizon

    pinned = get_settings().insider_amendment_review_horizon_days
    if pinned is not None:
        return max(0, int(pinned))
    return insider_amendment_review_horizon(DEFAULT_CONFIG)


def pending_keys(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID],
    asof: date | None = None,
    horizons: Mapping[str, int | None] | None = None,
    review_days: int | None = _UNSET,
) -> dict[str, AmendmentItem]:
    """``"<security_id>:<accession>" -> item`` for every amendment OWED an operator decision among these
    securities — the nightly scan's inventory (it pages the NEW ones once), urgency-ordered (``items``). R8-B:
    only ambiguous links that touch a signal row inside the review horizon (plus operator-linked ones awaiting
    a verdict, and any the operator took back) — a system application and a historical ambiguous link are
    never here and never paged."""
    return {
        f"{i.security_id}:{i.accession}": i
        for i in items(
            conn, security_ids=security_ids, asof=asof, horizons=horizons, review_days=review_days
        )
        if i.needs_operator
    }


# --- the SYSTEM's sweep (R8-B) ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepResult:
    """One sweep's outcome: the decisions it appended, by basis and by disposition (``applied`` = keys
    ``"<security_id>:<accession>"``), and how many it considered."""

    considered: int = 0
    applied: tuple[str, ...] = ()
    by_basis: dict[str, int] = field(default_factory=dict)
    by_disposition: dict[str, int] = field(default_factory=dict)


def sweep_plan(found: Iterable[AmendmentItem]) -> list[tuple[AmendmentItem, SystemResolution]]:
    """PURE over ``items`` output — ``(item, resolution)`` for every amendment the system resolves now: no
    decision ever recorded on it, and a system resolution (``domain.insider_amendments.system_resolution`` —
    an ``apply`` carrying its row match, or a void's ``keep``). An ambiguous link, an unresolved row match, an
    inert one, and anything the operator (or the system) ever decided are left exactly as they are.
    """
    out: list[tuple[AmendmentItem, SystemResolution]] = []
    for it in found:
        res = sweep_resolution(
            it.effective, ever_decided=bool(it.decisions), resolution=it.resolution
        )
        if res is not None:
            out.append((it, res))
    return out


def sweep(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID] | None = None,
    signal_codes: Collection[str] | None = None,
) -> SweepResult:
    """R8-B — APPLY every resolvable amendment a pre-R8-B ingest left without a decision: append one SYSTEM
    ``apply`` each (basis ``system:filer-correction``, or ``system:no-signal-effect`` when it changes no signal
    row — ``domain.insider_amendments.system_basis``; a matched disposition carries its row match, so only the
    original rows it accounts for are replaced), stamped ``recorded_at = now()`` — so a read pinned
    before tonight still sees the state the system held then (pending; a void suppressing): no rewrite of the
    past. Every security when ``security_ids`` is None (the resolver reads amendments for any security).

    IDEMPOTENT — re-running appends nothing: an amendment is swept only while it has NO decision row at all,
    re-checked under the amendment's ``FOR UPDATE`` lock (so a click racing the sweep wins or waits, never
    doubles). Never touches an operator decision, an operator link, or an Undo (an amendment the operator took
    back stays the operator's). The caller owns the transaction."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    # the plan is time-free (no asof, an unbounded review horizon): WHETHER the system applies an amendment
    # depends on its disposition and decisions only, never on a date window
    found = items(
        conn, security_ids=security_ids, horizons={c: None for c in codes}, review_days=None
    )
    applied: list[str] = []
    by_basis: dict[str, int] = {}
    by_disp: dict[str, int] = {}
    for it, res in sweep_plan(found):
        links = _lock(conn, it.security_id, it.accession)
        decisions = _decisions(conn, it.security_id, it.accession)
        eff, _, _ = _state(links, decisions, it.accession)
        if decisions or eff.disposition != it.effective.disposition:
            continue  # decided (or re-linked) since the read: the newer state wins
        v = system_decision_values(
            tenant_id=links[-1]["tenant_id"],
            security_id=it.security_id,
            accession=it.accession,
            basis=res.basis,
            disposition=eff.disposition,
            signal_codes=codes,
            via="sweep",
            replaced_rows=res.replaced_rows,
            action=res.action,
        )
        _insert(conn, v)
        applied.append(f"{it.security_id}:{it.accession}")
        by_basis[res.basis] = by_basis.get(res.basis, 0) + 1
        by_disp[eff.disposition] = by_disp.get(eff.disposition, 0) + 1
    return SweepResult(
        considered=len(found),
        applied=tuple(applied),
        by_basis=by_basis,
        by_disposition=by_disp,
    )


# --- the R8 linker fix's RE-LINK (2026-10-21) ---------------------------------------------------------------


# one security's submissions index as the linker reads it: ``({accession: filed}, {accession: accepted})``
FilingIndex = tuple[Mapping[str, date], Mapping[str, datetime]]


@dataclass(frozen=True)
class RelinkItem:
    """One stored link the fixed cascade moves (``domain.insider_amendments.relink_rule``): the stored link
    row it supersedes, the fresh ``LinkResult``, the rule, and the standing SYSTEM verdict it takes back
    (computed against the wrong original) — ``None`` when nothing stands."""

    security_id: UUID
    ticker: str | None
    accession: str
    rule: str
    old: Mapping[str, Any]
    new: LinkResult
    revoke: Mapping[str, Any] | None
    rejected: int = 0


@dataclass
class RelinkPlan:
    """What a re-link pass would do: ``items`` to append; ``operator_held`` the amendments the rule would move
    but the operator decided or linked (never touched — the operator's); ``drift`` the stored links the fresh
    cascade reads differently for ANOTHER reason (reported, never re-linked here); ``no_index`` the securities
    with no filing-date index to judge by (not judged)."""

    considered: int = 0
    items: list[RelinkItem] = field(default_factory=list)
    operator_held: list[RelinkItem] = field(default_factory=list)
    drift: list[tuple[str | None, str, tuple[Any, ...], tuple[Any, ...]]] = field(
        default_factory=list
    )
    no_index: list[tuple[str | None, UUID, int]] = field(default_factory=list)


def facts_from_record(filing: Mapping[str, Any]) -> AmendmentFacts:
    """A STORED 4/A (``insider_amendment``, as filed) as the linker reads it — the same facts the ingest built
    from the XML: every reporting owner, ``dateOfOriginalSubmission``, the filing date and acceptance, the
    kept Table I rows, and the filer's own words."""
    return AmendmentFacts(
        accession=str(filing["accession"]),
        owners=tuple(
            (norm_cik(o.get("cik")), (o.get("name") or None)) for o in (filing.get("owners") or [])
        ),
        date_of_original_submission=filing.get("date_of_original_submission"),
        filed=filing.get("filed"),
        accepted=filing.get("accepted"),
        rows=tuple(filing.get("rows") or []),
        text=filer_text(filing.get("remarks"), filing.get("footnotes")),
    )


def relink_plan(
    conn: psycopg.Connection,
    *,
    index: Callable[[UUID, str | None], FilingIndex | None],
    security_ids: Iterable[UUID] | None = None,
    signal_codes: Collection[str] | None = None,
) -> RelinkPlan:
    """READ-ONLY — re-run the FIXED cascade over every stored amendment (the latest link of each) against the
    security's stored Form 4 rows (``stored_rows_by_accession`` — the ingest's own input) and its filing-date
    index (``index(security_id, cik)`` -> ``({accession: filed}, {accession: accepted})`` — the submissions
    index the ingest reads; ``None`` = not available, the security is not judged). A link moves only by
    ``relink_rule`` (made public after the amendment; the row tie-break) and only when NOTHING the operator did
    touches the amendment."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    sid_filter = list(security_ids) if security_ids is not None else None
    where = "" if sid_filter is None else " WHERE x.security_id = ANY(%(ids)s)"
    p: dict[str, Any] = {"ids": sid_filter}
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment_link x" + where, p)  # noqa: S608
        links = cur.fetchall()
        cur.execute("SELECT * FROM insider_amendment_decision x" + where, p)  # noqa: S608
        decisions = cur.fetchall()
        cur.execute(
            "SELECT DISTINCT ON (x.security_id, x.accession) x.*, sm.ticker, sm.cik AS member_cik "
            "FROM insider_amendment x JOIN security_master sm ON sm.id = x.security_id"
            + where
            + " ORDER BY x.security_id, x.accession, x.recorded_at DESC, x.id DESC",  # noqa: S608
            p,
        )
        filings = {(r["security_id"], r["accession"]): r for r in cur.fetchall()}
    by_sid: dict[UUID, list[dict[str, Any]]] = {}
    for r in links:
        by_sid.setdefault(r["security_id"], []).append(r)
    dec_by: dict[tuple[UUID, str], list[dict[str, Any]]] = {}
    for d in decisions:
        dec_by.setdefault((d["security_id"], d["amendment_accession"]), []).append(d)
    plan = RelinkPlan()
    for sid in sorted(by_sid, key=str):
        latest = latest_by_accession(by_sid[sid], "amendment_accession")
        recs = [filings.get((sid, acc)) for acc in latest]
        first = next((f for f in recs if f is not None), None)
        if first is None:
            continue
        idx = index(sid, first.get("member_cik"))
        if idx is None:
            plan.no_index.append((first.get("ticker"), sid, len(latest)))
            continue
        held = stored_rows_by_accession(conn, sid, tenant_id=first["tenant_id"])
        for acc in sorted(latest):
            f = filings.get((sid, acc))
            if f is None:
                continue  # a link without its filing record cannot be judged honestly
            plan.considered += 1
            link = latest[acc]
            fresh = link_amendment(facts_from_record(f), held, idx[0], codes, idx[1])
            rule = relink_rule(link, fresh)
            if rule is None:
                if link_identity(link) != link_identity(fresh):
                    plan.drift.append(
                        (f.get("ticker"), acc, link_identity(link), link_identity(fresh))
                    )
                continue
            own = dec_by.get((sid, acc), [])
            _, verdict = standing_decisions(own).get(acc, (None, None))
            item = RelinkItem(
                security_id=sid,
                ticker=f.get("ticker"),
                accession=acc,
                rule=rule,
                old=link,
                new=fresh,
                revoke=verdict,
                rejected=int((link.get("detail") or {}).get("rejected_rows") or 0),
            )
            if any(not is_system_basis(d.get("basis")) for d in own):
                plan.operator_held.append(item)  # anything the operator did stays the operator's
            else:
                plan.items.append(item)
    return plan


@dataclass(frozen=True)
class RelinkResult:
    """What ``relink`` appended: link versions, the system's revokes of its own stale decisions, and the
    system's decisions against the new originals; ``skipped`` = planned items the state moved under (re-checked
    under the amendment's lock — decided or re-linked since the plan)."""

    links: int = 0
    revokes: int = 0
    decisions: int = 0
    applied: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def relink(
    conn: psycopg.Connection,
    plan: RelinkPlan,
    *,
    signal_codes: Collection[str] | None = None,
) -> RelinkResult:
    """APPEND the planned re-links (the caller owns the transaction): for each item, under the amendment's
    ``FOR UPDATE`` lock and re-checked against the state it was planned on, (1) a NEW link version, (2) a
    SYSTEM ``revoke`` (basis ``system:relink``) of the standing system verdict that was computed against the
    wrong original, (3) the system's decision against the new original when it resolves one (``write_link`` —
    the ingest's own write). All stamped ``recorded_at = now()`` (the transaction's), so a read pinned before
    the re-link still sees exactly what the system held then: no rewrite of the past (#1). Idempotent: once
    applied, the stored link IS the fresh one, so a re-plan finds nothing. Never touches an amendment the
    operator decided or linked."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    n_links = n_rev = n_dec = 0
    applied: list[str] = []
    skipped: list[str] = []
    for it in plan.items:
        key = f"{it.security_id}:{it.accession}"
        links = _lock(conn, it.security_id, it.accession)
        decisions = _decisions(conn, it.security_id, it.accession)
        latest = latest_by_accession(links, "amendment_accession")[it.accession]
        _, verdict = standing_decisions(decisions).get(it.accession, (None, None))
        stale = (
            str(latest["id"]) != str(it.old["id"])
            or any(not is_system_basis(d.get("basis")) for d in decisions)
            or (verdict is None) != (it.revoke is None)
            or (verdict is not None and str(verdict["id"]) != str(it.revoke["id"]))  # type: ignore[index]
        )
        if stale:
            skipped.append(key)
            continue
        relink_detail = {
            "rule": it.rule,
            "from_link": str(it.old["id"]),
            "from": {
                "method": it.old["method"],
                "disposition": it.old["disposition"],
                "original_accessions": list(it.old["original_accessions"] or []),
                "candidate_accessions": list(it.old["candidate_accessions"] or []),
            },
        }
        if verdict is not None:
            _insert(
                conn,
                {
                    "tenant_id": latest["tenant_id"],
                    "security_id": it.security_id,
                    "amendment_accession": it.accession,
                    "action": "revoke",
                    "revokes": verdict["id"],
                    "reason": (
                        "re-linked by the system: this decision was computed against "
                        f"{', '.join(it.old['original_accessions'] or []) or 'no original'}, which is "
                        "not the filing this amendment amends"
                    ),
                    "basis": BASIS_RELINK,
                    "detail": relink_detail,
                },
            )
            n_rev += 1
        with conn.cursor() as cur:
            n_dec += write_link(
                cur,
                tenant_id=latest["tenant_id"],
                security_id=it.security_id,
                accession=it.accession,
                link=it.new,
                signal_codes=codes,
                accepted=latest.get("accepted"),
                rejected=it.rejected,
                via="relink",
                extra_detail={"relink": relink_detail},
            )
        n_links += 1
        applied.append(key)
    return RelinkResult(
        links=n_links,
        revokes=n_rev,
        decisions=n_dec,
        applied=tuple(applied),
        skipped=tuple(skipped),
    )


# --- the R8 RE-DERIVATION of the legacy whole-original decisions (2026-10-21) --------------------------------


@dataclass(frozen=True)
class RederiveItem:
    """One LEGACY system ``apply`` (``replaced_rows`` NULL on a matched disposition — ``is_legacy_whole``) and
    what R8-B records for it instead: its row match against the FULL stored originals and the filer's words
    (``match_rows`` — ``replaced_rows`` it replaces, ``kept_rows`` that stand beside it, ``added`` amendment rows
    that match no original row, the ``reading`` and the ``passage`` that settled the leftovers) and the system
    basis under that match (``signal_effect`` — does it change a row a call signal reads)."""

    security_id: UUID
    ticker: str | None
    accession: str
    disposition: str
    original_accessions: tuple[str, ...]
    stale: Mapping[str, Any]
    replaced_rows: tuple[str, ...]
    kept_rows: tuple[str, ...]
    added: int
    reading: str
    passage: str | None
    basis: str | None
    signal_effect: bool


@dataclass
class RederivePlan:
    """What a re-derivation pass would do. ``items`` = the legacy decisions to re-derive (an original they
    amend reads differently under the row match — ``rederive_scope``); ``unchanged`` = legacy decisions whose
    row match changes no read (left exactly as they are); ``unresolved`` = legacy decisions whose row match is
    UNRESOLVED (R8-B would owe the operator; never moved here — reported); ``operator_held`` = legacy decisions
    on an amendment the operator ever decided (never touched). ``gained`` / ``lost`` = the rows the read gains /
    loses once ``items`` are re-derived, ``(ticker, security_id, row)``."""

    considered: int = 0
    items: list[RederiveItem] = field(default_factory=list)
    unchanged: list[RederiveItem] = field(default_factory=list)
    unresolved: list[RederiveItem] = field(default_factory=list)
    operator_held: list[RederiveItem] = field(default_factory=list)
    gained: list[tuple[str | None, UUID, dict[str, Any]]] = field(default_factory=list)
    lost: list[tuple[str | None, UUID, dict[str, Any]]] = field(default_factory=list)


_FAR_KNOWN_AT = datetime(9999, 12, 31, tzinfo=timezone.utc)


def _full_insider_rows(
    conn: psycopg.Connection, security_id: UUID, tenant_id: UUID
) -> list[dict[str, Any]]:
    """The security's stored insider rows — the latest version of each (the as-of read's natural key), every
    column, no time gate: what a read of the whole history resolves."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (accession, insider_name, valid_from, txn_seq) * FROM fact_insider_txn "
            "WHERE tenant_id = %s AND security_id = %s "
            "ORDER BY accession, insider_name, valid_from, txn_seq, recorded_at DESC, id DESC",
            (tenant_id, security_id),
        )
        return cur.fetchall()


def rederive_plan(
    conn: psycopg.Connection,
    *,
    security_ids: Iterable[UUID] | None = None,
    signal_codes: Collection[str] | None = None,
) -> RederivePlan:
    """READ-ONLY — every LEGACY system ``apply`` (a matched disposition applied with ``replaced_rows`` NULL —
    ``is_legacy_whole``: the first live night wrote them before the row match existed) with what R8-B records for
    it instead (``match_for`` against the FULL stored originals with the filer's words, the basis by the
    signal effect under that match — ``system_resolution``), and the scope: only a decision whose row match
    changes the read of an original it amends is re-derived (``rederive_scope``, over the full history).
    """
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    sid_filter = list(security_ids) if security_ids is not None else None
    where = "" if sid_filter is None else " WHERE x.security_id = ANY(%(ids)s)"
    p: dict[str, Any] = {"ids": sid_filter}
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM insider_amendment_link x" + where, p)  # noqa: S608
        links = cur.fetchall()
        cur.execute("SELECT * FROM insider_amendment_decision x" + where, p)  # noqa: S608
        decisions = cur.fetchall()
        cur.execute(
            "SELECT DISTINCT ON (x.security_id, x.accession) x.*, sm.ticker FROM insider_amendment x "
            "JOIN security_master sm ON sm.id = x.security_id"
            + where
            + " ORDER BY x.security_id, x.accession, x.recorded_at DESC, x.id DESC",  # noqa: S608
            p,
        )
        filings = {(r["security_id"], r["accession"]): r for r in cur.fetchall()}
    by_sid: dict[UUID, list[dict[str, Any]]] = {}
    for r in links:
        by_sid.setdefault(r["security_id"], []).append(r)
    dec_by: dict[UUID, list[dict[str, Any]]] = {}
    for d in decisions:
        dec_by.setdefault(d["security_id"], []).append(d)
    plan = RederivePlan()
    for sid in sorted(by_sid, key=str):
        latest = latest_by_accession(by_sid[sid], "amendment_accession")
        standing = standing_decisions(dec_by.get(sid, []))
        held: dict[str, list[dict[str, Any]]] | None = None
        candidates: list[RederiveItem] = []
        for acc in sorted(latest):
            link = latest[acc]
            link_d, verdict = standing.get(acc, (None, None))
            eff = effect_of(link, link_d, verdict)
            if not is_legacy_whole(eff, verdict):
                continue
            f = filings.get((sid, acc))
            if f is None:
                continue  # a link without its filing record cannot be judged honestly
            plan.considered += 1
            if held is None:
                held = stored_rows_by_accession(conn, sid, tenant_id=link["tenant_id"])
            amend_rows = list(f.get("rows") or [])
            match = match_for(
                eff.disposition,
                eff.original_accessions,
                held,
                amend_rows,
                codes,
                filer_text(f.get("remarks"), f.get("footnotes")),
            )
            assert match is not None  # a matched disposition (``is_legacy_whole``)
            before, after = before_after(
                eff.disposition,
                original_accessions=eff.original_accessions,
                voided_accessions=eff.voided_accessions,
                candidate_accessions=eff.candidate_accessions,
                stored=held,
                amend_rows=amend_rows,
                replaced=None if match.unresolved else match.replaced,
            )
            effect = bool(changed_signal_rows(eff.disposition, before, after, codes))
            res = system_resolution(eff.disposition, signal_effect=effect, match=match)
            item = RederiveItem(
                security_id=sid,
                ticker=f.get("ticker"),
                accession=acc,
                disposition=eff.disposition,
                original_accessions=eff.original_accessions,
                stale=verdict,  # type: ignore[arg-type]  # ``is_legacy_whole`` = a standing verdict
                replaced_rows=match.replaced,
                kept_rows=match.kept,
                added=match.added,
                reading=match.reading,
                passage=match.passage,
                basis=res.basis if res is not None else None,
                signal_effect=effect,
            )
            own = [d for d in dec_by.get(sid, []) if d["amendment_accession"] == acc]
            if any(not is_system_basis(d.get("basis")) for d in own):
                plan.operator_held.append(item)  # anything the operator did stays the operator's
            elif res is None:
                plan.unresolved.append(item)  # R8-B would owe the operator: never moved here
            else:
                candidates.append(item)
        if not candidates:
            continue
        tenant = latest[candidates[0].accession]["tenant_id"]
        inputs = load_inputs(conn, [sid], known_at=_FAR_KNOWN_AT, tenant_id=tenant)
        key = str(sid)
        picked, gained, lost = rederive_scope(
            _full_insider_rows(conn, sid, tenant),
            amendments=inputs.amendments.get(key, []),
            links=inputs.links.get(key, []),
            decisions=inputs.decisions.get(key, []),
            replaced={str(c.stale["id"]): c.replaced_rows for c in candidates},
            originals_of={str(c.stale["id"]): c.original_accessions for c in candidates},
            own_ciks=inputs.own_ciks.get(key, ()),
        )
        ticker = candidates[0].ticker
        for c in candidates:
            (plan.items if str(c.stale["id"]) in picked else plan.unchanged).append(c)
        plan.gained.extend((ticker, sid, r) for r in gained)
        plan.lost.extend((ticker, sid, r) for r in lost)
    return plan


@dataclass(frozen=True)
class RederiveResult:
    """What ``rederive`` appended: the system's revokes of its legacy decisions and the system's re-derived
    applications; ``skipped`` = planned items the state moved under (re-checked under the amendment's lock).
    """

    revokes: int = 0
    decisions: int = 0
    applied: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def rederive(
    conn: psycopg.Connection,
    plan: RederivePlan,
    *,
    signal_codes: Collection[str] | None = None,
) -> RederiveResult:
    """APPEND the planned re-derivations (the caller owns the transaction): for each item, under the amendment's
    ``FOR UPDATE`` lock and re-checked against the state it was planned on (the legacy decision is still the
    standing verdict, still NULL, and the operator never decided the amendment), (1) the SYSTEM's ``revoke``
    (basis ``system:rederive``) of the legacy decision, (2) the system's ``apply`` carrying the row match
    (``system_decision_values`` — the ingest's and the sweep's own shape, ``via`` = ``rederive``). Both stamped
    ``recorded_at = now()`` (the transaction's), so a read pinned before sees exactly what the system held then:
    no rewrite of the past (#1). Idempotent: the re-derived decision carries its row match, so a re-plan finds
    nothing."""
    codes = frozenset(signal_codes) if signal_codes is not None else frozenset(default_horizons())
    n_rev = n_dec = 0
    applied: list[str] = []
    skipped: list[str] = []
    for it in plan.items:
        key = f"{it.security_id}:{it.accession}"
        links = _lock(conn, it.security_id, it.accession)
        decisions = _decisions(conn, it.security_id, it.accession)
        _, verdict = standing_decisions(decisions).get(it.accession, (None, None))
        if (
            verdict is None
            or str(verdict["id"]) != str(it.stale["id"])
            or verdict.get("replaced_rows") is not None
            or any(not is_system_basis(d.get("basis")) for d in decisions)
        ):
            skipped.append(key)
            continue
        tenant = links[-1]["tenant_id"]
        detail = {
            "rule": "row-match",
            "from_decision": str(it.stale["id"]),
            "reading": it.reading,
            "passage": it.passage,
            "replaced_rows": list(it.replaced_rows),
            "kept_rows": list(it.kept_rows),
            "added": it.added,
        }
        _insert(
            conn,
            {
                "tenant_id": tenant,
                "security_id": it.security_id,
                "amendment_accession": it.accession,
                "action": "revoke",
                "revokes": it.stale["id"],
                "reason": (
                    "re-derived by the system: this decision replaced the WHOLE original (written before the "
                    "row match existed); the filer's correction replaces only the rows it accounts for"
                ),
                "basis": BASIS_REDERIVE,
                "detail": detail,
            },
        )
        n_rev += 1
        v = system_decision_values(
            tenant_id=tenant,
            security_id=it.security_id,
            accession=it.accession,
            basis=it.basis or str(it.stale["basis"]),
            disposition=it.disposition,
            signal_codes=codes,
            via="rederive",
            replaced_rows=it.replaced_rows,
            action=ACTION_APPLY,
        )
        v["detail"] = {**v["detail"], "rederive": detail}
        _insert(conn, v)
        n_dec += 1
        applied.append(key)
    return RederiveResult(
        revokes=n_rev, decisions=n_dec, applied=tuple(applied), skipped=tuple(skipped)
    )


def system_applied_since(conn: psycopg.Connection, since: datetime) -> dict[str, int]:
    """``{basis: n}`` of the SYSTEM decisions (applications and keeps) recorded at/after ``since`` (any
    security) — the cron's quiet count line ("N applied automatically, K kept as filed"). Read-only.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT basis, count(*) AS n FROM insider_amendment_decision "
            "WHERE action IN ('apply', 'keep') AND basis LIKE 'system:%%' AND recorded_at >= %s "
            "GROUP BY basis",
            (since,),
        )
        return {r["basis"]: int(r["n"]) for r in cur.fetchall()}

"""Form 4/A — the AMENDMENT leg (R8-A): parse an amended ownership document, store it AS FILED, and link it to
the original Form 4 it amends (``domain.insider_amendments`` holds the pure rules; this module is the I/O).

An amendment is never written into ``fact_insider_txn``: its rows would land BESIDE the original's (the
natural key carries the accession), so a clerical 4/A would double-count and a zero-row void would remove
nothing. It is stored in ``insider_amendment`` (the filing as filed: owners, ``dateOfOriginalSubmission``,
remarks, footnotes, its Table I rows) with an append-only ``insider_amendment_link`` naming the original(s),
the link method, the disposition and the system status, both stamped in the caller's transaction (one
``recorded_at``). Every as-of read of ``fact_insider_txn`` resolves the stored rows against them
(``db.insider_resolution`` / ``replay.pit``) — so nothing about a past replay moves: a link recorded tonight
is invisible to a read pinned before tonight.

Two screens, both deterministic over the filing itself:

- **The other-issuer screen** — the XML's ``issuerCik`` is not the member's CIK: the member was the
  REPORTING OWNER of another company's stock. Not this security's insider flow, never stored; returned as a
  ``form4_other_issuer`` skip (Q18 — known skips are counted, not re-printed). The same screen guards the
  Form 4 leg (``ingest_form4(member_cik=...)``).
- **The transaction-date sanity bound** (P1) — the Form 4 leg's ``implausible_txn_date``, imported, so an
  amended row whose date cannot be true is rejected by the SAME rule as an original one (printed in full,
  never stored, never clamped — #3).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.session import DEFAULT_TENANT_ID
from domain.insider_amendments import (
    BASIS_FILER_CORRECTION,
    BASIS_HELD,
    BASIS_NO_RETRACTION,
    BASIS_NO_SIGNAL_EFFECT,
    STATUS_PENDING,
    AmendmentFacts,
    LinkResult,
    filer_text,
    link_amendment,
    norm_cik,
)
from ingest.edgar.form4 import _txn_date, implausible_txn_date, parse_form4
from ingest.edgar.submissions import (
    filed_on_or_after,
    filings_of,
    form4_doc_url,
    form4_filings,
    parse_acceptance,
)
from ingest.skips import (
    FORM4_OTHER_ISSUER,
    FORM4_UNPARSEABLE,
    IngestSkip,
    clip_reason,
    is_known,
)

AMENDMENT_FORM = "4/A"


def form4_leg_filings(
    submissions: Mapping[str, Any], *, filed_since: date | None = None
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """THE ONE RULE for which filing documents the Form 4 leg reads — ``(originals, amendments)`` — shared by
    the leg itself (``pipeline.ingest_thesis._form4_leg``) and the fetch-ahead that pre-fetches them
    (``pipeline.fetch_ahead``), so the two can never read different documents.

    The normal walk (``filed_since`` None): every Form 4 and every Form 4/A in the submissions index. X1 — a
    name still ONBOARDING (``filed_since`` set): only the Form 4s filed on/after that date
    (``submissions.filed_on_or_after``) and NO 4/A at all — a 4/A links to the original it amends, which may be
    older than the window and so not stored yet; the onboarding job's full walk stores the originals, then the
    4/As, in order. The caller still skips the accessions it has already stored."""
    subs = dict(submissions)
    originals = filed_on_or_after(form4_filings(subs), filed_since)
    amendments = [] if filed_since is not None else filings_of(subs, AMENDMENT_FORM)
    return originals, amendments


@dataclass(frozen=True)
class ParsedAmendment:
    """One 4/A ownership document, as filed."""

    document_type: str | None
    issuer_cik: str | None
    issuer_name: str | None
    owners: tuple[tuple[str | None, str | None], ...]
    date_of_original_submission: date | None
    period_of_report: date | None
    remarks: str | None
    footnotes: tuple[dict[str, str], ...]
    rows: tuple[dict[str, Any], ...]  # the non-derivative transactions, ``parse_form4``'s row shape
    derivative_count: int


def default_signal_codes() -> frozenset[str]:
    """The codes a CALL signal reads from ``fact_insider_txn``, derived from the registered detectors' declared
    ``txn_codes`` under the config the cron runs (``signals.horizons.insider_signal_codes``). Imported lazily:
    the detector registry sits above the ingest."""
    from domain.config import DEFAULT_CONFIG
    from signals.horizons import insider_signal_codes

    return frozenset(insider_signal_codes(DEFAULT_CONFIG))


# the reason a SYSTEM application carries, per basis (the Admin panel shows it beside the Undo)
SYSTEM_REASONS: dict[str, str] = {
    BASIS_NO_SIGNAL_EFFECT: "changes no row a call signal reads (no signal effect)",
    BASIS_FILER_CORRECTION: (
        "the filer's own amendment, resolved to the Form 4 it amends — applied by the system (R8-B)"
    ),
    BASIS_NO_RETRACTION: (
        "a zero-row 4/A whose text retracts nothing (a holdings / Table II / power-of-attorney fix) — the "
        "original stands"
    ),
    BASIS_HELD: (
        "a zero-row 4/A that retracts a trade, against an original with several — the original stands until "
        "the operator decides"
    ),
}


def system_decision_values(
    *,
    tenant_id: UUID,
    security_id: UUID,
    accession: str,
    basis: str,
    disposition: str,
    signal_codes: Collection[str],
    via: str,
    replaced_rows: Collection[str] | None = None,
    action: str = "apply",
) -> dict[str, Any]:
    """One SYSTEM ``apply`` decision's columns (the ingest's and the sweep's — one shape): append-only and
    undoable on Admin like any decision. ``via`` names the writer (``ingest`` / ``sweep``); ``replaced_rows``
    (R8-B) = the original rows a MATCHED disposition accounts for (``domain.insider_amendments.match_rows``) —
    only those are replaced; ``None`` for a disposition that does not match rows."""
    return {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "amendment_accession": accession,
        "action": action,
        "reason": SYSTEM_REASONS[basis],
        "basis": basis,
        "detail": {"disposition": disposition, "signal_codes": sorted(signal_codes), "via": via},
        "replaced_rows": list(replaced_rows) if replaced_rows is not None else None,
    }


def link_detail(
    link: LinkResult,
    signal_codes: Collection[str],
    *,
    rejected: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A link row's ``detail`` (the cascade's reading, shown on Admin): the why, the system's judgment, and —
    since the R8 linker fix — the candidates refused as made public after the amendment and whether the row
    tie-break settled an ambiguous set."""
    return {
        "why": link.why,
        "rejected_rows": rejected,
        "signal_effect": link.signal_effect,
        "auto_resolved": link.auto_resolved,
        "basis": link.basis,
        "unresolved_match": link.unresolved,
        "void_reading": link.void_kind,
        "signal_codes": sorted(signal_codes),
        "excluded_later": list(link.excluded_later),
        "tie_break": link.tie_break,
        **(dict(extra) if extra else {}),
    }


def write_link(
    cur: psycopg.Cursor[Any],
    *,
    tenant_id: UUID,
    security_id: UUID,
    accession: str,
    link: LinkResult,
    signal_codes: Collection[str],
    accepted: datetime | None,
    rejected: int,
    via: str,
    recorded_at: datetime | None = None,
    extra_detail: Mapping[str, Any] | None = None,
) -> int:
    """Append one amendment's LINK and — when the system resolves it (R8-B) — the system's decision, in the
    caller's transaction, both at ONE ``recorded_at`` (``now()`` unless given). The ingest's write and the
    re-link's (``repositories.insider_amendment_repo.relink``) — one shape. Returns the decisions appended
    (0 or 1)."""
    common: dict[str, Any] = {"tenant": tenant_id, "sid": security_id, "acc": accession}
    stamp = "%(rec)s" if recorded_at is not None else "now()"
    if recorded_at is not None:
        common["rec"] = recorded_at
    cur.execute(
        "INSERT INTO insider_amendment_link (tenant_id, security_id, amendment_accession, "
        "original_accessions, voided_accessions, candidate_accessions, owner_ciks, method, "
        "disposition, status, detail, accepted, recorded_at) VALUES (%(tenant)s, %(sid)s, %(acc)s, "
        "%(orig)s, %(void)s, %(cand)s, %(owners)s, %(method)s, %(disp)s, %(status)s, %(detail)s, "
        f"%(accepted)s, {stamp})",  # noqa: S608 — stamp is one of two literals
        {
            **common,
            "orig": list(link.original_accessions),
            "void": list(link.voided_accessions),
            "cand": list(link.candidate_accessions),
            "owners": list(link.owner_ciks),
            "method": link.method,
            "disp": link.disposition,
            "status": link.status,
            "detail": Json(link_detail(link, signal_codes, rejected=rejected, extra=extra_detail)),
            "accepted": accepted,
        },
    )
    if link.basis is None:
        return 0
    # the system's application, recorded exactly like an operator's verdict (append-only, undoable)
    v = system_decision_values(
        tenant_id=tenant_id,
        security_id=security_id,
        accession=accession,
        basis=link.basis,
        disposition=link.disposition,
        signal_codes=signal_codes,
        via=via,
        replaced_rows=link.replaced_rows if link.system_action == "apply" else None,
        action=link.system_action or "apply",
    )
    cur.execute(
        "INSERT INTO insider_amendment_decision (tenant_id, security_id, amendment_accession, action, "
        "reason, basis, detail, replaced_rows, recorded_at) VALUES (%(tenant)s, %(sid)s, %(acc)s, "
        f"%(action)s, %(reason)s, %(basis)s, %(ddetail)s, %(replaced)s, {stamp})",  # noqa: S608
        {
            **common,
            "action": v["action"],
            "reason": v["reason"],
            "basis": v["basis"],
            "ddetail": Json(v["detail"]),
            "replaced": v["replaced_rows"],
        },
    )
    return 1


def _opt_date(raw: str | None) -> date | None:
    try:
        return _txn_date(raw)
    except ValueError:
        return None  # a malformed header date is unknown, never a guess (the link falls back)


def parse_form4a(xml: str) -> ParsedAmendment:
    """Parse a 4/A ownership document. The rows reuse ``parse_form4`` (the same per-row fields a stored
    Form 4 row has); the amendment header adds EVERY reporting owner (a joint filing names several — the
    linker matches any of them), ``dateOfOriginalSubmission`` (the original's FILING date — the link key),
    ``periodOfReport``, the remarks and the footnotes (the evidence the Admin panel shows), and the count of
    derivative transactions (display: a zero-row amendment whose change is in Table II)."""
    root = ET.fromstring(xml)
    owners = tuple(
        (
            norm_cik(ro.findtext("reportingOwnerId/rptOwnerCik")),
            (ro.findtext("reportingOwnerId/rptOwnerName") or "").strip() or None,
        )
        for ro in root.findall("reportingOwner")
    )
    footnotes = tuple(
        {"id": f.get("id") or "", "text": " ".join((f.text or "").split())}
        for f in root.findall("footnotes/footnote")
    )
    rows = parse_form4(xml)
    for i, r in enumerate(rows):
        r["txn_seq"] = i  # position within the amendment, the ``ingest_form4`` convention
    return ParsedAmendment(
        document_type=(root.findtext("documentType") or "").strip() or None,
        issuer_cik=norm_cik(root.findtext("issuer/issuerCik")),
        issuer_name=(root.findtext("issuer/issuerName") or "").strip() or None,
        owners=owners,
        date_of_original_submission=_opt_date(root.findtext("dateOfOriginalSubmission")),
        period_of_report=_opt_date(root.findtext("periodOfReport")),
        remarks=" ".join((root.findtext("remarks") or "").split()) or None,
        footnotes=footnotes,
        rows=tuple(rows),
        derivative_count=len(root.findall("derivativeTable/derivativeTransaction")),
    )


def _json_row(r: dict[str, Any]) -> dict[str, Any]:
    """A parsed row as stored JSON: the date as ISO text, every other field verbatim."""
    out = dict(r)
    out["txn_date"] = r["txn_date"].isoformat() if r.get("txn_date") else None
    return out


def stored_rows_by_accession(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> dict[str, list[dict[str, Any]]]:
    """Every STORED Form 4 row of one security — the latest version per natural key, no time gate (the
    linker compares against what is held now) — grouped by accession. The amendment's link is computed
    against the FULL rows here, never against a windowed as-of read, and stored; a read applies it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (accession, insider_name, valid_from, txn_seq) "
            "accession, insider_name, insider_role, valid_from, txn_seq, txn_code, shares, price, usd, "
            "aff_10b5_1, rpt_owner_cik, accepted "
            "FROM fact_insider_txn WHERE tenant_id = %s AND security_id = %s "
            "ORDER BY accession, insider_name, valid_from, txn_seq, recorded_at DESC, id DESC",
            (tenant_id, security_id),
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for r in cur.fetchall():
            out.setdefault(r["accession"], []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: r["txn_seq"])
    return out


def existing_amendment_accessions(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> set[str]:
    """The 4/A accessions already stored for (tenant, security) — the leg skips them (a re-run appends
    nothing; the link is never silently recomputed)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT accession FROM insider_amendment WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return {r["accession"] for r in cur.fetchall()}


@dataclass(frozen=True)
class AmendmentIngest:
    """One 4/A's outcome. ``other_issuer`` = screened, nothing stored; else ``link`` is what was stored."""

    accession: str
    other_issuer: bool = False
    issuer_cik: str | None = None
    link: LinkResult | None = None
    rejected: int = 0
    rows: int = 0


def ingest_form4a(
    conn: psycopg.Connection,
    security_id: UUID,
    xml: str,
    accession: str,
    *,
    member_cik: str | None,
    filed_by_accession: Mapping[str, date],
    filed: date | None = None,
    accepted: datetime | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at: datetime | None = None,
    stored: Mapping[str, list[dict[str, Any]]] | None = None,
    signal_codes: Collection[str] | None = None,
    accepted_by_accession: Mapping[str, datetime] | None = None,
) -> AmendmentIngest:
    """Parse one 4/A, screen it, link it against the security's stored Form 4 rows and append the filing +
    its link (the caller owns the transaction — every row shares its ``now()``). ``stored`` may be passed in
    (the leg loads it once per security); else it is read here.

    THE FILER'S CORRECTION, APPLIED (R8-B, 2026-10-20): an amendment the linker resolves to a definite original
    is applied HERE, in the same transaction as its link (one ``recorded_at`` — a new 4/A is never pending): an
    append-only ``apply`` decision whose basis is ``system:no-signal-effect`` when it changes no row a call
    signal reads (``signal_codes``, default derived from the registered detectors — ``signals.horizons
    .insider_signal_codes``), else ``system:filer-correction``. A partial / consolidation / addition /
    restatement / field change carries its ROW MATCH (``replaced_rows`` — the original rows it accounts for;
    every other original row stands). A zero-row 4/A is read by the filer's words (``void_reading``): it is
    applied only when it retracts a single-trade original; otherwise the system records a ``keep`` (basis
    ``system:no-retraction`` — the original stands — or ``system:held`` — owed to the operator). Undoable on
    Admin. An ambiguous link, and a match neither shape nor words resolve, write no decision — they wait for the
    operator (or read as historical).

    ``accepted_by_accession`` (the R8 linker fix) = each Form 4's SEC acceptance from the SAME submissions index
    as ``filed_by_accession`` and this 4/A's own ``accepted`` — the order test refuses a candidate made public
    after the amendment (``domain.insider_amendments.made_public_after``); without it only the filing dates
    order them (a same-day pair is then not told apart)."""
    if signal_codes is None:
        signal_codes = default_signal_codes()
    parsed = parse_form4a(xml)
    member = norm_cik(member_cik)
    if member is not None and parsed.issuer_cik is not None and parsed.issuer_cik != member:
        return AmendmentIngest(accession=accession, other_issuer=True, issuer_cik=parsed.issuer_cik)
    rows: list[dict[str, Any]] = []
    rejected = 0
    for r in parsed.rows:
        if r.get("txn_date") is None:
            continue
        reason = implausible_txn_date(r["txn_date"], accession)
        if reason is not None:
            rejected += 1
            print(
                f"  REJECT insider 4/A txn {accession} seq={r['txn_seq']} "
                f"{r['insider_name'] or '?'} ({r['txn_code'] or '?'}): transactionDate "
                f"{r['txn_date'].isoformat()} {reason}. The filing says this verbatim — NOT stored (#3)."
            )
            continue
        rows.append(r)
    facts = AmendmentFacts(
        accession=accession,
        owners=parsed.owners,
        date_of_original_submission=parsed.date_of_original_submission,
        filed=filed,
        accepted=accepted,
        rows=tuple(rows),
        text=filer_text(parsed.remarks, parsed.footnotes),
    )
    held = (
        stored
        if stored is not None
        else stored_rows_by_accession(conn, security_id, tenant_id=tenant_id)
    )
    link = link_amendment(facts, held, filed_by_accession, signal_codes, accepted_by_accession)
    common: dict[str, Any] = {"tenant": tenant_id, "sid": security_id, "acc": accession}
    stamp = "%(rec)s" if recorded_at is not None else "now()"
    if recorded_at is not None:
        common["rec"] = recorded_at
    with conn.cursor() as cur:
        # the filing as filed first, then its link + the system's decision (``write_link``)
        cur.execute(
            "INSERT INTO insider_amendment (tenant_id, security_id, accession, issuer_cik, issuer_name, "
            "owners, date_of_original_submission, period_of_report, remarks, footnotes, rows, "
            "derivative_count, filed, accepted, recorded_at) VALUES (%(tenant)s, %(sid)s, %(acc)s, "
            "%(icik)s, %(iname)s, %(owners)s, %(dos)s, %(por)s, %(remarks)s, %(fn)s, %(rows)s, %(nd)s, "
            f"%(filed)s, %(accepted)s, {stamp})",  # noqa: S608 — stamp is one of two literals
            {
                **common,
                "icik": parsed.issuer_cik,
                "iname": parsed.issuer_name,
                "owners": Json([{"cik": c, "name": n} for c, n in parsed.owners]),
                "dos": parsed.date_of_original_submission,
                "por": parsed.period_of_report,
                "remarks": parsed.remarks,
                "fn": Json(list(parsed.footnotes)),
                "rows": Json([_json_row(r) for r in rows]),
                "nd": parsed.derivative_count,
                "filed": filed,
                "accepted": accepted,
            },
        )
        write_link(
            cur,
            tenant_id=tenant_id,
            security_id=security_id,
            accession=accession,
            link=link,
            signal_codes=signal_codes,
            accepted=accepted,
            rejected=rejected,
            via="ingest",
            recorded_at=recorded_at,
        )
    return AmendmentIngest(
        accession=accession,
        issuer_cik=parsed.issuer_cik,
        link=link,
        rejected=rejected,
        rows=len(rows),
    )


@dataclass
class AmendmentLegResult:
    """The 4/A leg's outcome for one security."""

    stored: list[AmendmentIngest] = field(default_factory=list)
    other_issuer: list[AmendmentIngest] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(
        default_factory=list
    )  # (accession, reason) — unparseable

    @property
    def pending(self) -> int:
        """Stored tonight and NOT applied by the system (an ambiguous link, or an unresolved row match — R8-B)."""
        return sum(1 for a in self.stored if a.link is not None and a.link.status == STATUS_PENDING)


def accepted_times(filings: Collection[Mapping[str, str]]) -> dict[str, datetime]:
    """``accession -> SEC acceptance`` from enumerated submissions rows (``filings_of``'s shape) — the SAME
    source as the amendment's own ``accepted`` (the R8 linker fix's order test,
    ``domain.insider_amendments.made_public_after``); an unparseable one is left out (unknown, never a guess).
    """
    out: dict[str, datetime] = {}
    for f in filings:
        at = parse_acceptance(f.get("accepted"))
        if at is not None:
            out[f["accession"]] = at
    return out


def filed_dates(filings: Collection[Mapping[str, str]]) -> dict[str, date]:
    """``accession -> filing date`` from enumerated submissions rows (``filings_of``'s shape)."""
    out: dict[str, date] = {}
    for f in filings:
        try:
            out[f["accession"]] = date.fromisoformat(str(f["filed"]))
        except (KeyError, ValueError):
            continue
    return out


def ingest_amendments(
    conn: psycopg.Connection,
    client: Any,
    security_id: UUID,
    cik: str,
    ticker: str | None,
    submissions: Mapping[str, Any],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    known_skips: Collection[str] | None = None,
    tolerable: Callable[[Exception], bool] | None = None,
    signal_codes: Collection[str] | None = None,
) -> tuple[AmendmentLegResult, tuple[IngestSkip, ...]]:
    """The 4/A leg for one security: every 4/A in its submissions feed not already stored is fetched (the
    immutable ``forms/`` cache — a re-run costs no request), parsed, screened, linked and stored. Returns the
    outcome and the Q18 skip records (``form4_other_issuer`` for a filing about another company,
    ``form4_unparseable`` for one whose fetch/parse failed — ``tolerable`` decides which errors are ONE
    filing's, exactly the Form 4 leg's ``_tolerable_filing_error``; anything else raises).

    Runs AFTER the Form 4 leg, so a 4/A and the original it amends that arrive the same night link against
    each other. The original's filing date comes from the SAME submissions document (``filings_of(subs,
    "4")`` — what ``dateOfOriginalSubmission`` names). Oldest first, so an amendment of an amendment is
    linked in filing order."""
    result = AmendmentLegResult()
    skips: list[IngestSkip] = []
    amendments = filings_of(dict(submissions), AMENDMENT_FORM)
    if not amendments:
        return result, ()
    seen = existing_amendment_accessions(conn, security_id, tenant_id=tenant_id)
    todo = [f for f in amendments if f["accession"] not in seen]
    if not todo:
        return result, ()
    originals = filings_of(dict(submissions), "4")
    filed_by = filed_dates(originals)
    accepted_by = accepted_times(originals)  # the R8 linker fix's order test reads the SAME index
    # the stored Form 4 rows, read ONCE (the Form 4 leg above already appended tonight's originals)
    held = stored_rows_by_accession(conn, security_id, tenant_id=tenant_id)
    codes = frozenset(signal_codes) if signal_codes is not None else default_signal_codes()
    for f in reversed(todo):  # the feed is newest-first
        acc = f["accession"]
        url = form4_doc_url(cik, acc, f["primary_doc"])
        doc = f["primary_doc"].rsplit("/", 1)[-1]
        try:
            xml = client.get_text(url, f"forms/{acc}/{doc}")
            try:
                filed: date | None = date.fromisoformat(str(f.get("filed")))
            except ValueError:
                filed = None
            one = ingest_form4a(
                conn,
                security_id,
                xml,
                acc,
                member_cik=cik,
                filed_by_accession=filed_by,
                accepted_by_accession=accepted_by,
                filed=filed,
                accepted=parse_acceptance(f.get("accepted")),
                tenant_id=tenant_id,
                stored=held,
                signal_codes=codes,
            )
        except Exception as e:
            # tolerated only BEFORE this filing's first write: parse + link run before either INSERT
            if tolerable is None or not tolerable(e):
                raise
            result.skipped.append((acc, clip_reason(e)))
            skips.append(IngestSkip(FORM4_UNPARSEABLE, acc, security_id, ticker, clip_reason(e)))
            if not is_known(known_skips, FORM4_UNPARSEABLE, acc):
                print(
                    f"  warn: {ticker or security_id} form 4/A {acc} ({f.get('filed')}) skipped: {e}"
                )
            continue
        if one.other_issuer:
            result.other_issuer.append(one)
            why = (
                f"4/A about issuer CIK {one.issuer_cik}, not this security "
                "(the member is its reporting owner)"
            )
            skips.append(IngestSkip(FORM4_OTHER_ISSUER, acc, security_id, ticker, why))
            if not is_known(known_skips, FORM4_OTHER_ISSUER, acc):
                print(f"  {ticker or security_id} form 4/A {acc} not stored: {why}")
            continue
        result.stored.append(one)
    return result, tuple(skips)

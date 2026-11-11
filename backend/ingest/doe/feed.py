"""The DOE / USASpending automated catalyst feed.

Pipeline (all deterministic — invariant #3, never model-sourced):
  discover (fuzzy search NET)  →  resolve EXACTLY by curated recipient_id  →  fetch award detail  →
  derive grade + liveness horizon from the structured terms  →  emit a catalyst-conviction fact.

The grade rule and the obligation threshold are the only call-logic here; both are flagged ``PROPOSED``
and live in ``CallConfig`` so the operator can confirm/tune them at review (this is the operator's edge).

The catalyst's EVENT DATE (``fact_catalyst.valid_from``: the day it fires and the day the public clock
treats it as public) is the award's SIGNING date, and its work-period start only when the award carries
no signing date — ``_event_date``, the one home of that rule (rule log
``2026-11-catalysts-dated-by-signing``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import psycopg

from db.bitemporal import fact_exists
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import CatalystType, Grade
from domain.settings import get_settings
from ingest.catalyst import ingest_catalyst
from ingest.doe import entities
from ingest.doe.client import UsaSpendingClient

_SOURCE = "doe_usaspending"


def usaspending_award_url(generated_internal_id: str) -> str:
    """The human-facing USASpending award page (a catalyst's ``source_ref`` provenance link)."""
    return f"{get_settings().usaspending_award_url_base}/{generated_internal_id}"


# award_type_codes must be ONE group per call (USASpending 422s on mixed groups). These four cover the
# award kinds DOE uses for the nuclear names: definitive contracts, grants/cooperative-agreements, direct
# payments, and "other" financial assistance (where OTAs — like OKLO's reactor-pilot OTA — live, type 11).
_TYPE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("A", "B", "C", "D"),  # contracts
    ("02", "03", "04", "05"),  # grants / cooperative agreements
    ("06", "10"),  # direct payments
    ("09", "11"),  # other financial assistance (incl. OTAs)
)
_SEARCH_FIELDS = ["Award ID", "Recipient Name", "Award Amount", "recipient_id"]
_DOE = {"type": "awarding", "tier": "toptier", "name": "Department of Energy"}


@dataclass(frozen=True)
class DoeCatalyst:
    """A resolved, graded DOE award — the deterministic parse, ready to become a catalyst fact.

    ``event_date`` is the date the catalyst counts from (``_event_date``: the signing date, else the work
    period's start). ``start_date`` and ``date_signed`` are the two payload dates it was chosen from, kept
    so a reader (the label, ``pipeline.repair_catalyst_dates``) can show which one it is.
    """

    ticker: str
    generated_internal_id: str
    piid: str
    grade: Grade
    label: str
    source_ref: str
    event_date: date
    horizon_end: date | None
    obligation: float
    category: str
    start_date: date | None = None
    date_signed: date | None = None


def _to_date(value: object) -> date | None:
    """USASpending dates are ``YYYY-MM-DD`` (detail) sometimes with a trailing time — take the date."""
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def _signing_date(value: object) -> date | None:
    """The award's ``date_signed`` when the payload carries a READABLE one, else ``None``. A missing value
    and one that is not a date are the same answer (the caller then falls back to the work period's start),
    so the signing date can never be the reason an award fails to parse."""
    try:
        return _to_date(value)
    except ValueError:
        return None


def _event_date(date_signed: date | None, start_date: date | None) -> date | None:
    """THE EVENT-DATE RULE, defined once (rule log ``2026-11-catalysts-dated-by-signing``; invariant #1).

    A DOE award catalyst counts from the day the award was SIGNED: that is the day the commitment exists
    and is public. ``period_of_performance.start_date`` is when the funded work is dated to begin, and an
    award can be signed months after that (MEASURED on the six seeded awards, 2026-11-11: three of the six
    start before they were signed, one of them by 98 days). ``fact_catalyst.valid_from`` is this date and
    the public clock reads it as the day the catalyst became public (``db.public_clock``), so dating by the
    start let a history view between the two dates read a catalyst that did not exist yet.

    - a readable signing date -> the signing date, also when it is EARLIER than the start (an award is
      public once signed, whenever its work begins);
    - no readable signing date -> the work period's start (the behavior before this rule);
    - neither -> ``None`` (the caller skips the award: nothing honest to anchor it on).
    """
    return date_signed or start_date


def _derive_grade(category: str, obligation: float, cfg: CallConfig) -> Grade:
    """Deterministic grade — ``[APPROVED]``, the **customer-vs-sponsor** principle (the operator's edge).

    The line is whether DOE is your **customer** or your **sponsor**:
    - a procurement **contract** of real size (DOE *buying your product*) = contracted revenue = ``core``.
    - a **loan / loan guarantee** (committed financing) = ``core`` — an original core example.
    - a grant / cooperative agreement / OTA / other assistance (DOE *funding your development* = support,
      not revenue), or a sub-threshold contract = ``flip`` (provisional → small, short-dated).

    Award type is the *proxy*; customer-vs-sponsor is the *principle*. Reproduces the operator's precedent:
    LEU's $317M HALEU production CONTRACT → core; OKLO's $0 reactor-pilot OTA (assistance) → flip. **Grade is
    the NATURE of the commitment, never its size:** a large *assistance* award (e.g. a $148M cooperative
    agreement) stays ``flip`` — its size flows through CONFIDENCE within the flip grade, not a grade bump
    (a recalibration item; see docs/RECALIBRATION.md). NB the loans award-type group isn't *queried* yet
    (the feed iterates contracts/grants/direct-payments/other) — the rule is here so the first loan that
    surfaces grades core, not flip; wiring the loans query is on the recalibration list.
    """
    if category == "loan":  # direct loan / loan guarantee = committed financing → build
        return Grade.CORE
    if category == "contract" and obligation >= cfg.doe_core_min_obligation_usd:
        return Grade.CORE
    return Grade.FLIP


def discover(
    client: UsaSpendingClient, *, search_terms: tuple[str, ...] = entities.SEARCH_TERMS
) -> dict[str, str]:
    """Discover + EXACTLY resolve DOE awards for the curated entities.

    The search is a fuzzy NET (it over- and under-matches); only awards whose ``recipient_id`` is in the
    curated table survive (``entities.resolve``), so NAC International and the polluted OKLO TECHNOLOGIES
    recipient are dropped regardless of what a term drags in. Returns ``{generated_internal_id: ticker}``,
    deduped (a term × group can surface the same award more than once).
    """
    resolved: dict[str, str] = {}
    for term in search_terms:
        for codes in _TYPE_GROUPS:
            body = {
                "filters": {
                    "recipient_search_text": [term],
                    "agencies": [_DOE],
                    "award_type_codes": list(codes),
                },
                "fields": _SEARCH_FIELDS,
                "limit": 100,
                "sort": "Award Amount",
                "order": "desc",
            }
            for row in client.search_awards(body).get("results", []):
                awardee = entities.resolve(row.get("recipient_id"))
                gid = row.get("generated_internal_id")
                if awardee and gid:
                    resolved[gid] = awardee.ticker
    return resolved


def parse_award(
    client: UsaSpendingClient,
    generated_internal_id: str,
    ticker: str,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> DoeCatalyst | None:
    """Fetch an award's detail and deterministically parse it into a graded catalyst (``None`` to skip).

    The event date is the award's signing date, else its work period's start (``_event_date``, the one home
    of that rule). ``horizon_end`` is the work period's end either way, so liveness
    (``horizon_end - valid_from``) follows the event date. When the two dates differ the label says both
    (invariant #6: the card shows which date the catalyst counts from); when they are equal, or the award
    carries no signing date, the label is exactly what it was before the rule.
    """
    d = client.award_detail(generated_internal_id)
    po = d.get("period_of_performance") or {}
    start_date = _to_date(po.get("start_date"))
    date_signed = _signing_date(d.get("date_signed"))
    event_date = _event_date(date_signed, start_date)
    if event_date is None:
        return None  # no action date → can't anchor liveness honestly; skip
    category = (d.get("category") or "").lower()
    obligation = float(d.get("total_obligation") or 0.0)
    piid = d.get("piid") or d.get("fain") or generated_internal_id
    horizon_end = _to_date(po.get("end_date"))
    desc = (d.get("description") or "").strip()
    grade = _derive_grade(category, obligation, cfg)
    label = (
        f"DOE {category or 'award'} ({piid})"
        + (f": {desc[:80]}" if desc else "")
        + f" — ${obligation:,.0f} obligated, term to {horizon_end or 'n/a'}"
    )
    if date_signed is not None and start_date is not None and date_signed != start_date:
        label += f" · signed {date_signed}, work period from {start_date}"
    return DoeCatalyst(
        ticker=ticker,
        generated_internal_id=generated_internal_id,
        piid=piid,
        grade=grade,
        label=label,
        source_ref=usaspending_award_url(generated_internal_id),
        event_date=event_date,
        horizon_end=horizon_end,
        obligation=obligation,
        category=category,
        start_date=start_date,
        date_signed=date_signed,
    )


def run_doe_feed(
    conn: psycopg.Connection,
    client: UsaSpendingClient,
    resolve_security: Callable[[str], UUID | None],
    *,
    cfg: CallConfig = DEFAULT_CONFIG,
    search_terms: tuple[str, ...] = entities.SEARCH_TERMS,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    skip_stored: bool = False,
) -> list[DoeCatalyst]:
    """Run the feed end-to-end: discover → exact-resolve → detail-parse → emit catalyst facts.

    Deterministic; the caller owns the txn (no commit here). ``resolve_security(ticker) -> UUID | None``
    maps a curated ticker to its security id (the security master) — a ticker outside this universe is
    skipped. Catalyst facts are emitted under ``tenant_id`` (defaults to demo; pass a production tenant to
    run the feed into production). ``resolve_security`` must resolve in the SAME tenant. Returns the emitted
    catalysts (for logging / assertions).

    ``skip_stored`` (default ``False`` — the emit path is BYTE-IDENTICAL to before) suppresses the fact
    APPEND for a catalyst whose ``source_ref`` (the ``fact_catalyst`` natural key) is already stored under
    that security, while still returning it in ``emitted``. It exists for ``pipeline.seed``, this function's
    ONLY caller today (``seed_doe_catalysts``), which replays the committed USASpending fixtures at every
    container boot: without it each boot appended a fresh version of the same six awards. A live/recurring
    caller must leave it ``False`` — a re-run that legitimately RESTATES an award (a re-graded obligation,
    a moved period-of-performance end) has to append a new version, and this flag would swallow it.
    """
    emitted: list[DoeCatalyst] = []
    for gid, ticker in discover(client, search_terms=search_terms).items():
        sec = resolve_security(ticker)
        if sec is None:
            continue
        catalyst = parse_award(client, gid, ticker, cfg)
        if catalyst is None:
            continue
        emitted.append(catalyst)
        if skip_stored and fact_exists(
            conn,
            "fact_catalyst",
            security_id=sec,
            tenant_id=tenant_id,
            source_ref=catalyst.source_ref,
        ):
            continue  # a fixture replay, not new information — see ``skip_stored``
        ingest_catalyst(
            conn,
            sec,
            catalyst_type=CatalystType.GOV_FUNDING,
            grade=catalyst.grade,
            label=catalyst.label,
            source=_SOURCE,
            source_ref=catalyst.source_ref,
            event_date=catalyst.event_date,
            horizon_end=catalyst.horizon_end,
            tenant_id=tenant_id,
        )
    return emitted

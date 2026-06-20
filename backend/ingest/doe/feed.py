"""The DOE / USASpending automated catalyst feed.

Pipeline (all deterministic — invariant #3, never model-sourced):
  discover (fuzzy search NET)  →  resolve EXACTLY by curated recipient_id  →  fetch award detail  →
  derive grade + liveness horizon from the structured terms  →  emit a catalyst-conviction fact.

The grade rule and the obligation threshold are the only call-logic here; both are flagged ``PROPOSED``
and live in ``CallConfig`` so the operator can confirm/tune them at review (this is the operator's edge).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import psycopg

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
    """A resolved, graded DOE award — the deterministic parse, ready to become a catalyst fact."""

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


def _to_date(value: object) -> date | None:
    """USASpending dates are ``YYYY-MM-DD`` (detail) sometimes with a trailing time — take the date."""
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


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
    """Fetch an award's detail and deterministically parse it into a graded catalyst (``None`` to skip)."""
    d = client.award_detail(generated_internal_id)
    po = d.get("period_of_performance") or {}
    event_date = _to_date(po.get("start_date"))
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
    )


def run_doe_feed(
    conn: psycopg.Connection,
    client: UsaSpendingClient,
    resolve_security: Callable[[str], UUID | None],
    *,
    cfg: CallConfig = DEFAULT_CONFIG,
    search_terms: tuple[str, ...] = entities.SEARCH_TERMS,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> list[DoeCatalyst]:
    """Run the feed end-to-end: discover → exact-resolve → detail-parse → emit catalyst facts.

    Deterministic; the caller owns the txn (no commit here). ``resolve_security(ticker) -> UUID | None``
    maps a curated ticker to its security id (the security master) — a ticker outside this universe is
    skipped. Catalyst facts are emitted under ``tenant_id`` (defaults to demo; pass a production tenant to
    run the feed into production). ``resolve_security`` must resolve in the SAME tenant. Returns the emitted
    catalysts (for logging / assertions).
    """
    emitted: list[DoeCatalyst] = []
    for gid, ticker in discover(client, search_terms=search_terms).items():
        sec = resolve_security(ticker)
        if sec is None:
            continue
        catalyst = parse_award(client, gid, ticker, cfg)
        if catalyst is None:
            continue
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
        emitted.append(catalyst)
    return emitted

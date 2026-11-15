"""THE MACHINE WRITER (V2 W2b): one extraction result in, platform values out — the only code that turns the
deterministic extractor's parse of a company's filings into a share count or a cash/burn value
(``docs/PLATFORM_VALUES.md``, "The machine writer").

``record_extraction`` takes an ``ExtractionResult`` (what ``ingest.edgar.extract.extract_with_annual_fallback``
returns; already fetched — no network here) and, for EACH value type, does exactly one thing:

- a clean **AUTO** value → ``domain.platform_values.machine_action`` decides: **apply** it (a fact stamped
  ``ratified_by='auto'`` in the data tenant), record it as a flagged **candidate** (the company is pinned: the
  steward reviews every new filing value), or **skip** it (``already_on_file`` — ANY stored version of the filing,
  set-aside ones included, so an undone approve is never re-applied; ``older_than_on_file`` — a date on or before
  the value shown; ``after_today`` — a value dated after today could never be shown, D1). A strictly newer filing
  is applied over a verified value unless the company is pinned (D3 (a)).
- an annual foreign filer's **FLAG share count whose only flags are the annual ones** (``annual-cover``,
  ``stale-cover``), no older than ``ExtractorConfig.annual_shares_auto_max_age_days`` (400) on today's market date
  → treated as AUTO (``domain.platform_values.annual_shares_auto_eligible``, the extractor plan's PR 5): the same
  ``machine_action`` above, and an applied one's note adds that it is an annual figure, its as-of date, age and
  flags. Share counts only: annual cash and burn always waits for the steward.
- a **FLAG** value (or an AUTO value with no parsed number) → a ``flag`` candidate carrying the raw values, the
  flags, the filing and the located passages. Never a fact.
- **nothing** → a ``nothing`` candidate with the extractor's reason (``no-annual-filing``, ``cover-not-located``,
  ``cash-generative``, ``financials-in-exhibit``, ``exhibit-ambiguous``, ``statements-not-located``, else
  ``no-candidate``). Never a fact. ``cash-generative`` is a state ("runway does not apply"), stored the same way.

A candidate is not a value: it lives outside every fact table, so no meter, as-of read, signal, call or mirror can
read it (``tests/db/test_v2_schema.py``). A candidate that repeats the company's NEWEST candidate for that type
(same outcome, reason, filing, date, flags and values) is skipped, so re-clicking get-data adds no rows.

THE MODEL NEVER SOURCES A NUMBER (invariant #3): only the extractor's own parse is read. A fact carrying an
``estimate_source`` (a model's proposal; today only purity has one) is ignored as if absent, and this module
imports nothing from ``llm``.

LOCKING AND STAMPS (the W2 plan's Q7). The caller fetches first, with the company free; this function then takes
the company write lock (``db.write_lock.acquire_company_lock``: the shared fact-writer gate plus the company,
waiting at most ``wait_s``; ``CompanyLockBusy`` when it cannot, with NOTHING written), RE-READS what is on file
under it (the state, the pin, every stored filing), writes both types in ONE transaction, moves the company's change
stamp in that same transaction (the fact writers stamp; a candidate-only write stamps here), commits, and only then
releases (the release stamps again). Before V2 the route checked "on file?" before its SEC call and never again,
so two clicks at once could both append; under the lock the second re-reads and writes nothing. A fault rolls the
whole transaction back (neither type is half-written) and re-raises.

Callers: the Workbench get-data auto-confirm (``POST /workbench/facts/auto-confirm``, D2 (a)) with
``Settings.ingest_lock_request_wait_s``; background extraction (``platform_values.extraction``) calls it with the
same request wait, so one busy company cannot spend a run's budget.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg

from db.change_stamp import mark_changed
from db.write_lock import acquire_company_lock
from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.market_time import market_today
from domain.platform_values import (
    FACT_TYPES,
    MACHINE,
    FactType,
    annual_shares_auto_eligible,
    machine_action,
)
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import cik_of_source_ref, ingest_shares_outstanding, shares_publication
from platform_values.state import read_state
from repositories import platform_value_repo as repo

# The reason a "nothing" candidate carries when the extractor emitted no value of that type and gave no reason.
NO_CANDIDATE = "no-candidate"

MachineReason = Literal[
    "applied",  # a fact stamped ``auto`` was written
    "already_on_file",  # a version of this filing is stored (set-aside ones too): nothing written
    "older_than_on_file",  # dated on or before the value shown: nothing written
    "after_today",  # dated after today's market date: never written (D1)
    "pinned",  # the company is pinned: a flagged candidate (flag ``pinned``) instead of a fact
    "flagged",  # a FLAG result: a flagged candidate
    "nothing_found",  # the extractor found no value: a "nothing" candidate
]


@dataclass(frozen=True)
class MachineOutcome:
    """What the machine did with one value type of one extraction.

    ``applied`` = a fact was written (``fact_id``). ``candidate_id`` = the candidate that stands for this result
    (the one written now, or the identical newest one already stored when ``written`` is False). ``written`` = any
    row was appended for this type now."""

    fact_type: FactType
    applied: bool
    reason: MachineReason
    fact_id: UUID | None = None
    candidate_id: UUID | None = None
    written: bool = False


@dataclass(frozen=True)
class _Planned:
    """What the extraction says for one type, worked out BEFORE the lock (no database): ``auto`` (an appliable
    value), ``flag`` (a value for the steward) or ``nothing`` (with ``reason``)."""

    fact_type: FactType
    kind: Literal["auto", "flag", "nothing"]
    fact: ExtractedFact | None = None
    reason: str | None = None
    filing_accession: str | None = None
    filing_accepted: datetime | None = None
    # set when the annual share-count rule made a FLAG count appliable: the note the fact will carry if applied
    annual_note: str | None = None


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def _has_value(fact: ExtractedFact) -> bool:
    if fact.fact_type == "shares_outstanding":
        return _finite(fact.value)
    return _finite(fact.cash_usd) and _finite(fact.quarterly_burn_usd)


def _nothing_reason(result: ExtractionResult, fact_type: FactType) -> str:
    """The extractor's own reason there is no value of ``fact_type``: the runway leg's reason first for cash/burn
    (an annual filer), then the result's (no-annual-filing / cover-not-located), else ``no-candidate``.
    """
    if fact_type == "cash_burn" and result.runway_empty_reason:
        return result.runway_empty_reason
    return result.empty_reason or NO_CANDIDATE


def _annual_note(fact: ExtractedFact, today: date, max_age_days: int) -> str:
    """The sentence an annual count applied by the annual share-count rule adds to its stored note: that it is an
    annual figure, its as-of date and age, the flags it carried and the cap (the extractor's note before it already
    names the form, the as-of date and the ADS ratio)."""
    age = (today - fact.event_date).days
    return (
        f" Applied automatically as an annual figure (the annual share-count rule): a 20-F/40-F cover count as of "
        f"{fact.event_date.isoformat()}, {age} days old on {today.isoformat()}, flags {', '.join(fact.flags)}; "
        f"an annual count is applied up to {max_age_days} days old and waits for the steward after that."
    )


def plan(
    result: ExtractionResult,
    fact_type: FactType,
    *,
    cache_dir: Path | None = None,
    cik: str | int | None = None,
    today: date | None = None,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> _Planned:
    """Map one value type of ``result`` to auto / flag / nothing (the W2 plan's Q6). No database. A share count's
    filing and acceptance (F9, its public instant) are read from the EDGAR cache on disk, never a request.

    A FLAG share count the annual share-count rule accepts (``annual_shares_auto_eligible``: an annual cover count
    whose only flags are the annual ones, at most ``cfg.annual_shares_auto_max_age_days`` old on ``today``, the
    market date by default) plans as ``auto`` and then meets ``machine_action`` like any AUTO value.
    """
    fact = next(
        (
            f
            for f in result.facts
            if f.fact_type == fact_type
            and f.estimate_source is None  # the extractor's own parse only
        ),
        None,
    )
    if fact is None:
        return _Planned(fact_type, "nothing", reason=_nothing_reason(result, fact_type))
    kind: Literal["auto", "flag"] = (
        "auto" if fact.tier == Tier.AUTO and _has_value(fact) else "flag"
    )
    annual_note = None
    if kind == "flag":
        day = today or market_today()
        cap = cfg.annual_shares_auto_max_age_days
        if annual_shares_auto_eligible(fact, day, cap):
            kind, annual_note = "auto", _annual_note(fact, day, cap)
    accession = accepted = None
    if fact_type == "shares_outstanding" and _finite(fact.value):
        accession, accepted = shares_publication(
            cache_dir, cik or cik_of_source_ref(fact.source_ref), fact.value, fact.event_date
        )
    return _Planned(fact_type, kind, fact, None, accession, accepted, annual_note)


def _candidate_columns(p: _Planned, outcome: str, extra_flags: tuple[str, ...] = ()) -> dict:
    if p.fact is None:
        return {"outcome": outcome, "reason": p.reason}
    f = p.fact
    flags = list(f.flags) + [x for x in extra_flags if x not in f.flags]
    cols: dict[str, Any] = {
        "outcome": outcome,
        "flags": flags,
        "source": f.source,
        "source_ref": f.source_ref,
        "event_date": f.event_date,
        "note": f.note or None,
        "located_passages": [lp.model_dump(mode="json") for lp in f.located_passages],
        "filing_accession": p.filing_accession,
        "filing_accepted": p.filing_accepted,
    }
    if p.fact_type == "shares_outstanding":
        cols.update(shares=f.value, ads_ratio=f.ads_ratio, ads_ratio_status=f.ads_ratio_status)
    else:
        cols.update(
            cash_usd=f.cash_usd,
            quarterly_burn_usd=f.quarterly_burn_usd,
            statement_currency=f.statement_currency,
        )
    return cols


# The candidate columns that make two results "the same" (Q6: outcome, reason, filing, date, flags and values).
_SAME = (
    "reason",
    "source_ref",
    "event_date",
    "shares",
    "cash_usd",
    "quarterly_burn_usd",
    "statement_currency",
    "ads_ratio",
    "ads_ratio_status",
)
_NUMERIC = frozenset({"shares", "cash_usd", "quarterly_burn_usd"})


def _same_candidate(stored: Mapping[str, Any], cols: Mapping[str, Any]) -> bool:
    if stored["outcome"] != cols["outcome"]:
        return False
    if sorted(stored.get("flags") or []) != sorted(cols.get("flags") or []):
        return False
    for k in _SAME:
        a, b = stored.get(k), cols.get(k)
        if k in _NUMERIC and a is not None and b is not None:
            if float(a) != float(b):
                return False
        elif a != b:
            return False
    return True


def _record_candidate(
    conn: psycopg.Connection, security_id: UUID, fact_type: FactType, cols: dict
) -> tuple[UUID, bool]:
    """Append the candidate unless it repeats the company's newest one for this type. ``(id, written)``."""
    newest = repo.latest_candidate(conn, security_id, fact_type)
    if newest is not None and _same_candidate(newest, cols):
        return newest["id"], False
    outcome = cols.pop("outcome")
    cid = repo.append_candidate(
        conn, security_id=security_id, fact_type=fact_type, outcome=outcome, **cols
    )
    return cid, True


def _apply(conn: psycopg.Connection, security_id: UUID, p: _Planned) -> UUID:
    """Write the AUTO value as a fact stamped ``auto`` in the data tenant (the fact writer stamps the company)."""
    f = p.fact
    assert f is not None
    common = dict(
        source=f.source,
        source_ref=f.source_ref,
        event_date=f.event_date,
        note=f"{f.note}{p.annual_note or ''}",
        ratified_by=MACHINE,  # applied by the machine, never confirmed by a human
        vouched=None,  # no estimate was shown to anyone
        tenant_id=DATA_TENANT_ID,  # platform data (V2): one value for every workspace
    )
    if p.fact_type == "shares_outstanding":
        return ingest_shares_outstanding(
            conn,
            security_id,
            shares=f.value,  # the extractor's own parse
            ads_ratio=f.ads_ratio,
            ads_ratio_status=f.ads_ratio_status,
            filing_accession=p.filing_accession,
            filing_accepted=p.filing_accepted,
            **common,
        )
    return ingest_cash_burn(
        conn,
        security_id,
        cash_usd=f.cash_usd,
        quarterly_burn_usd=f.quarterly_burn_usd,
        **common,
    )


def _decide_and_write(
    conn: psycopg.Connection, security_id: UUID, p: _Planned, today: date
) -> MachineOutcome:
    """One type, under the lock: re-read what is on file, decide, write (no commit)."""
    ft = p.fact_type
    if p.kind == "nothing":
        cid, written = _record_candidate(conn, security_id, ft, _candidate_columns(p, "nothing"))
        return MachineOutcome(ft, False, "nothing_found", candidate_id=cid, written=written)
    if p.kind == "flag":
        cid, written = _record_candidate(conn, security_id, ft, _candidate_columns(p, "flag"))
        return MachineOutcome(ft, False, "flagged", candidate_id=cid, written=written)
    f = p.fact
    assert f is not None
    st = read_state(conn, security_id, ft, asof=today)  # what the meters show NOW, under the lock
    decision = machine_action(
        source_ref=f.source_ref,
        event_date=f.event_date,
        stored_refs=repo.stored_source_refs(conn, security_id, ft),
        shown=st.value,
        pinned=st.pinned,
        today=today,
    )
    if decision.action == "apply":
        fid = _apply(conn, security_id, p)
        return MachineOutcome(ft, True, "applied", fact_id=fid, written=True)
    if decision.action == "candidate":
        cols = _candidate_columns(p, "flag", decision.flags)
        cid, written = _record_candidate(conn, security_id, ft, cols)
        return MachineOutcome(ft, False, "pinned", candidate_id=cid, written=written)
    return MachineOutcome(ft, False, decision.reason)  # type: ignore[arg-type] — a skip reason


def record_extraction(
    conn: psycopg.Connection,
    security_id: UUID,
    result: ExtractionResult,
    *,
    wait_s: float,
    cache_dir: Path | None = None,
    cik: str | int | None = None,
    today: date | None = None,
) -> dict[FactType, MachineOutcome]:
    """Turn ``result`` (the deterministic extractor's, already fetched) into platform values for ``security_id``:
    a fact stamped ``auto``, a candidate, or nothing, per value type (the module docstring has the rules).

    Takes the company write lock (waiting at most ``wait_s``; ``CompanyLockBusy`` with nothing written), re-reads
    what is on file under it, writes both types in ONE transaction with the company's change stamp, COMMITS, then
    releases. Any open transaction on ``conn`` is committed with it, so call it with none. A fault rolls back and
    re-raises. ``cache_dir`` / ``cik``: where to read a share count's filing acceptance (the EDGAR cache on disk;
    ``cik`` defaults to the one in the filing URL). ``today`` defaults to today's market date.
    """
    day = today or market_today()
    planned = [
        plan(result, ft, cache_dir=cache_dir, cik=cik, today=day) for ft in FACT_TYPES
    ]  # no database
    lock = acquire_company_lock(conn, security_id, wait_s=wait_s)
    with lock:  # released only after the commit below
        try:
            outcomes = {p.fact_type: _decide_and_write(conn, security_id, p, day) for p in planned}
            if any(o.written and not o.applied for o in outcomes.values()):
                mark_changed(
                    conn, [security_id]
                )  # a candidate write, stamped in the same transaction
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return outcomes


__all__ = [
    "NO_CANDIDATE",
    "MachineReason",
    "MachineOutcome",
    "plan",
    "record_extraction",
]

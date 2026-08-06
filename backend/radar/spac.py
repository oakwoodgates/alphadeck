"""The SPAC Radar watcher (slices 1+2) — scan the EDGAR daily index for blank-check TRANSITION
filings, lazily accrete the shell universe, persist events, and match DA-class filings against
every thesis's term set.

Universe = LAZY ACCRETION (options doc Rev 2), no prerequisite run:
  (i)  rows already enriched (``security_master.sector = 'Blank Checks'`` — drafts keep adding);
  (ii) on-demand: an UNKNOWN CIK filing a merger-specific form (425 / S-4 / merger proxy) gets ONE
       cached submissions fetch; SIC says shell or not, and a shell's master row is durably
       enriched (``enrich_for_ciks``) so the set accretes;
  (iii) ``enrich_identity --universe`` stays an optional completeness backstop.
The honest recall trade-off, by design: an 8-K from a never-seen shell is not resolved on demand
(8-K volume is the whole market's; a DA virtually always brings a 425/S-4 that admits the CIK).

Deterministic end-to-end (#3): forms, SIC, and item codes — no LLM anywhere. Fail-visible: a bad
date/CIK/doc is recorded in the result's errors, never silently dropped (#9). Never imports
``calls/`` — radar output is a tape + recommendations, not a trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.edgar.dailyindex import IndexFiling, fetch_daily_index
from ingest.edgar.submissions import fetch_submissions, parse_identity
from radar import matcher, repo
from repositories import thesis_repo
from securities import master
from workbench.enrichment import enrich_for_ciks

BLANK_CHECKS = repo.BLANK_CHECKS

# Merger-specific, low-volume forms: an UNKNOWN filer of one of these earns an on-demand
# submissions fetch (the accretion trigger). Deliberately excludes 8-K and DEF 14A — whole-market
# volume (hundreds to thousands a day) would turn "a handful of fetches" into a crawl.
ACCRETE_FORMS = frozenset({"425", "S-4", "S-4/A", "DEFM14A", "PREM14A"})
# Recorded for KNOWN shells only (the deepening events: votes, terminations, completions).
KNOWN_ONLY_FORMS = frozenset({"8-K", "8-K/A", "DEF 14A", "DEFA14A", "25", "25-NSE"})
WATCH_FORMS = ACCRETE_FORMS | KNOWN_ONLY_FORMS
# DA-class events get term-set matching (slice 2); an 8-K joins when its items include 1.01.
DA_MATCH_FORMS = frozenset({"425", "S-4", "S-4/A", "DEFM14A", "PREM14A"})


@dataclass
class RadarRunResult:
    dates_scanned: list[str] = field(default_factory=list)
    dates_skipped: list[str] = field(default_factory=list)  # weekends/holidays (no index posted)
    filings_seen: int = 0  # watch-form rows considered
    shells_admitted: list[str] = field(default_factory=list)  # CIKs newly classified blank-check
    events_appended: int = 0
    events_unchanged: int = 0
    matches_appended: int = 0
    matches_unchanged: int = 0
    docs_matched: int = 0  # DA-class docs fetched + matched this run
    errors: list[str] = field(default_factory=list)
    edgar_fetches: int = 0

    @property
    def summary(self) -> str:
        parts = [
            f"{len(self.dates_scanned)} days",
            f"{self.filings_seen} watch filings",
            f"+{self.events_appended} events ({self.events_unchanged} unchanged)",
            f"+{len(self.shells_admitted)} shells admitted",
            f"+{self.matches_appended} matches ({self.matches_unchanged} unchanged)",
            f"{self.edgar_fetches} EDGAR fetches",
        ]
        if self.errors:
            parts.append(f"{len(self.errors)} ERRORS")
        return " · ".join(parts)


def _is_no_index_day(e: Exception) -> bool:
    """A missing daily index (weekend/holiday). MEASURED live 2026-08-05: EDGAR's edge answers
    **403 Forbidden** (not 404) for an absent master.idx date — treat both as the quiet no-index
    skip, never an error (loudness marks the exception; a weekend is not one)."""
    return getattr(getattr(e, "response", None), "status_code", None) in (403, 404)


def _resolve_shell(
    client: EdgarClient, cik10: str, submissions_by_cik: dict[str, dict[str, Any] | None]
) -> bool:
    """Is this CIK a blank check, per its own submissions SIC? One cached fetch; a fetch/parse
    failure reads NOT-shell for this run (recorded upstream as an error — never silent)."""
    if cik10 not in submissions_by_cik:
        try:
            subs = fetch_submissions(client, cik10)
            # the enrichment genuine-doc guard: a real submissions doc echoes its cik
            submissions_by_cik[cik10] = subs if subs.get("cik") is not None else None
        except Exception:  # noqa: BLE001 — per-CIK isolation; the caller records the error
            submissions_by_cik[cik10] = None
            raise
    subs = submissions_by_cik[cik10]
    if subs is None:
        return False
    return parse_identity(subs).sector == BLANK_CHECKS


def _items_for(
    client: EdgarClient,
    cik10: str,
    accession: str,
    submissions_by_cik: dict[str, dict[str, Any] | None],
) -> list[str] | None:
    """The 8-K's item codes from the filer's submissions JSON (the ``items`` array parallels
    ``accessionNumber``). None = unknown — the accession isn't in the (possibly TTL-stale) JSON
    yet; the state derive treats unknown as contributing nothing (the Rev 2 honesty rule), and
    the next run's re-scan versions the row when items resolve."""
    if cik10 not in submissions_by_cik:
        try:
            subs = fetch_submissions(client, cik10)
            submissions_by_cik[cik10] = subs if subs.get("cik") is not None else None
        except Exception:  # noqa: BLE001
            submissions_by_cik[cik10] = None
    subs = submissions_by_cik[cik10]
    if subs is None:
        return None
    recent = subs.get("filings", {}).get("recent", {})
    accns = recent.get("accessionNumber", [])
    items = recent.get("items", [])
    for i, a in enumerate(accns):
        if a == accession:
            raw = items[i] if i < len(items) else ""
            return [s.strip() for s in (raw or "").split(",") if s.strip()] or None
    return None


def _filing_index_url(cik10: str, accession: str) -> str:
    nodash = accession.replace("-", "")
    return f"{get_settings().sec_archives_base}/{int(cik10)}/{nodash}/{accession}-index.htm"


def run_spac_radar(
    conn: psycopg.Connection,
    *,
    until: date | None = None,
    days: int = 3,
    allow_live: bool = True,
    user_agent: str | None = None,
    edgar_client: EdgarClient | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    match: bool = True,
) -> RadarRunResult:
    """One radar pass: scan the daily indexes for ``days`` days ending at ``until`` (default
    today), accrete + persist + (optionally) match. Idempotent over a re-scan of the same window
    (append-only if-changed). The caller may pass its own ``edgar_client`` (tests: a fixture-cache
    client with ``allow_live=False``)."""
    client = edgar_client or EdgarClient(allow_live=allow_live, user_agent=user_agent)
    until = until or date.today()
    result = RadarRunResult()
    submissions_by_cik: dict[str, dict[str, Any] | None] = {}
    shells = repo.known_shell_ciks(conn, tenant_id=tenant_id)
    admitted: set[str] = set()
    collected: list[IndexFiling] = []

    for offset in range(days - 1, -1, -1):  # oldest → newest, so accretion helps later days
        d = until - timedelta(days=offset)
        try:
            filings = fetch_daily_index(client, d)
        except CacheMiss:
            result.dates_skipped.append(f"{d} (not cached)")
            continue
        except Exception as e:  # noqa: BLE001 — 403/404 = a no-index day, anything else is an error
            if _is_no_index_day(e):
                result.dates_skipped.append(str(d))
            else:
                result.errors.append(f"{d}: index fetch: {e}")
            continue
        result.dates_scanned.append(str(d))
        seen_accessions: set[str] = set()
        for f in filings:
            if f.form not in WATCH_FORMS or f.accession in seen_accessions:
                continue
            seen_accessions.add(f.accession)
            cik10 = f.cik.zfill(10)
            result.filings_seen += 1
            if cik10 not in shells and f.form in ACCRETE_FORMS:
                try:
                    if _resolve_shell(client, cik10, submissions_by_cik):
                        shells.add(cik10)
                        admitted.add(cik10)
                except Exception as e:  # noqa: BLE001
                    result.errors.append(f"{d} {f.form} CIK {cik10}: submissions: {e}")
                    continue
            if cik10 in shells:
                collected.append(f)

    # canonical master join for every event CIK (padded keys in and out)
    sid_by_cik = master.ids_for_ciks(
        conn, [f.cik.zfill(10) for f in collected], tenant_id=tenant_id
    )
    # durable accretion: a newly-admitted shell WITH a master row gets its identity written now
    # (per-CIK isolated inside; commits its own work), so the next run's known-set query sees it.
    accrete_map = {c: sid_by_cik[c] for c in sorted(admitted) if c in sid_by_cik}
    if accrete_map:
        try:
            enrich_for_ciks(conn, client, accrete_map, tenant_id=tenant_id)
        except Exception as e:  # noqa: BLE001 — accretion is best-effort; events still record
            result.errors.append(f"accretion enrich: {e}")
    result.shells_admitted = sorted(admitted)

    events: list[repo.SpacEvent] = []
    for f in collected:
        cik10 = f.cik.zfill(10)
        items = (
            _items_for(client, cik10, f.accession, submissions_by_cik)
            if f.form.startswith("8-K")
            else None
        )
        events.append(
            repo.SpacEvent(
                cik=cik10,
                company_name=f.company,
                form=f.form,
                filed=f.filed,
                accession=f.accession,
                source_ref=_filing_index_url(cik10, f.accession),
                items=items,
                security_id=sid_by_cik.get(cik10),
            )
        )
    for ev in events:
        if repo.record_event_if_changed(conn, ev, tenant_id=tenant_id):
            result.events_appended += 1
        else:
            result.events_unchanged += 1
    conn.commit()

    if match:
        _match_events(conn, client, events, result, tenant_id=tenant_id)
        conn.commit()

    result.edgar_fetches = client.live_fetches
    return result


def _match_events(
    conn: psycopg.Connection,
    client: EdgarClient,
    events: list[repo.SpacEvent],
    result: RadarRunResult,
    *,
    tenant_id: UUID,
) -> None:
    """Slice 2: run every thesis's term set over each DA-class filing in this window. A match row
    is recorded only when ≥1 term hit (either tier); the append-if-changed keeps re-scans flat."""
    da_events = [
        e
        for e in events
        if e.form in DA_MATCH_FORMS or (e.form.startswith("8-K") and "1.01" in (e.items or []))
    ]
    if not da_events:
        return
    theses = [t for t in thesis_repo.list_all(conn) if t.term_set]
    if not theses:
        return
    for ev in da_events:
        try:
            text, truncated = matcher.fetch_filing_text(client, ev.cik, ev.accession)
        except Exception as e:  # noqa: BLE001 — fail-visible, never fatal to the run
            result.errors.append(f"match doc {ev.accession}: {e}")
            continue
        result.docs_matched += 1
        for t in theses:
            signal_hits, broad_hits = matcher.match_term_set(text, t.term_set)
            if not signal_hits and not broad_hits:
                continue
            m = repo.SpacMatch(
                thesis_id=t.id,
                cik=ev.cik,
                accession=ev.accession,
                matched_signal=signal_hits,
                matched_broad=broad_hits,
                truncated=truncated,
                source_ref=f"{get_settings().sec_archives_base}/{int(ev.cik)}/{ev.accession}.txt",
                filed=ev.filed,
            )
            if repo.record_match_if_changed(conn, m, tenant_id=tenant_id):
                result.matches_appended += 1
            else:
                result.matches_unchanged += 1

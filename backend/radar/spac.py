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
from domain.market_time import market_today
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.edgar.dailyindex import IndexFiling, fetch_daily_index
from ingest.edgar.submissions import fetch_submissions, parse_identity, parse_item_codes
from notify import Notifier, SpacStatusEvent
from radar import matcher, repo
from radar.state import StateEvent, deal_state
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
    status_notifications: int = 0  # ANNOUNCED-only pages emitted this run
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
        # loudness marks the exception — silent when nothing was announced (#7)
        if self.status_notifications:
            parts.append(f"+{self.status_notifications} announced pages")
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
    the next run's re-scan versions the row when items resolve. The items-string parse itself is
    the shared ``submissions.parse_item_codes`` (one parser, also the corporate-event ingest's)."""
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
            return parse_item_codes(items[i] if i < len(items) else None)
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
    notifier: Notifier | None = None,
) -> RadarRunResult:
    """One radar pass: scan the daily indexes for ``days`` days ending at ``until`` (default
    today), accrete + persist + (optionally) match. Idempotent over a re-scan of the same window
    (append-only if-changed). The caller may pass its own ``edgar_client`` (tests: a fixture-cache
    client with ``allow_live=False``).

    ``notifier`` (the nightly cron passes one; the CLI passes none) fires an ANNOUNCED-only Slack page
    per (CIK, thesis) when a shell's deal-state transitions INTO ``announced`` this run AND its DA-class
    filing matched that thesis's term set — deterministic (#3, ``deal_state``), loudness-marks-the-
    exception (#7), and naturally idempotent (the baseline is the prior RECORDED state, so a re-scan over
    already-stored events pages nothing). It runs AFTER the commits below and is fail-open, so a notify
    fault can never roll back committed radar facts nor fail the run."""
    # G1 — THE RECURRING TTL (five minutes): a RECURRING pass never reads a DAYTIME-warm mutable key (no
    # exceptions on the nightly path; the full rationale, including why five minutes rather than zero — the
    # same-pass re-read of one key must stay free — lives at ``ingest.edgar.client.RECURRING_CACHE_TTL_S``).
    # Both keys this leg reads are mutable: TODAY's ``daily-index/master.<date>.idx`` GROWS through the
    # day as filings are accepted, so a daytime scan cached an incomplete index that the 22:30 pass then
    # served — the radar silently missed the evening's DAs; and ``submissions/CIK<10>.json`` is the same
    # mutable index the call path enumerates from (item-code resolution here). Re-pulling costs the
    # ``days``-wide index window (3 files) plus the per-CIK indexes the pass was already fetching (this leg
    # also memoizes submissions per CIK in ``submissions_by_cik``, so it re-reads a key at most once);
    # immutable ``forms/*`` documents still cache forever.
    client = edgar_client or EdgarClient(
        allow_live=allow_live, user_agent=user_agent, cache_ttl_s=RECURRING_CACHE_TTL_S
    )
    until = until or market_today()
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
    # ANNOUNCED-only page baseline: the PRIOR deal-state per CIK, from the RECORDED history read BEFORE
    # this run's appends. This is the idempotency anchor — a re-scan finds these events already stored, so
    # prior == new and nothing pages (the "baseline is the prior recorded state, not a fresh re-compare"
    # rule). Only built when a notifier is listening (the CLI path passes none and pays for no extra read).
    # State is a read-time derive, never stored (radar/state.py).
    prior_by_cik: dict[str, dict[str, StateEvent]] = {}
    if notifier is not None:
        for h in repo.events_for_ciks(conn, sorted({ev.cik for ev in events}), tenant_id=tenant_id):
            prior_by_cik.setdefault(h["cik"], {})[h["accession"]] = StateEvent(
                filed=h["filed"],
                form=h["form"],
                items=tuple(h["items"]) if h["items"] else None,
                accession=h["accession"],
            )

    for ev in events:
        if repo.record_event_if_changed(conn, ev, tenant_id=tenant_id):
            result.events_appended += 1
        else:
            result.events_unchanged += 1
    conn.commit()

    matches: list[repo.SpacMatch] = []
    if match:
        matches = _match_events(conn, client, events, result, tenant_id=tenant_id)
        conn.commit()

    # The ANNOUNCED-only status page (#7 inverse loudness, #3 deterministic): a company that transitioned
    # INTO `announced` this run AND matched a thesis's term set. It runs AFTER the commits above so a notify
    # fault can never roll back committed radar facts (the SAVEPOINT-in-the-cron-loop trap); the whole block
    # is fail-open and fail-visible (#9 — a fault is recorded in errors, never raised).
    if notifier is not None:
        try:
            _notify_announced(
                conn,
                notifier,
                prior_by_cik,
                events,
                matches,
                sid_by_cik,
                result,
                tenant_id=tenant_id,
            )
        except Exception as e:  # noqa: BLE001 — notify is best-effort; the radar's job is the tape
            result.errors.append(f"status notify: {e}")

    result.edgar_fetches = client.live_fetches
    return result


def _match_events(
    conn: psycopg.Connection,
    client: EdgarClient,
    events: list[repo.SpacEvent],
    result: RadarRunResult,
    *,
    tenant_id: UUID,
) -> list[repo.SpacMatch]:
    """Slice 2: run every thesis's term set over each DA-class filing in this window. A match row
    is recorded only when ≥1 term hit (either tier); the append-if-changed keeps re-scans flat.
    Returns EVERY match found this run (whether it appended or was unchanged) — the ANNOUNCED-only
    notifier reads them to intersect a →announced transition with the theses that filing matched."""
    da_events = [
        e
        for e in events
        if e.form in DA_MATCH_FORMS or (e.form.startswith("8-K") and "1.01" in (e.items or []))
    ]
    if not da_events:
        return []
    theses = [t for t in thesis_repo.list_all(conn) if t.term_set]
    if not theses:
        return []
    matches: list[repo.SpacMatch] = []
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
            matches.append(m)
            if repo.record_match_if_changed(conn, m, tenant_id=tenant_id):
                result.matches_appended += 1
            else:
                result.matches_unchanged += 1
    return matches


def _notify_announced(
    conn: psycopg.Connection,
    notifier: Notifier,
    prior_by_cik: dict[str, dict[str, StateEvent]],
    events: list[repo.SpacEvent],
    matches: list[repo.SpacMatch],
    sid_by_cik: dict[str, UUID],
    result: RadarRunResult,
    *,
    tenant_id: UUID,
) -> None:
    """Emit ONE ANNOUNCED page per (transitioned CIK, matched thesis). A CIK transitioned when its
    deal-state moved INTO ``announced`` this run (prior != announced AND new == announced); ``new`` is the
    prior RECORDED history MERGED with this run's observed events (this run's version wins per accession),
    so a re-scan over already-stored events yields prior == new and pages nothing (idempotent). The
    intersection with ``matches`` (this run's DA-class term hits) makes loudness mark the exception (#7): a
    transitioned-but-unmatched shell and an unchanged status both page nothing, and terminated / completed
    moves never reach here (they are not → announced). Enrichment reads are isolated so a fault never
    poisons the committed radar txn."""
    new_by_cik: dict[str, dict[str, StateEvent]] = {c: dict(v) for c, v in prior_by_cik.items()}
    for ev in events:
        new_by_cik.setdefault(ev.cik, {})[ev.accession] = StateEvent(
            filed=ev.filed,
            form=ev.form,
            items=tuple(ev.items) if ev.items else None,
            accession=ev.accession,
        )
    prior_state = {c: deal_state(list(v.values())) for c, v in prior_by_cik.items()}
    new_state = {c: deal_state(list(v.values())) for c, v in new_by_cik.items()}
    transitioned = {
        c
        for c in new_by_cik
        if new_state[c] == "announced" and prior_state.get(c, "searching") != "announced"
    }
    if not transitioned:
        return
    # ONE page per (CIK, thesis): dedup this run's matches on the transitioned CIKs, keeping the most
    # informative (most terms) when a CIK filed more than one DA this run — a single transition, one page.
    best: dict[tuple[str, UUID], repo.SpacMatch] = {}
    for m in matches:
        if m.cik not in transitioned:
            continue
        key = (m.cik, m.thesis_id)
        cur = best.get(key)
        if cur is None or (len(m.matched_signal) + len(m.matched_broad)) > (
            len(cur.matched_signal) + len(cur.matched_broad)
        ):
            best[key] = m
    if not best:
        return
    company_by_cik = {ev.cik: ev.company_name for ev in events}
    url_by_accession = {ev.accession: ev.source_ref for ev in events}
    sids = [sid_by_cik[c] for c in transitioned if c in sid_by_cik]
    try:
        ticker_by_sid = repo.tickers_for(conn, sids, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — an enrichment read must not poison the committed radar txn
        conn.rollback()
        ticker_by_sid = {}
    thesis_name = {t.id: t.name for t in thesis_repo.list_all(conn)}
    for key in sorted(best, key=lambda k: (k[0], str(k[1]))):
        m = best[key]
        name = thesis_name.get(m.thesis_id)
        if name is None:  # a match on an archived/deleted thesis — never a ghost page
            continue
        sid = sid_by_cik.get(m.cik)
        notifier.notify_spac_status(
            SpacStatusEvent(
                cik=m.cik,
                company_name=company_by_cik.get(m.cik, m.cik),
                ticker=ticker_by_sid.get(sid) if sid else None,
                thesis_id=m.thesis_id,
                thesis_name=name,
                accession=m.accession,
                filed=m.filed,
                signal_terms=tuple(m.matched_signal),
                broad_terms=tuple(m.matched_broad),
                url=url_by_accession.get(m.accession, m.source_ref),
            )
        )
        result.status_notifications += 1

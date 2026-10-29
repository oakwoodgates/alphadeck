"""Per-thesis back-half ingest — insider Form 4 + the 8-K item-code tape + the 13D/G ownership tape
+ price EOD (+ fund shares for ETF sleeves) for a thesis's RESOLVED basket (M2a; the fund-shares leg
is ETF net flow F2; the 8-K leg is Band 03 S3; the 13D/G leg is Band 03 S5).

The create/promote path writes only the spine, so a freshly-authored thesis has no CALL-ENGINE facts and
can never WARM/ARM. This CLI fills that gap on demand: for each resolved basket member it ingests the insider
+ 8-K corporate-event + 13D/G activist-stake + price facts the detectors read — plus, for an
``instrument_kind == 'etf'`` member, one fund shares-outstanding sample (DISPLAY-feeding data for the
sleeve's flow read, never a call input). The 8-K and 13D/G legs read the SAME submissions JSON as the
Form 4 leg (parallel arrays / subject-indexed rows), so both tapes cost zero extra enumeration fetches
(the 13D/G leg's bounded per-filing identity fetches are immutable-cached — paid once per accession,
ever). It is:

- **Incremental** — only NEW Form 4 accessions (``existing_accessions``) and only EOD bars newer than the
  latest stored one (``latest_bar_date``). A re-run of an already-current name appends ZERO rows; the
  append-only fact tables never silently grow (the read dedups; we stop the write).
- **Fail-visible** — each leg runs in its own try; a failure is CAPTURED into the name's result and the run
  continues (one bad name never aborts the others), improving on the scanner's bare ``except: pass``. Inside
  the Form 4 leg the tolerance is also PER-FILING: one unfetchable or unparseable filing (pre-2004-06-30
  Form 4s are SGML/text, not XML, and some ancient document URLs 404) is skipped-and-counted with a warning
  (``NameResult.form4_skipped``) — a single bad old filing never blanks the name's whole insider history.
  The live fetchers are polite (``ingest.http.polite_get`` retries 429 / transient 5xx with jittered backoff;
  every SEC request draws from the ONE global SEC budget every process shares — ``ingest/sec_budget.py``, X2 —
  and every Yahoo pull from the process-wide Yahoo gate, X7).
- **Concurrent fetch, serial write** — on a live walk the company's vendor reads are made AHEAD of the walk by
  a small pool (``pipeline/fetch_ahead.py``, ``Settings.ingest_fetch_concurrency``; 1 = the serial walk), and
  each read's outcome is handed to the legs in place of their own first read. Every WRITE still happens on
  the one connection, under the W1 company lock, in basket order — the same facts and versions as serial.
- **No-lookahead** — ``recorded_at`` is left to the DB default ``now()`` (NEVER backdated), so a fact ingested
  today is invisible to an as-of read pinned at an earlier transaction time (the replay guarantee holds).
- **Exact membership (#2)** — it targets the member's RESOLVED ``security_id`` via ``master.get`` (the issuer
  ticker + CIK), never a fresh fuzzy resolve. Tenant comes from the thesis (one thesis = one tenant).

    python -m pipeline.ingest_thesis --thesis <uuid>
    python -m pipeline.ingest_thesis --thesis <uuid> --no-live   # cache-only (no network)
"""

from __future__ import annotations

import argparse
import logging
import xml.etree.ElementTree as ET
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from db.session import connect, name_session
from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.onboarding import OnboardingWindow
from domain.security import Security
from domain.settings import get_settings
from domain.thesis import BasketMember
from ingest.edgar.acceptance import AcceptanceTally, resolve_filing_acceptance
from ingest.edgar.client import EdgarClient
from ingest.edgar.filing_index import FilingIndexResult, ingest_filing_index
from ingest.edgar.form4 import existing_accessions, ingest_form4, other_issuer_cik
from ingest.edgar.form4a import form4_leg_filings, ingest_amendments
from ingest.edgar.form8k import ingest_form8k
from ingest.edgar.schedule13 import ingest_schedule13
from ingest.edgar.submissions import (
    DelistingFiling,
    FormerName,
    NameChangeFiling,
    delisting_filings,
    edgar_tickers,
    entity_name,
    fetch_submissions,
    filed_on_or_after,
    form4_doc_url,
    form8k_filings,
    former_names,
    index_filings,
    name_change_filings,
    schedule13_filings,
)
from ingest.funds.ingest_security import (
    fund_shares_applies,
    ingest_fund_shares_for_security,
    latest_shares_date,
)
from ingest.funds.source import FundSharesSource
from ingest.prices.eod_loader import latest_bar_date
from ingest.prices.ingest_security import ingest_bars_for_security
from ingest.prices.source import PriceSource, YahooPriceSource
from ingest.prices.split_normalize import DIVIDE_BACK_REASONS, REASON_HOLE_AS_SERVED
from ingest.skips import (
    FORM4_OTHER_ISSUER,
    FORM4_UNPARSEABLE,
    SCHEDULE13_IDENTITY,
    SCHEDULE13_OUTBOUND,
    IngestSkip,
    clip_reason,
    is_known,
)
from pipeline.fetch_ahead import FetchAhead, Handoff, fetch_workers, thread_completed
from pipeline.pass_timings import timed

if TYPE_CHECKING:  # the nightly passes one in (S4); the unit itself never builds or imports it
    from pipeline.detect_splits import SplitDetector
from repositories import thesis_repo
from securities import master, sec_tickers

_log = logging.getLogger(__name__)


@dataclass
class NameResult:
    """The per-name outcome — what was appended, what was tolerated-and-skipped, and the captured error
    if a leg failed (fail-visible)."""

    ticker: str | None
    security_id: UUID
    form4_appended: int
    price_bars_appended: int
    error: str | None = None
    form4_skipped: int = 0  # filings skipped per-filing (pre-XML era / unfetchable), never appended
    # P1 — TRANSACTIONS rejected by the sanity bound (`ingest.edgar.form4.implausible_txn_date`): a filer
    # serialized a date that CANNOT be true (a leading-zero year like `0023-03-23`, or a year after the
    # accession's own filing year). A DIFFERENT thing from `form4_skipped`: that is a whole filing we could
    # not read, this is one bad row inside a filing that stored fine. Never clamped, never stored, never
    # silently absorbed — each one already PRINTED in full at the row level; this tally exists only so the
    # cron's per-thesis summary can name the aggregate, and it prints ONLY when nonzero (honest loudness).
    # The count ACCOMPANIES the loud report; it must never replace it (the skip-counter lesson).
    form4_txn_rejected: int = 0
    # overlap bars re-stored because the source RESTATED them (a split re-base; source-strategy A) —
    # the exceptional path, reported loudly only when nonzero
    price_bars_reversioned: int = 0
    # D2 — fresh bars NOT stored because their session had not settled (today's in-progress bar on a pass
    # before `Settings.price_settle_time`); the next settled pass appends them. Summed into ONE quiet count
    # line per run, never one line per name.
    price_bars_withheld: int = 0
    # Q11 / T5 — older restated bars NOT stored because they REVERT to a value the store held before (a
    # vendor flipping a history between split bases) and only one pull has said so; stored once a pull on a
    # later market date agrees. Never silent: ONE quiet line per held name (`print_held_lines`) + the run
    # artifact's per-thesis `price_bars_held`.
    price_bars_held: int = 0
    # S2 — fresh bars READ onto the split list's scale this pass, per reason (``BarsResult.split_normalized``:
    # vendor_raw / vendor_doubled / mis_factored / first_night) and the splits applied to the stored history
    # for the first time. Most write nothing (the vendor's unadjusted series absorbed); never silent: one quiet
    # pass-summary line (`print_split_lines`) + the run artifact's per-thesis `price_bars_split_normalized`.
    price_bars_split_normalized: dict[str, int] = field(default_factory=dict)
    price_split_first_night: tuple[str, ...] = ()
    # the fund-shares leg (ETF net flow, F2): a non-ETF member always contributes 0; a restated
    # same-day count is a new VERSION (reversioned) — loud only when nonzero
    fund_shares_appended: int = 0
    fund_shares_reversioned: int = 0
    # the 8-K corporate-event leg (Band 03 S3): new filings stored on the item-code tape; a
    # reversion = a stored filing whose items RESOLVED (or surface changed) — loud only when nonzero
    form8k_appended: int = 0
    form8k_reversioned: int = 0
    # the 13D/G activist-stake leg (Band 03 S5): new ownership schedules stored on the tape; a
    # reversion = a stored filing whose filer identity RESOLVED (or surface changed); an
    # identity_skipped = a row stored THIS run with NULL filer (fetch/parse failed — never a
    # dropped row, #9). All loud only when nonzero
    sched13_appended: int = 0
    sched13_reversioned: int = 0
    sched13_identity_skipped: int = 0
    # THE TAPE EDGE (G5a): the latest stored EOD bar date for this security AFTER this pass, or None when
    # it has no bars at all. A FACT, never a judgment — whether that edge is too old is decided by
    # `pipeline.daily` against the run's `asof` (`pipeline/tape_health.py`), because a tape that silently
    # ENDED (a vendor symbol drift after a rename, a delisting) appends zero bars with NO error, which is
    # byte-identical to a market holiday. Nothing read it before this field existed, so a dead tape was
    # invisible and every price-driven detector for that name went quietly dark.
    tape_edge: date | None = None
    # THE FUND-SHARES EDGE (F1) — the price tape's twin for the OTHER per-name feed: the latest stored
    # shares-outstanding sample date for this ETF sleeve AFTER this pass, or None when it has none. Same
    # silent-end class: the sampler is a fallback chain whose legs miss independently, and a source page
    # whose STATED as-of date freezes re-reports the same count, which the incremental compare correctly
    # skips — so the leg reports success while the series stands still. A FACT, judged in `pipeline.daily`.
    fund_shares_edge: date | None = None
    # ...and whether the leg APPLIES to this member at all (an ETF sleeve with a ticker —
    # `ingest.funds.ingest_security.fund_shares_applies`, the leg's own gate). Load-bearing and NOT
    # derivable from the edge: an equity has no samples and must never be judged, while an ETF with no
    # samples has a sampler that has never worked and must be. Nor derivable from the leg's result — an
    # unsamplable fund RAISES, and that is precisely the name to report.
    fund_shares_tracked: bool = False
    # THE STOP-CLASSIFICATION FACTS (G5b, reshaped by T1) — what tells a STOPPED price tape apart: a name
    # that legitimately CLOSED (delisted / acquired / deregistered — its tape correctly ended), a RENAME the
    # vendor now prices under another symbol, or a plain feed gap. Three FACTS, no judgment here (the
    # judgment — is the tape stale, and if so which of the three? — is `pipeline.daily`'s, against the run's
    # asof, via `pipeline/tape_health.classify_stop`):
    # - `delisting_filings`: EVERY SEC delisting form this company filed (25 / 25-NSE / 15-12B / 15-12G,
    #   `submissions.delisting_filings`), newest first. ALL of them, not the latest: which one (if any)
    #   explains a stop depends on the tape's last bar — VRME's 2025 warrant delisting is why.
    # - `edgar_tickers`: EDGAR's CURRENT symbol list (`submissions.edgar_tickers`); `()` = EDGAR lists none,
    #   None = not read (no CIK, or the read failed). The rename guard's input; display only, never adopted.
    # - `price_symbol`: the master's vendor-symbol override (#252), so the guard can see the symbol we
    #   actually price under (KYOCY is priced as KYOAY, which EDGAR lists). Identity, copied off the master.
    # Read from the SAME cached submissions the Form 4 leg fetched, so they cost zero extra network.
    delisting_filings: tuple[DelistingFiling, ...] = ()
    edgar_tickers: tuple[str, ...] | None = None
    price_symbol: str | None = None
    # ...and two more inputs to the rename guard (the company-list refresh, 2026-10-24):
    # - `sec_map_tickers`: the CIK's tickers in SEC's current ticker map (the CACHED
    #   company_tickers_exchange.json the weekly refresh keeps current — a disk read, memoized per pass, never
    #   a fetch); `()` = the map lists none, None = not read. The second source when EDGAR's own list is empty
    #   (the PSKY -> SKYD shape).
    # - `price_symbol_basis`: who set `price_symbol` — the refresh's PIN to a renamed company's OLD ticker
    #   (`system:rename-pin`) means the master ticker is SEC's NEW one, which the price leg does not read.
    sec_map_tickers: tuple[str, ...] | None = None
    price_symbol_basis: str | None = None
    # O1 — the SAME failures as ``error``, kept STRUCTURED: one ``(leg, message)`` pair per leg that raised
    # (``form4`` / ``form8k`` / ``schedule13`` / ``price`` / ``fund_shares``; ``master`` for an id missing
    # from the tenant master). ``error`` stays the joined string every existing reader uses (and the R2
    # recording gate counts) — this field changes no behavior; it exists so the cron's run-of-record can
    # persist each reason by leg and page only the NEWLY failing ones (``pipeline/name_errors.py``).
    leg_errors: tuple[tuple[str, str], ...] = ()
    # T3 — a FACT about the price leg's failure: the vendor answered **404 Not Found** for the symbol (it
    # has no such listing). Recorded here, never judged here: whether a 404 is EXPECTED (the name CLOSED, or
    # the operator acknowledged it unpriceable) is decided in ``pipeline.daily`` against the tape
    # classification (``pipeline/price_feed.excuse_expected_misses``). Any other price failure (a timeout, a
    # 5xx, a parse error) leaves this False and stays an error whatever the classification says.
    price_not_found: bool = False
    # T2 — the RENAME EVIDENCE, from the same cached submissions as the stop-classification facts above
    # (zero extra fetches): EDGAR's current entity ``name``, its ``formerNames``, and the 8-Ks carrying item
    # 5.03 (where a name change is reported). Display evidence for the rename recommender
    # (``pipeline/rename_probe.py``) — never a judgment, never a call input.
    edgar_name: str | None = None
    former_names: tuple[FormerName, ...] = ()
    name_change_filings: tuple[NameChangeFiling, ...] = ()
    # Q18 — every FILING a leg skipped this pass (``ingest.skips``): a Form 4 it could not fetch or parse,
    # a 13D-family filing that is the owner's own outbound stake, a 13D/G whose filer identity did not
    # resolve. The counts above are unchanged; this names the filings so the cron can count the ones that
    # skipped the same way last pass instead of re-printing ~440 lines a night, and print only the new.
    ingest_skips: tuple[IngestSkip, ...] = ()
    # W1 — THE PER-COMPANY WRITE LOCK (``db/write_lock.py``). ``lock_waited_s``: how long this unit waited for
    # another writer's hold on the company before ingesting it (0.0 = it was free at once — the common case).
    # ``lock_busy``: set when the company was STILL held after the bounded wait, so this unit did NOT run its
    # legs for it (who held it, how long we waited). NOT an error — the holder is writing the very same facts
    # right now — so it never counts toward ``error`` / the recording gate; the monitor facts (edges, stop
    # facts) are still read, and the name is still reported (#9). ``pipeline.daily`` puts both on the artifact.
    lock_waited_s: float = 0.0
    lock_busy: str | None = None
    # THE FREEZE FLOOR's per-company facts (``pipeline/edgar_coverage.py``). ``edgar_cik``: the CIK this unit's
    # EDGAR legs (Form 4 / 8-K / 13D/G) ran against — None when none ran (no CIK, not in the tenant master, or
    # ``lock_busy``), which keeps the name out of the floor's denominator. ``edgar_completed``: how many EDGAR
    # fetches COMPLETED (a 2xx body — ``EdgarClient.live_completed``) while this name's legs ran; 0 on a cache
    # hit. Facts only — the pass judges them across every thesis (``pipeline.daily.assess_health``).
    edgar_cik: str | None = None
    edgar_completed: int = 0
    # X1 — THE STAGED COLD-INGEST (``pipeline/onboarding.py``). ``filed_since``: this unit read the per-filing
    # legs (Form 4, 13D/G) ONLY for filings filed on/after this date, and skipped the 4/A leg — the name's
    # history is still loading in the onboarding job, outside the nightly window. ``onboarding``: that job's
    # state for the name (``queued`` / ``in_progress`` / ``failed``). Both None for every other name (byte-
    # identical to before X1). NOT an error: ``pipeline.daily`` stamps the thesis's row ``ingest_fresh=false``
    # and lists the name as onboarding — visible, never dropped (#9).
    filed_since: date | None = None
    onboarding: str | None = None
    # ...and ``onboarding_deferred``: the pass had already given its nightly cap of onboarding names
    # (``Settings.onboard_nightly_max_names``) the windowed walk, so THIS one was not ingested at all tonight —
    # no leg ran, no fetch was made (the job loads it outside the window). Still listed, never dropped (#9);
    # the nightly leaves it out of the feed-recency judgment (no price leg ran, so its edge is not news).
    onboarding_deferred: bool = False
    # THE ACCEPTANCE CLOCK (``ingest.edgar.acceptance``): how many of this name's NEW Form 4 / 4/A filings had
    # their listed SEC acceptance time set aside this pass (not on the filing date within EDGAR's hours) —
    # replaced by the filing's own header (``acceptance_header``) or bounded to the end of the filing
    # date (``acceptance_bounded``). Facts for ONE quiet nightly line (``print_acceptance_line``), never a page
    # (#7); 0 when the Form 4 leg failed (its writes rolled back).
    acceptance_header: int = 0
    acceptance_bounded: int = 0
    # THE FILING INDEX (``_filing_index_leg``, migration 0070) — every form in the company's EDGAR index, stored
    # for the research dashboards (display only). A PASSENGER, like the nightly's fundamentals refresh: its
    # failure is recorded HERE (``filing_index_error``), never on ``error`` / ``leg_errors`` (which feed the R2
    # recording gate, ``ingest_fresh`` and the name-error pages) — it can never cost a name its ingest or a
    # thesis its call. ``filing_index_skipped`` / ``filing_index_skips`` = the index rows it could not store, each
    # ``(accession or "row N", reason)``; ``filing_index_accepted_set_aside`` = the appended rows whose listed
    # acceptance time was not trusted (stored NULL). All loud only when nonzero (#7).
    filing_index_appended: int = 0
    filing_index_skipped: int = 0
    filing_index_skips: tuple[tuple[str, str], ...] = ()
    filing_index_accepted_set_aside: int = 0
    filing_index_error: str | None = None


def _tolerable_filing_error(e: Exception) -> bool:
    """Is ``e`` ONE filing's fetch/parse failure (skip-and-count) rather than the whole leg's?

    Tolerated: an unparseable document (pre-2004-06-30 Form 4s are SGML/text, not XML →
    ``ET.ParseError``; a malformed value inside one → ``ValueError``) and a fetch that still fails after
    the polite retries (ancient accessions 404 → ``httpx.HTTPError``). Everything else — DB errors,
    ``CacheMiss`` with live pulls off, a missing User-Agent — is systemic, not one filing's fault, and
    must still abort the leg."""
    if isinstance(e, (ET.ParseError, ValueError)):
        return True
    try:
        import httpx  # lazy, mirroring the clients — the package imports without it
    except ImportError:  # pragma: no cover — with httpx absent, no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPError)


def _is_not_found(e: Exception) -> bool:
    """Did the price vendor answer **404 Not Found** (it has no listing under that symbol)? The ONE shape
    T3 may later excuse — a closed name's or an acknowledged-unpriceable name's vendor 404 (MEASURED on prod
    2026-10-16: GTLS / XOMA / CPRX / AVNS / WONDF / HLLK all fail exactly so). A timeout, a 5xx, a parse
    error or anything else is not this, and stays an error unconditionally."""
    try:
        import httpx  # lazy, mirroring the clients — the package imports without it
    except ImportError:  # pragma: no cover — with httpx absent, no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404


# X5 — each leg's wall time on a metered pass (`pipeline/pass_timings.py`); a no-op anywhere else
@timed("form4")
def _form4_leg(
    conn: psycopg.Connection,
    client: EdgarClient,
    sec: Security,
    *,
    tenant_id: UUID,
    known_skips: Collection[str] | None = None,
    filed_since: date | None = None,
    acceptance: AcceptanceTally | None = None,
) -> tuple[int, int, int, tuple[IngestSkip, ...]]:
    """Ingest only Form 4 accessions not already stored for this security (incremental). Returns
    ``(appended, skipped, txn_rejected, skips)``. Needs the issuer CIK; a name without one contributes no
    insider facts.

    THE ACCEPTANCE CLOCK (``ingest.edgar.acceptance``): each new filing's stored ``accepted`` — the public
    instant every history view reads — is the listed SEC time only when it agrees with the filing date and
    EDGAR's hours; otherwise the filing's own header (one immutable, cached read), else the end of the filing
    date in ET. ``acceptance`` (when given) counts the listed times set aside, Form 4 and 4/A alike, for the
    nightly's quiet line.

    X1 — ``filed_since`` (the nightly's treatment of a name still ONBOARDING, ``pipeline/onboarding.py``): only
    the Form 4s filed on/after that date are read, and the 4/A leg does NOT run — a 4/A links to the original
    it amends, which may be older than the window and so not stored yet; the onboarding job's full walk stores
    the originals first and then the 4/As, in order. ``None`` (every other caller) is the full walk, unchanged.

    PER-FILING tolerance: a filing whose fetch or parse fails (``_tolerable_filing_error``) is skipped
    with a printed warning and counted — never aborting the leg, so one bad old filing can't blank the
    name's whole insider history. A skipped accession is never stored, so every later run re-attempts it
    (and re-counts it) rather than silently marking it done.

    Q18 — every skipped filing is also RETURNED in ``skips`` (``ingest.skips.FORM4_UNPARSEABLE``). Its
    warning prints unless ``known_skips`` (the previous finished pass's skip keys) already holds it: a
    filing that failed the same way last pass is counted by the cron instead of re-printed nightly. Only
    the line changes — the skip, its count and the re-attempt are identical, and an error that is NOT
    tolerable still raises exactly as before (never a new swallow-and-count).

    ``txn_rejected`` (P1) is a DIFFERENT and much narrower thing than ``skipped``: a per-TRANSACTION
    rejection by the sanity bound (``ingest.edgar.form4.implausible_txn_date``) inside a filing that
    otherwise stored fine. It is carried so the cron's per-thesis summary can name it when nonzero; every
    rejection has already printed in full at the row level (the skip-counter lesson — the tally accompanies
    the loud report, it never stands in for it)."""
    if not sec.cik:
        return 0, 0, 0, ()
    seen = existing_accessions(conn, sec.id, tenant_id=tenant_id)
    appended = 0
    skipped = 0
    txn_rejected = 0
    skips: list[IngestSkip] = []
    subs = fetch_submissions(client, sec.cik)
    # THE ONE RULE for which documents this leg reads (``form4_leg_filings`` — the fetch-ahead plans its
    # pre-fetch with the very same call): every Form 4 + every 4/A, or for a name still onboarding (X1) only
    # the Form 4s filed since ``filed_since`` and no 4/A
    originals, amendments = form4_leg_filings(subs, filed_since=filed_since)
    for f in originals:
        if f["accession"] in seen:
            continue  # already have this filing's txns — skip (no duplicate append)
        url = form4_doc_url(sec.cik, f["accession"], f["primary_doc"])
        doc = f["primary_doc"].rsplit("/", 1)[-1]
        try:
            xml = client.get_text(url, f"forms/{f['accession']}/{doc}")
            # R8-A — a filing about ANOTHER issuer (the member is its reporting owner) is never stored;
            # it is recorded as a Q18 skip, so a known one is counted rather than re-printed nightly
            other = other_issuer_cik(xml, sec.cik)
            if other is not None:
                why = f"Form 4 about issuer CIK {other}, not this security (reporting owner)"
                skips.append(
                    IngestSkip(FORM4_OTHER_ISSUER, f["accession"], sec.id, sec.ticker, why)
                )
                if not is_known(known_skips, FORM4_OTHER_ISSUER, f["accession"]):
                    print(f"  {sec.ticker or sec.id} form4 {f['accession']} not stored: {why}")
                continue
            # the SEC acceptance (the public instant on history views) — the ownership XML has none, so it
            # comes from the enumeration, checked against the filing date (``ingest.edgar.acceptance``)
            resolved = resolve_filing_acceptance(client, sec.cik, f)
            one = ingest_form4(
                conn,
                sec.id,
                xml,
                f["accession"],
                tenant_id=tenant_id,
                accepted=resolved.at,
                member_cik=sec.cik,
            )
            if acceptance is not None:
                acceptance.add(resolved)  # counted once the filing parsed and stored
            appended += one.appended
            txn_rejected += one.rejected
        except Exception as e:
            # A tolerated error can only fire BEFORE this filing's first row: parse_form4 fully parses
            # the doc before ingest_form4 appends anything (append failures are DB errors → re-raised),
            # so a skipped filing never leaves partial rows behind.
            if not _tolerable_filing_error(e):
                raise
            skipped += 1
            skips.append(
                IngestSkip(FORM4_UNPARSEABLE, f["accession"], sec.id, sec.ticker, clip_reason(e))
            )
            if not is_known(known_skips, FORM4_UNPARSEABLE, f["accession"]):
                print(
                    f"  warn: {sec.ticker or sec.id} form4 {f['accession']} ({f['filed']}) "
                    f"skipped: {e}"
                )
    if not amendments:
        # no 4/A to read: none filed (``ingest_amendments`` would return at once), or X1's windowed
        # (onboarding) read, which stores no 4/A — its original may not be stored yet (``form4_leg_filings``)
        return appended, skipped, txn_rejected, tuple(skips)
    # R8-A — the Form 4/A leg (``ingest.edgar.form4a``): AFTER tonight's originals, from the SAME cached
    # submissions document; stored as filed + linked, never written into fact_insider_txn
    amend, amend_skips = ingest_amendments(
        conn,
        client,
        sec.id,
        sec.cik,
        sec.ticker,
        subs,
        tenant_id=tenant_id,
        known_skips=known_skips,
        tolerable=_tolerable_filing_error,
        acceptance=acceptance,
    )
    skips.extend(amend_skips)
    skipped += len(amend.skipped)
    if amend.stored:
        # R8-B — a resolvable 4/A is applied by the system as it is stored; an ambiguous one (or an unresolved
        # row match) is not
        applied = sum(1 for a in amend.stored if a.link is not None and a.link.auto_resolved)
        tail = f", {applied} applied by the system" if applied else ""
        tail += f", {amend.pending} for the operator (Admin)" if amend.pending else ""
        print(f"  {sec.ticker or sec.id}: +{len(amend.stored)} form 4/A stored + linked{tail}")
    return appended, skipped, txn_rejected, tuple(skips)


@timed("form8k")
def _form8k_leg(
    conn: psycopg.Connection, client: EdgarClient, sec: Security, *, tenant_id: UUID
) -> tuple[int, int]:
    """Ingest the security's 8-K item-code tape (Band 03 S3) from its submissions JSON. Returns
    ``(appended, reversioned)``. Needs the issuer CIK; a name without one contributes no events.

    Reads the SAME submissions document the Form 4 leg fetches (the client's cache makes the second
    read free), so the whole tape costs zero extra fetches — the ``items`` array parallels
    ``accessionNumber``, no per-filing documents. Append-if-changed inside ``ingest_form8k``: a
    re-run of an unchanged tape appends ZERO rows; a stored filing whose items RESOLVE (NULL -> the
    real codes) gets a new version (never an UPDATE)."""
    if not sec.cik:
        return 0, 0
    filings = form8k_filings(fetch_submissions(client, sec.cik))
    res = ingest_form8k(conn, sec.id, sec.cik, filings, tenant_id=tenant_id)
    return res.appended, res.reversioned


@timed("schedule13")
def _schedule13_leg(
    conn: psycopg.Connection,
    client: EdgarClient,
    sec: Security,
    *,
    tenant_id: UUID,
    known_skips: Collection[str] | None = None,
    filed_since: date | None = None,
) -> tuple[int, int, int, tuple[IngestSkip, ...]]:
    """Ingest the security's 13D/G activist-stake tape (Band 03 S5) from its OWN submissions JSON —
    the S5-verified subject-indexing path. Returns ``(appended, reversioned, identity_skipped, skips)``.
    Needs the issuer CIK; a name without one contributes no stakes. X1 — ``filed_since`` reads only the
    schedules filed on/after that date (a name still onboarding; ``None`` = the full walk, unchanged).

    Enumeration reads the SAME submissions document the Form 4 / 8-K legs fetch (cache-free second
    read); the activist's identity costs one bounded, immutable-cached document fetch per filing
    (``ingest_schedule13``'s depth strategy). Append-if-changed: an unchanged tape re-run appends
    ZERO rows; a stored filing whose identity RESOLVES gets a new version (never an UPDATE); an
    identity failure stores the row with NULL filer and counts loud (#9).

    Q18 — ``skips`` names the outbound (owner-filed) and identity-failed filings; ``known_skips`` mutes the
    lines of the ones that skipped the same way last pass (``ingest_schedule13``)."""
    if not sec.cik:
        return 0, 0, 0, ()
    filings = filed_on_or_after(schedule13_filings(fetch_submissions(client, sec.cik)), filed_since)
    res = ingest_schedule13(
        conn, sec.id, sec.cik, filings, client, tenant_id=tenant_id, known_skips=known_skips
    )
    skips = tuple(
        IngestSkip(SCHEDULE13_OUTBOUND, acc, sec.id, sec.ticker, why) for acc, why in res.outbound
    ) + tuple(
        IngestSkip(SCHEDULE13_IDENTITY, acc, sec.id, sec.ticker, why)
        for acc, why in res.identity_failures
    )
    return res.appended, res.reversioned, res.identity_skipped, skips


@timed("filing_index")
def _filing_index_leg(
    conn: psycopg.Connection, client: EdgarClient, sec: Security, *, tenant_id: UUID
) -> FilingIndexResult:
    """Store the security's EDGAR filing index (migration 0070): every form ``filings.recent`` lists — an S-1, a
    424B5, a DEF 14A, a 10-Q — for the research dashboards' Filing Stream (DISPLAY data; no detector reads it).
    Needs the issuer CIK; a name without one contributes nothing (the other EDGAR legs' guard).

    Reads the SAME submissions document the Form 4 / 8-K / 13D/G legs read moments earlier (the client's cache
    makes it free), so it costs ZERO extra SEC requests on a normal pass. Never windowed by an onboarding
    ``filed_since``: the read is free and a cold name's index (at most ~1,000 rows) is one batched insert.
    INSERT-ONLY (``ingest.edgar.filing_index``): only the not-yet-stored accessions append — a stored one is
    never re-versioned, so a re-run appends ZERO rows. The caller runs it as a PASSENGER (``_ingest_one``).
    """
    if not sec.cik:
        return FilingIndexResult()
    rows = index_filings(fetch_submissions(client, sec.cik))
    return ingest_filing_index(conn, sec.id, sec.cik, rows, tenant_id=tenant_id)


# The price leg lives in ``ingest.prices.ingest_security.ingest_bars_for_security`` now — ONE
# implementation shared with the Workbench's per-name / per-section pull (the finalize screen needs
# real caps + hints BEFORE promote), so the incremental / cache-first / no-lookahead rules can't fork.


def ingest_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
    allow_live: bool = True,
    force_refresh: bool = False,
    user_agent: str | None = None,
    price_source: PriceSource | None = None,
    edgar_client: EdgarClient | None = None,
    fund_source: FundSharesSource | None = None,
    only_security_ids: Collection[UUID] | None = None,
    now: datetime | None = None,
    known_skips: Collection[str] | None = None,
    lock_wait_s: float | None = None,
    onboarding: Mapping[UUID, OnboardingWindow] | None = None,
    split_detector: SplitDetector | None = None,
) -> list[NameResult]:
    """Ingest insider + price facts — and, for an ETF sleeve, a fund shares-outstanding sample — for
    each RESOLVED basket member of ``thesis_id``.

    W1 — ONE WRITER PER COMPANY: each member's legs run while ``conn`` holds that company's write lock
    (``db/write_lock.py``), so this unit (the nightly's, the Workbench on-promote job's, the CLI's) can never
    interleave a read-compare-append with another writer on the same company. A company another writer holds
    is DEFERRED to the end of the walk, then waited for at most ``lock_wait_s`` (default
    ``Settings.ingest_lock_wait_s``); one still held after that is reported with ``lock_busy`` and its legs are
    not run by this unit (the holder is writing them) — the result list still carries it, in basket order.

    Per member: resolve the id to the master row (skip unresolved / foreign ids), then run the Form 4
    leg, the 8-K corporate-event leg, the price leg, and the fund-shares leg — EACH in its own try,
    COMMITTING on success and ROLLING BACK on
    failure, so one leg's error never discards the others' work and never aborts the run (the error is
    captured into the name's ``NameResult``). The fund-shares leg is gated on the member's
    ``instrument_kind == 'etf'`` inside ``ingest_fund_shares_for_security`` (the form4 leg's no-CIK
    mirror: a non-ETF member contributes no shares sample and never touches the source). Incremental +
    no-lookahead (see the module docstring). Returns one ``NameResult`` per member that had a resolved id.

    Each name's result also carries the two RECENCY FACTS the nightly monitor judges — the latest stored
    price bar date (``tape_edge``) and, for an ETF sleeve only, the latest stored fund-shares sample date
    (``fund_shares_edge`` + ``fund_shares_tracked``). Both are read AFTER their leg and outside its
    try/except, and neither is judged here: this unit has no ``asof`` by design, so "is that too old?" is
    ``pipeline.daily``'s call (``pipeline/tape_health.py``).

    ``force_refresh`` makes the price + fund-shares legs bypass a stale cache hit (the recurring/daily
    path sets it; see ``eod_loader.fetch_eod``). ``now`` (AWARE; default the UTC wall clock) is the price
    leg's SETTLE-guard clock (D2 — ``ingest.prices.settle``): a bar whose session has not settled by then
    is withheld and counted in ``price_bars_withheld``, never stored. ``price_source`` is the swappable
    EOD source (defaults to Yahoo); ``fund_source`` the swappable shares source (defaults to issuer-first + fallback).
    ``edgar_client`` lets the caller INJECT the client (else one is constructed) so it can read
    ``client.live_fetches`` afterwards — the daily cron does, to record the freeze-detector count.

    ``only_security_ids`` SCOPES the run to the given resolved members (the on-promote kick for a
    promote's NEWLY-ADDED names — the cost thread: the cron walks the whole basket nightly, so an
    interactive kick pays only for the names actually waiting). ``None`` (the default — the CLI and the
    cron) walks every resolved member, byte-identical to before the parameter existed. Scope narrows
    WHICH members are walked, never what a walked member ingests.

    ``known_skips`` (Q18) is the previous finished pass's set of skipped-filing keys
    (``ingest.skips.skip_key``), which only the daily cron passes: a filing that skipped the same way last
    pass is recorded on ``NameResult.ingest_skips`` but its line is not printed again. ``None`` prints every
    skip, as before. It changes LOGGING only — never what is stored, skipped, counted or raised.

    ``onboarding`` (X1, only the daily cron passes it) maps the members whose HISTORY is still loading in the
    staged onboarding job (``pipeline/onboarding.py``) to their window: such a member is walked like any other
    (its lock, its 8-K / price / fund-shares legs, its monitor facts), but its per-filing legs read only the
    filings filed on/after ``filed_since`` and its 4/A leg waits for the full load — so a cold name costs the
    pass about what a warm one does, never its 300-600 s history walk (MEASURED). Its result carries ``filed_since`` and
    ``onboarding``. A member whose window says ``defer`` (past the pass's onboarding cap) is not walked at all —
    no lock, no fetch, no write — and comes back as a listed ``onboarding_deferred`` result. ``None`` / an absent
    member = the full walk, byte-identical to before X1.
    """
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis {thesis_id} not found")
    client = edgar_client or EdgarClient(allow_live=allow_live, user_agent=user_agent)
    source = price_source or YahooPriceSource()
    wait_s = get_settings().ingest_lock_wait_s if lock_wait_s is None else lock_wait_s
    members = [
        m
        for m in thesis.basket
        # an unresolved placement has no exact member to ingest against; a scoped run (the on-promote
        # kick) walks only the requested members
        if m.security_id is not None
        and (only_security_ids is None or m.security_id in only_security_ids)
    ]

    def one(m: BasketMember, handoff: Handoff | None = None) -> NameResult:
        # THE FETCH-AHEAD's handoff (``pipeline/fetch_ahead.py``): this company's vendor reads were already
        # made by a worker; its legs consume each read's outcome in place of their own first network read
        # of the same key — identical inputs, identical writes. ``None`` = the serial walk's own reads.
        c, s, n = client, source, now
        if handoff is not None:
            c, s = handoff.edgar_client(client), handoff.price_source(source)
            if n is None and handoff.price_started_at is not None:
                # D2: the settle guard judges the pull at the instant it was made
                n = handoff.price_started_at
        # the freeze floor's per-company fact: the EDGAR fetches that COMPLETED for THIS name's reads — the
        # ones its legs made on this (the writer's) thread, plus the ones its fetch-ahead tasks made on the
        # workers (per-thread counts: the client is shared, and other companies' fetches complete meanwhile)
        before = thread_completed(client)
        # X1 — a name still onboarding: its per-filing legs read only the window (the fetch-ahead planned
        # exactly the same documents — ``ingest.edgar.form4a.form4_leg_filings``, the one rule both use)
        window = onboarding.get(m.security_id) if onboarding and m.security_id else None
        r = _ingest_one(
            conn,
            c,
            s,
            m,
            tenant_id=thesis.tenant_id,
            allow_live=allow_live,
            force_refresh=force_refresh,
            fund_source=fund_source,
            now=n,
            known_skips=known_skips,
            filed_since=window.filed_since if window else None,
            split_detector=split_detector,
        )
        r.edgar_completed = thread_completed(client) - before
        if handoff is not None:
            r.edgar_completed += handoff.completed
        if window is not None:  # X1 — the onboarding treatment, stamped (visible, never an error)
            r.filed_since, r.onboarding = window.filed_since, window.status
        return r

    # THE FETCH-AHEAD (X2/X7 + the measured concurrency lever, 2026-10-21): on a live walk with
    # ``Settings.ingest_fetch_concurrency`` > 1, a small pool fetches each upcoming company's submissions,
    # new Form 4 / 4-A documents and price pull AHEAD of this walk, inside the global SEC budget and the
    # Yahoo gate. FETCHING only: every write below still happens HERE, on this one connection, under the
    # company's W1 lock, in basket order. ``1`` (or a --no-live walk) = no pool: the serial walk verbatim.
    workers = fetch_workers(allow_live)
    ahead = (
        FetchAhead(
            conn,
            client,
            source,
            members,
            tenant_id=thesis.tenant_id,
            allow_live=allow_live,
            force_refresh=force_refresh,
            workers=workers,
            fetch_submissions=fetch_submissions,
            # X1 — a deferred name gets no fetch job; a windowed one fetches only its window
            onboarding=onboarding,
        )
        if workers > 1
        else None
    )

    # W1 — THE PER-COMPANY WRITE LOCK (``db/write_lock.py``): a company's legs run only while this
    # connection holds its lock, so a concurrent writer (the nightly vs a Workbench ingest vs a CLI run) can
    # never interleave a read-compare-append on the same company. FIRST PASS: every company is TRIED without
    # waiting, in basket order; a held one is DEFERRED, so a busy company never delays the free ones.
    results: list[NameResult | None] = [None] * len(members)
    deferred: list[int] = []
    # a deferred company's worker completions: its handoff is dropped, but what the workers fetched for it
    # this walk is on disk and is what its second-pass legs read — so it still counts as fresh SEC data
    deferred_completed: dict[int, int] = {}
    try:
        for i, m in enumerate(members):
            window = onboarding.get(m.security_id) if onboarding and m.security_id else None
            if window is not None and window.defer:
                # X1 — past the pass's onboarding cap: listed, not walked (no lock, no fetch, no write —
                # the fetch-ahead planned no job for it either)
                results[i] = NameResult(
                    m.ticker,
                    m.security_id,
                    0,
                    0,
                    onboarding=window.status,
                    onboarding_deferred=True,
                )
                continue
            handoff = None
            if ahead is not None:
                # a clean (never an aborted) transaction for the planning reads
                _end_transaction(conn)
                handoff = ahead.take(i)
            _end_transaction(conn)
            try:
                lock = acquire_company_lock(conn, m.security_id, wait_s=0)
            except CompanyLockBusy:
                # deferred: its handed-over reads are dropped — after the wait its legs read for
                # themselves, like the serial walk always did
                deferred.append(i)
                if handoff is not None:
                    deferred_completed[i] = handoff.completed
                continue
            with lock:
                results[i] = one(m, handoff)
    finally:
        if ahead is not None:
            ahead.close()  # no worker outlives the walk; every count is final when we return
    # SECOND PASS: the deferred companies, each with a BOUNDED wait (``Settings.ingest_lock_wait_s``). One
    # still held after it is NOT ingested by this unit — a ``lock_busy`` result carrying the holder and the
    # monitor facts, never an error and never a dropped name (#9): the holder is writing those very facts.
    # A company placed in N segments is N basket rows; once its wait has run out it is not waited for again.
    busy: dict[UUID, CompanyLockBusy] = {}
    for i in deferred:
        m = members[i]
        sid = m.security_id
        assert sid is not None  # filtered above
        _end_transaction(conn)  # never wait "idle in transaction"
        if sid not in busy:
            try:
                lock = timed("company_lock")(acquire_company_lock)(conn, sid, wait_s=wait_s)
            except CompanyLockBusy as exc:
                busy[sid] = exc
        if sid in busy:
            held = _held_result(conn, client, m, busy[sid], tenant_id=thesis.tenant_id)
            window = onboarding.get(sid) if onboarding else None
            # X1 — still listed as onboarding, even when another writer held it
            if window is not None:
                held.onboarding = window.status
            results[i] = held
            continue
        with lock:
            r = one(m)
            r.edgar_completed += deferred_completed.get(i, 0)
            r.lock_waited_s = round(lock.waited_s, 1)
            results[i] = r
    return [r for r in results if r is not None]


def _end_transaction(conn: psycopg.Connection) -> None:
    """End whatever transaction the connection has open before a company-lock attempt, so a lock wait never
    sits "idle in transaction" and an attempt never runs inside an aborted one. COMMIT, never a discard: every
    leg already committed its own writes, so what is open here is the previous name's monitor READS (or the
    caller's reads before the walk); an aborted transaction (a failed monitor read) can only be rolled back.
    """
    if conn.info.transaction_status == TransactionStatus.INERROR:
        conn.rollback()
    else:
        conn.commit()


def _held_result(
    conn: psycopg.Connection,
    client: EdgarClient,
    m: BasketMember,
    busy: CompanyLockBusy,
    *,
    tenant_id: UUID,
) -> NameResult:
    """The result for a company another writer held past the bounded wait: NO leg ran (nothing appended, no
    error), ``lock_busy`` names the holder, and the MONITOR facts are still read — they are reads, and without
    them the tape monitor would judge a missing edge as stale. Visible on the artifact; never dropped (#9).
    """
    assert m.security_id is not None
    note = str(busy)
    print(f"  {m.ticker or m.security_id}: NOT ingested by this writer — {note}")
    sec = master.get(conn, m.security_id, tenant_id=tenant_id)
    if sec is None:
        return NameResult(
            m.ticker,
            m.security_id,
            0,
            0,
            "not in tenant master",
            leg_errors=(("master", "not in tenant master"),),
            lock_waited_s=round(busy.waited_s, 1),
            lock_busy=note,
        )
    stop = _stop_facts(client, sec)
    fund_shares_edge, fund_shares_tracked = _fund_edge(conn, sec, tenant_id=tenant_id)
    return NameResult(
        sec.ticker,
        sec.id,
        0,
        0,
        tape_edge=_tape_edge(conn, sec, tenant_id=tenant_id),
        fund_shares_edge=fund_shares_edge,
        fund_shares_tracked=fund_shares_tracked,
        delisting_filings=stop.delisting,
        edgar_tickers=stop.listed,
        price_symbol=sec.price_symbol,
        price_symbol_basis=sec.price_symbol_basis,
        sec_map_tickers=stop.mapped,
        edgar_name=stop.current_name,
        former_names=stop.formers,
        name_change_filings=stop.renames,
        lock_waited_s=round(busy.waited_s, 1),
        lock_busy=note,
    )


@dataclass(frozen=True)
class _StopFacts:
    """The stop-classification + rename evidence read off a name's cached submissions (see ``_stop_facts``)."""

    delisting: tuple[DelistingFiling, ...] = ()
    listed: tuple[str, ...] | None = None
    current_name: str | None = None
    formers: tuple[FormerName, ...] = ()
    renames: tuple[NameChangeFiling, ...] = ()
    mapped: tuple[str, ...] | None = None


def _tape_edge(conn: psycopg.Connection, sec: Security, *, tenant_id: UUID) -> date | None:
    """THE TAPE EDGE (G5a) — the latest stored bar date, or None. Its OWN try: this is a monitor reading a
    fact, so a fault here degrades to None — it must never blank a name's whole result. Cheap: one indexed
    MAX(d) per name. The JUDGMENT (is this edge too old?) is NOT made here — ``pipeline.daily`` makes it
    against the run's asof (``pipeline/tape_health.py``), which is why this unit needs no ``asof``.
    """
    try:
        return latest_bar_date(conn, sec.id, tenant_id=tenant_id)
    except Exception as e:  # noqa: BLE001 — a monitor read never costs a name its result
        print(f"  warn: {sec.ticker or sec.id} tape-edge read failed: {e}")
        return None


def _stop_facts(client: EdgarClient, sec: Security) -> _StopFacts:
    """THE STOP-CLASSIFICATION FACTS (G5b/T1) — every SEC delisting form (25/25-NSE/15-12B/15-12G) and
    EDGAR's current ticker list, from the name's submissions, so a STOPPED tape can be told apart: closed,
    renamed, or a feed gap. FREE on a daily pass: the Form 4 leg already fetched + cached this CIK's
    submissions, so this is a cache-hit disk read + two list walks, exactly like the 8-K / 13D legs (a cache
    hit never touches the network or the freeze counter). Gated on ``sec.cik`` — a name with no CIK has no
    submissions (the Form 4 leg's own guard). Its OWN try, degrade to the empty defaults: this is a monitor
    read feeding a classification, so a fault must never cost a name its result, and "no evidence" is the
    SAFE default — a name without positive delisting evidence stays the loud "stale — repair", never the
    quiet "closed" (erring toward visible, #7/#9). NOT judged here — ``pipeline.daily`` classifies a stale
    tape from these. T2 — the RENAME EVIDENCE rides the same read (the company's current name, its former
    names, and its item-5.03 8-Ks): three more walks of the cached doc, display evidence only. The CIK's
    tickers in SEC's CACHED ticker map ride along (``mapped`` — the rename guard's second source; a disk read
    memoized per file version, never a fetch; its own try, so a damaged map costs only that input).
    """
    if not sec.cik:
        return _StopFacts()
    try:
        mapped = sec_tickers.cached_tickers_for_cik(sec.cik)
    except Exception:  # noqa: BLE001 — a monitor input; "not read" is the safe default
        mapped = None
    try:
        subs = fetch_submissions(client, sec.cik)
        return _StopFacts(
            delisting=delisting_filings(subs),
            listed=edgar_tickers(subs),
            current_name=entity_name(subs),
            formers=former_names(subs),
            renames=name_change_filings(subs),
            mapped=mapped,
        )
    except Exception as e:  # noqa: BLE001 — a monitor read never costs a name its result
        print(f"  warn: {sec.ticker or sec.id} delisting read failed: {e}")
        return _StopFacts(mapped=mapped)


def _fund_edge(
    conn: psycopg.Connection, sec: Security, *, tenant_id: UUID
) -> tuple[date | None, bool]:
    """THE FUND-SHARES EDGE (F1) — ``(edge, tracked)``: the latest stored shares sample date for an ETF
    sleeve, and whether the leg APPLIES at all. GATED on ``fund_shares_applies``, which is load-bearing twice
    over: it keeps ~500 equity members from paying a pointless query every night, and it stops them being
    JUDGED at all (an equity's edge is None, and None means STALE to the rule — ungated, the monitor would
    report the entire basket). Its OWN try: a monitor read never costs a name its result."""
    tracked = fund_shares_applies(sec)
    if not tracked:
        return None, False
    try:
        return latest_shares_date(conn, sec.id, tenant_id=tenant_id), True
    except Exception as e:  # noqa: BLE001 — same degrade-to-None discipline as the tape edge
        print(f"  warn: {sec.ticker or sec.id} fund-shares-edge read failed: {e}")
        return None, True


def _ingest_one(
    conn: psycopg.Connection,
    client: EdgarClient,
    source: PriceSource,
    m: BasketMember,
    *,
    tenant_id: UUID,
    allow_live: bool,
    force_refresh: bool,
    fund_source: FundSharesSource | None,
    now: datetime | None,
    known_skips: Collection[str] | None,
    filed_since: date | None = None,
    split_detector: SplitDetector | None = None,
) -> NameResult:
    """ONE member's legs — run by ``ingest_thesis`` while it holds the company's write lock (W1). Resolves the
    id to the master row, then the Form 4, 8-K, 13D/G, price and fund-shares legs, EACH in its own try,
    committing on success and rolling back on failure (see ``ingest_thesis``). ``filed_since`` (X1) windows
    the per-filing legs for a name still onboarding; ``None`` is the full walk."""
    assert m.security_id is not None
    sec = master.get(conn, m.security_id, tenant_id=tenant_id)
    if sec is None:
        # a placed id not in this tenant's master (shouldn't happen post-promote-guard) — report it
        return NameResult(
            m.ticker,
            m.security_id,
            0,
            0,
            "not in tenant master",
            leg_errors=(("master", "not in tenant master"),),
        )
    # each failed leg is captured TWICE on purpose: the joined `errs` string is the existing
    # `NameResult.error` (unchanged); `leg_errs` keeps the same failures by leg for the run artifact (O1)
    errs: list[str] = []
    leg_errs: list[tuple[str, str]] = []
    f4 = 0
    f4_skipped = 0
    f4_txn_rejected = 0
    skips: list[IngestSkip] = []
    acceptance = AcceptanceTally()
    try:
        f4, f4_skipped, f4_txn_rejected, f4_skips = _form4_leg(
            conn,
            client,
            sec,
            tenant_id=tenant_id,
            known_skips=known_skips,
            filed_since=filed_since,
            acceptance=acceptance,
        )
        skips.extend(f4_skips)
        conn.commit()
    except Exception as e:  # noqa: BLE001 — fail-visible: capture, roll back this leg, keep going
        conn.rollback()
        errs.append(f"form4: {e}")
        leg_errs.append(("form4", str(e)))
        acceptance = AcceptanceTally()  # nothing it counted was stored
    f8k_appended, f8k_reversioned = 0, 0
    try:
        f8k_appended, f8k_reversioned = _form8k_leg(conn, client, sec, tenant_id=tenant_id)
        conn.commit()
    except Exception as e:  # noqa: BLE001 — same per-leg isolation as the form4 leg
        conn.rollback()
        errs.append(f"form8k: {e}")
        leg_errs.append(("form8k", str(e)))
    s13_appended, s13_reversioned, s13_skipped = 0, 0, 0
    try:
        s13_appended, s13_reversioned, s13_skipped, s13_skips = _schedule13_leg(
            conn, client, sec, tenant_id=tenant_id, known_skips=known_skips, filed_since=filed_since
        )
        skips.extend(s13_skips)
        conn.commit()
    except Exception as e:  # noqa: BLE001 — same per-leg isolation as the form4 leg
        conn.rollback()
        errs.append(f"schedule13: {e}")
        leg_errs.append(("schedule13", str(e)))
    # THE FILING INDEX (migration 0070) — every form the company's EDGAR index lists, off the submissions the
    # legs above just read (zero extra requests). A PASSENGER: its own transaction, and a failure lands on
    # ``filing_index_error`` ONLY — never on ``errs`` / ``leg_errs`` (the R2 recording gate, ``ingest_fresh``
    # and the name-error pages read those), so a display table can never cost a name its ingest.
    fi = FilingIndexResult()
    fi_error: str | None = None
    try:
        fi = _filing_index_leg(conn, client, sec, tenant_id=tenant_id)
        conn.commit()
    except Exception as e:  # noqa: BLE001 — a passenger: recorded, rolled back, never a name error
        conn.rollback()
        fi = FilingIndexResult()  # nothing it counted was stored
        fi_error = str(e) or type(e).__name__
    # S4 — THE SPLIT DETECTION (``pipeline/detect_splits.py``, the nightly only): after the 8-K leg stored
    # today's filings and BEFORE the price leg, so a split filed today with a second source is on the split
    # list before this company's price leg and tonight's call. Its own transaction; a fault is the
    # detector's to record (run artifact + report) — never a name error, never a stale ingest.
    if split_detector is not None:
        try:
            timed("split_detect")(split_detector.check)(conn, client, sec, tenant_id=tenant_id)
            conn.commit()
        except Exception as e:  # noqa: BLE001 — a detection fault never costs the name its ingest
            conn.rollback()
            split_detector.fault(sec, e)
    px_appended, px_reversioned, px_withheld, px_held = 0, 0, 0, 0
    px_split: dict[str, int] = {}
    px_first_night: tuple[str, ...] = ()
    px_not_found = False
    try:
        bars = timed("price")(ingest_bars_for_security)(  # X5 — the price leg's wall time
            conn,
            sec,
            tenant_id=tenant_id,
            allow_live=allow_live,
            force_refresh=force_refresh,
            source=source,
            now=now,
        )
        px_appended, px_reversioned, px_withheld, px_held = (
            bars.appended,
            bars.reversioned,
            bars.withheld,
            bars.held,
        )
        px_split, px_first_night = dict(bars.split_normalized), bars.split_first_night
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        errs.append(f"price: {e}")
        leg_errs.append(("price", str(e)))
        px_not_found = _is_not_found(e)  # T3 — a FACT (the vendor 404'd), judged in pipeline.daily
    # THE TAPE EDGE (G5a) — read AFTER the price leg so an appended tail counts, and OUTSIDE its
    # try/except so a name whose price leg FAILED still reports where its stored tape actually ends (a
    # failing name and a dead tape are different facts; the monitor must see both). See `_tape_edge`.
    tape_edge = _tape_edge(conn, sec, tenant_id=tenant_id)
    # THE STOP-CLASSIFICATION FACTS (G5b/T1) + the T2 rename evidence, off the submissions the Form 4 leg
    # just cached. See `_stop_facts`.
    stop = _stop_facts(client, sec)
    fs_appended, fs_reversioned = 0, 0
    try:
        shares = timed("fund_shares")(ingest_fund_shares_for_security)(  # X5
            conn,
            sec,
            tenant_id=tenant_id,
            allow_live=allow_live,
            force_refresh=force_refresh,
            source=fund_source,
        )
        fs_appended, fs_reversioned = shares.appended, shares.reversioned
        conn.commit()
    except Exception as e:  # noqa: BLE001 — fail-visible: a fund with no samplable source is a
        conn.rollback()  # captured error on ITS NameResult, never a silent omission (#7/#9)
        errs.append(f"fund_shares: {e}")
        leg_errs.append(("fund_shares", str(e)))
    # THE FUND-SHARES EDGE (F1) — the tape-edge read's twin, placed AFTER the fund-shares leg for the
    # same reason: a sample appended by this pass must count, or a healthy sleeve would read one pass
    # stale forever. OUTSIDE the leg's try, so an ETF whose sampler RAISED still reports where its
    # stored series actually ends — a failing leg and a dead series are different facts and the monitor
    # must see both. See `_fund_edge`.
    fund_shares_edge, fund_shares_tracked = _fund_edge(conn, sec, tenant_id=tenant_id)
    return NameResult(
        sec.ticker,
        sec.id,
        f4,
        px_appended,
        "; ".join(errs) or None,
        f4_skipped,
        form4_txn_rejected=f4_txn_rejected,
        price_bars_reversioned=px_reversioned,
        price_bars_withheld=px_withheld,
        price_bars_held=px_held,
        price_bars_split_normalized=px_split,
        price_split_first_night=px_first_night,
        fund_shares_appended=fs_appended,
        fund_shares_reversioned=fs_reversioned,
        form8k_appended=f8k_appended,
        form8k_reversioned=f8k_reversioned,
        sched13_appended=s13_appended,
        sched13_reversioned=s13_reversioned,
        sched13_identity_skipped=s13_skipped,
        tape_edge=tape_edge,
        fund_shares_edge=fund_shares_edge,
        fund_shares_tracked=fund_shares_tracked,
        delisting_filings=stop.delisting,
        edgar_tickers=stop.listed,
        price_symbol=sec.price_symbol,
        price_symbol_basis=sec.price_symbol_basis,
        sec_map_tickers=stop.mapped,
        leg_errors=tuple(leg_errs),
        price_not_found=px_not_found,
        edgar_name=stop.current_name,
        former_names=stop.formers,
        name_change_filings=stop.renames,
        ingest_skips=tuple(skips),
        # the EDGAR legs above ran against this CIK (each is gated on it) — the freeze floor's denominator
        edgar_cik=sec.cik or None,
        acceptance_header=acceptance.header,
        acceptance_bounded=acceptance.bound,
        filing_index_appended=fi.appended,
        filing_index_skipped=len(fi.skipped),
        filing_index_skips=fi.skipped,
        filing_index_accepted_set_aside=fi.accepted_set_aside,
        filing_index_error=fi_error,
    )


def _report(results: list[NameResult]) -> int:
    """Print a per-name summary; return the number of names that errored (the process exit signal).
    Skips + backfilled bars surface only when nonzero — loudness marks the exception."""
    total_f4 = sum(r.form4_appended for r in results)
    # price bars that LANDED = the incremental tail + the re-versioned/hole-backfilled bars (the resolve-heal
    # writes the whole recovered history via the reversioned path); count both, like the fund-shares tally
    total_px = sum(r.price_bars_appended + r.price_bars_reversioned for r in results)
    total_sk = sum(r.form4_skipped for r in results)
    total_rj = sum(r.form4_txn_rejected for r in results)  # P1 — loud only when nonzero
    total_fs = sum(r.fund_shares_appended + r.fund_shares_reversioned for r in results)
    total_8k = sum(r.form8k_appended + r.form8k_reversioned for r in results)
    total_s13 = sum(r.sched13_appended + r.sched13_reversioned for r in results)
    total_s13_sk = sum(r.sched13_identity_skipped for r in results)
    errored = [r for r in results if r.error]
    for r in results:
        tail = f"   ERROR: {r.error}" if r.error else ""
        skips = (
            f", {r.form4_skipped} form4 skipped (pre-XML era / unfetchable)"
            if r.form4_skipped
            else ""
        )
        # P1 — the per-TRANSACTION rejections, surfaced DISTINCTLY from the per-FILING skips above (they
        # are different failures) and only when nonzero. Each one already printed in full during the leg.
        rejects = (
            f", {r.form4_txn_rejected} insider txn REJECTED (impossible date)"
            if r.form4_txn_rejected
            else ""
        )
        # the exceptional path (a split re-base or a resolve-heal hole-backfill) — surfaced DISTINCTLY,
        # loud only when nonzero, so the big backfill is visible and not silently folded into "+bars"
        backfill = f", +{r.price_bars_reversioned} backfilled" if r.price_bars_reversioned else ""
        # only an ETF sleeve ever samples — the line rides only when something landed (exception-loud)
        fund = (
            f", +{r.fund_shares_appended + r.fund_shares_reversioned} fund shares"
            if r.fund_shares_appended or r.fund_shares_reversioned
            else ""
        )
        # the 8-K tape — rides only when something landed; a resolved-items re-version is surfaced
        # DISTINCTLY (the exceptional path), never folded into the new-filing count
        f8k = f", +{r.form8k_appended} 8-K" if r.form8k_appended else ""
        f8k += f", +{r.form8k_reversioned} 8-K re-versioned" if r.form8k_reversioned else ""
        # the 13D/G tape (S5) — same exception-loud shape; an unresolved-identity row is surfaced
        # DISTINCTLY (stored, not dropped — #9), never folded into the appended count
        s13 = f", +{r.sched13_appended} 13D/G" if r.sched13_appended else ""
        s13 += f", +{r.sched13_reversioned} 13D/G re-versioned" if r.sched13_reversioned else ""
        s13 += (
            f", {r.sched13_identity_skipped} 13D/G identity unresolved"
            if r.sched13_identity_skipped
            else ""
        )
        # W1 — a company another writer held past the bounded wait: its legs did not run HERE (loud — this
        # run is incomplete for it), and a wait that ended in time is noted quietly
        if r.lock_busy:
            lk = f"   NOT INGESTED: {r.lock_busy}"
        elif r.lock_waited_s:
            lk = f" (waited {r.lock_waited_s:.0f}s for another writer)"
        else:
            lk = ""
        # X1 — a name whose history is still loading in the onboarding job: quiet, but said
        ob = (
            f" (onboarding {r.onboarding}: filings since {r.filed_since} only)"
            if r.onboarding and r.filed_since
            else ""
        )
        # the filing index (0070) — rides only when something landed or was skipped; its failure is its OWN
        # fragment, never folded into the name's ERROR (a passenger: it never fails the name)
        fi = f", +{r.filing_index_appended} filings indexed" if r.filing_index_appended else ""
        fi += (
            f", {r.filing_index_skipped} filing index row(s) skipped"
            if r.filing_index_skipped
            else ""
        )
        fi += f"   filing index ERROR: {r.filing_index_error}" if r.filing_index_error else ""
        print(
            f"  {r.ticker or r.security_id}: +{r.form4_appended} form4, "
            f"+{r.price_bars_appended} bars{backfill}{f8k}{s13}{fund}{skips}{rejects}{fi}{tail}{lk}{ob}"
        )
    busy = {r.security_id for r in results if r.lock_busy}
    sk = f", {total_sk} form4 skipped" if total_sk else ""
    rj = f", {total_rj} insider txn REJECTED (impossible date)" if total_rj else ""
    fs = f", +{total_fs} fund shares" if total_fs else ""
    e8k = f", +{total_8k} 8-K events" if total_8k else ""
    s13 = f", +{total_s13} 13D/G" if total_s13 else ""
    s13 += f" ({total_s13_sk} identity unresolved)" if total_s13_sk else ""
    bz = f", {len(busy)} NOT ingested (company held by another writer)" if busy else ""
    total_fi = sum(r.filing_index_appended for r in results)
    fi_err = sum(1 for r in results if r.filing_index_error)
    efi = f", +{total_fi} filings indexed" if total_fi else ""
    efi += f", {fi_err} filing index error(s)" if fi_err else ""
    print(
        f"done: {len(results)} names, +{total_f4} insider txns, +{total_px} price bars{e8k}{s13}"
        f"{fs}{efi}{sk}{rj}, {len(errored)} errored{bz}"
    )
    print_withheld_line(results)
    print_held_lines(results)
    print_split_lines(results)
    # the exit signal: an errored name, or one this run could not ingest because another writer held it
    return len(errored) + len(busy)


def print_withheld_line(results: Iterable[NameResult]) -> None:
    """D2 — ONE quiet line counting the names whose in-progress bar was withheld (their session had not
    settled), deduped by security; nothing at all when there are none. Lowercase on purpose — it is an
    expected daytime state that the next settled pass resolves by itself, not news (#7)."""
    withheld = {r.security_id for r in results if r.price_bars_withheld}
    if withheld:
        print(
            f"{len(withheld)} name(s): today's in-progress bar withheld — the session settles at "
            f"{get_settings().price_settle_time:%H:%M} market time; the next pass after that stores it"
        )


def print_acceptance_line(results: Iterable[NameResult]) -> None:
    """THE ACCEPTANCE CLOCK — ONE quiet line when any new Form 4 / 4/A's listed SEC acceptance time was set
    aside this pass (``ingest.edgar.acceptance``: not on the filing date within EDGAR's hours),
    with what replaced them; nothing at all when none was. Lowercase, never a page (#7): every such filing was
    still stored, on the right night — the line exists so the drift in SEC's list is never silent (#9).
    """
    header = bounded = 0
    for r in results:
        header += getattr(r, "acceptance_header", 0) or 0
        bounded += getattr(r, "acceptance_bounded", 0) or 0
    total = header + bounded
    if total:
        print(
            f"acceptance · {total} listed time(s) disagreed with the filing date · {header} read from the "
            f"header, {bounded} bounded to the filing date"
        )


def print_held_lines(results: Iterable[NameResult]) -> None:
    """Q11 / T5 — ONE quiet line per name whose older restated bars were HELD (the vendor reverted them to a
    value it served before, and only one pull has said so), deduped by security across theses; nothing at
    all when there are none. No uppercase header on purpose (the closed-tape / known-error casing): the next
    agreeing pull stores the bars by itself — the line exists so a hold is never silent (#9), not to alarm.
    """
    held: dict[UUID, tuple[str, int]] = {}
    for r in results:
        if r.price_bars_held:
            label = r.ticker or str(r.security_id)
            held[r.security_id] = (
                label,
                max(r.price_bars_held, held.get(r.security_id, ("", 0))[1]),
            )
    for label, n in held.values():
        print(
            f"{label}: {n} older price bar(s) held — the vendor reverted them to values it served before; "
            "stored once a pull on a later night agrees"
        )


def split_reads_of(results: Iterable[NameResult]) -> dict[str, dict]:
    """S2 — the split list's reads this pass PER NAME, deduped by security across theses (the biggest count per
    name and reason wins: two theses holding a name pull it twice): ``{security_id (str): {"ticker",
    "reasons": {reason: n}, "first_night": [labels]}}``. Names that read nothing are absent. The run
    artifact's run-level ``split_reads`` (the next pass's baseline, ``cron_run_log.previous_split_reads``) and
    the pass summary both read it."""
    out: dict[str, dict] = {}
    for r in results:
        counts = getattr(r, "price_bars_split_normalized", None) or {}
        first = tuple(getattr(r, "price_split_first_night", ()) or ())
        if not (counts or first):
            continue
        key = str(r.security_id)
        cur = out.setdefault(key, {"ticker": r.ticker or key, "reasons": {}, "first_night": []})
        for k, n in counts.items():
            cur["reasons"][k] = max(cur["reasons"].get(k, 0), int(n))
        cur["first_night"] = sorted(set(cur["first_night"]) | set(first))
    for v in out.values():
        v["reasons"] = dict(sorted(v["reasons"].items()))
    return out


# A standing divide-back drifts by a few bars a night as the vendor's two-year window slides; a change smaller
# than this (bars, or share of the last count, whichever is larger) is the same condition, not news.
SPLIT_READ_DRIFT_BARS = 5
SPLIT_READ_DRIFT_SHARE = 0.05


def _fmt_reasons(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items()))


def _standing(cur: dict[str, int], prev: dict[str, int]) -> bool:
    """The same divide-back as the last pass: the same reasons, each count within the window drift."""
    if set(cur) != set(prev):
        return False
    return all(
        abs(n - prev[k]) <= max(SPLIT_READ_DRIFT_BARS, SPLIT_READ_DRIFT_SHARE * prev[k])
        for k, n in cur.items()
    )


def print_split_lines(
    results: Iterable[NameResult], *, previous: dict[str, dict[str, int]] | None = None
) -> None:
    """S2 — the split list's reads this pass (``split_reads_of``). ONE quiet line with the per-reason totals,
    whenever anything was read; nothing at all otherwise. Per name, a line only for NEWS:

    - a divide-back (``DIVIDE_BACK_REASONS``: the vendor applied a split twice, or a different factor) that
      CHANGED from the last pass — new, gone, or beyond the window drift — or every one when there is no
      baseline (``previous`` None: the first pass, or the CLI). A divide-back that stands night after night
      (ATRO's mis-factor, PBM's double adjustment, once repaired) is a standing condition: it stays in the
      totals and is not re-announced (#7, the re-review's N2). Each announced one is also logged at WARNING
      (it reaches stderr with no logging config — the level ``pipeline.backup`` uses for the same reason);
    - a first night (a split applied to the stored history for the first time) — always: it happens once.

    ``vendor_raw`` stays in the totals only: the steady absorb of a vendor still serving the unadjusted basis.
    ``previous`` is ``{security_id: {reason: n}}`` from the last live, finished pass."""
    reads = split_reads_of(results)
    if not reads:
        return
    totals: dict[str, int] = {}
    for v in reads.values():
        for k, n in v["reasons"].items():
            totals[k] = totals.get(k, 0) + n
    kept = totals.pop(REASON_HOLE_AS_SERVED, 0)
    parts = " · ".join(f"{k.replace('_', ' ')} {n}" for k, n in sorted(totals.items()))
    held = f"; {kept} hole(s) left as served beside a rescaled neighbor" if kept else ""
    print(
        f"split list: {sum(totals.values())} price bar(s) of {len(reads)} name(s) read onto the list's "
        f"scale ({parts}){held}"
    )
    for key, v in reads.items():
        label = v["ticker"]
        back = {k: n for k, n in v["reasons"].items() if k in DIVIDE_BACK_REASONS}
        prev = {
            k: n for k, n in ((previous or {}).get(key) or {}).items() if k in DIVIDE_BACK_REASONS
        }
        if back and (previous is None or not _standing(back, prev)):
            what = _fmt_reasons(back)
            was = f" (last pass: {_fmt_reasons(prev)})" if prev else ""
            print(f"{label}: split list divided the vendor's bars back — {what}{was}")
            _log.warning("price split normalization: %s — %s%s", label, what, was)
        elif prev and not back:
            print(f"{label}: split list no longer divides the vendor's bars back")
        if v["first_night"]:
            line = f"split applied to the stored history for the first time — {'; '.join(v['first_night'])}"
            print(f"{label}: {line}")
            _log.warning("price split normalization: %s — %s", label, line)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Ingest back-half facts (Form 4 + EOD + ETF fund shares) for a thesis's "
        "resolved basket."
    )
    p.add_argument("--thesis", required=True, help="thesis id (uuid)")
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache-only (no network); else live (needs ALPHADECK_USER_AGENT)",
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="re-pull live + overwrite the cache (bypass a stale cache hit) — for a manual re-ingest",
    )
    args = p.parse_args(argv)

    conn = connect()
    name_session(conn, "alphadeck-writer:cli-ingest")  # W1 — how a waiting writer names this one
    try:
        results = ingest_thesis(
            conn,
            UUID(args.thesis),
            allow_live=not args.no_live,
            force_refresh=args.force_refresh,
        )
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a cron / wrapper, non-silently


if __name__ == "__main__":
    main()

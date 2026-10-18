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
  The live fetchers are polite (``ingest.http.polite_get`` retries 429 / transient 5xx with backoff; EDGAR
  also throttles ≤8 req/s).
- **No-lookahead** — ``recorded_at`` is left to the DB default ``now()`` (NEVER backdated), so a fact ingested
  today is invisible to an as-of read pinned at an earlier transaction time (the replay guarantee holds).
- **Exact membership (#2)** — it targets the member's RESOLVED ``security_id`` via ``master.get`` (the issuer
  ticker + CIK), never a fresh fuzzy resolve. Tenant comes from the thesis (one thesis = one tenant).

    python -m pipeline.ingest_thesis --thesis <uuid>
    python -m pipeline.ingest_thesis --thesis <uuid> --no-live   # cache-only (no network)
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

from db.session import connect
from domain.security import Security
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient
from ingest.edgar.form4 import existing_accessions, ingest_form4, other_issuer_cik
from ingest.edgar.form4a import ingest_amendments
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
    form4_doc_url,
    form4_filings,
    form8k_filings,
    former_names,
    name_change_filings,
    parse_acceptance,
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
from ingest.skips import (
    FORM4_OTHER_ISSUER,
    FORM4_UNPARSEABLE,
    SCHEDULE13_IDENTITY,
    SCHEDULE13_OUTBOUND,
    IngestSkip,
    clip_reason,
    is_known,
)
from pipeline.pass_timings import timed
from repositories import thesis_repo
from securities import master


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
) -> tuple[int, int, int, tuple[IngestSkip, ...]]:
    """Ingest only Form 4 accessions not already stored for this security (incremental). Returns
    ``(appended, skipped, txn_rejected, skips)``. Needs the issuer CIK; a name without one contributes no
    insider facts.

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
    for f in form4_filings(subs):
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
            # thread the SEC acceptance datetime (the honest "disclosed" clock) from the enumeration —
            # the ownership XML has none; parse_acceptance -> None leaves accepted NULL (#9)
            one = ingest_form4(
                conn,
                sec.id,
                xml,
                f["accession"],
                tenant_id=tenant_id,
                accepted=parse_acceptance(f.get("accepted")),
                member_cik=sec.cik,
            )
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
    )
    skips.extend(amend_skips)
    skipped += len(amend.skipped)
    if amend.stored:
        pending = f", {amend.pending} pending the operator (Admin)" if amend.pending else ""
        print(f"  {sec.ticker or sec.id}: +{len(amend.stored)} form 4/A stored + linked{pending}")
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
) -> tuple[int, int, int, tuple[IngestSkip, ...]]:
    """Ingest the security's 13D/G activist-stake tape (Band 03 S5) from its OWN submissions JSON —
    the S5-verified subject-indexing path. Returns ``(appended, reversioned, identity_skipped, skips)``.
    Needs the issuer CIK; a name without one contributes no stakes.

    Enumeration reads the SAME submissions document the Form 4 / 8-K legs fetch (cache-free second
    read); the activist's identity costs one bounded, immutable-cached document fetch per filing
    (``ingest_schedule13``'s depth strategy). Append-if-changed: an unchanged tape re-run appends
    ZERO rows; a stored filing whose identity RESOLVES gets a new version (never an UPDATE); an
    identity failure stores the row with NULL filer and counts loud (#9).

    Q18 — ``skips`` names the outbound (owner-filed) and identity-failed filings; ``known_skips`` mutes the
    lines of the ones that skipped the same way last pass (``ingest_schedule13``)."""
    if not sec.cik:
        return 0, 0, 0, ()
    filings = schedule13_filings(fetch_submissions(client, sec.cik))
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
) -> list[NameResult]:
    """Ingest insider + price facts — and, for an ETF sleeve, a fund shares-outstanding sample — for
    each RESOLVED basket member of ``thesis_id``.

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
    """
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis {thesis_id} not found")
    client = edgar_client or EdgarClient(allow_live=allow_live, user_agent=user_agent)
    source = price_source or YahooPriceSource()
    results: list[NameResult] = []
    for m in thesis.basket:
        if m.security_id is None:
            continue  # unresolved placement — no exact member to ingest against
        if only_security_ids is not None and m.security_id not in only_security_ids:
            continue  # scoped run (the on-promote kick): only the requested members are walked
        sec = master.get(conn, m.security_id, tenant_id=thesis.tenant_id)
        if sec is None:
            # a placed id not in this tenant's master (shouldn't happen post-promote-guard) — report it
            results.append(
                NameResult(
                    m.ticker,
                    m.security_id,
                    0,
                    0,
                    "not in tenant master",
                    leg_errors=(("master", "not in tenant master"),),
                )
            )
            continue
        # each failed leg is captured TWICE on purpose: the joined `errs` string is the existing
        # `NameResult.error` (unchanged); `leg_errs` keeps the same failures by leg for the run artifact (O1)
        errs: list[str] = []
        leg_errs: list[tuple[str, str]] = []
        f4 = 0
        f4_skipped = 0
        f4_txn_rejected = 0
        skips: list[IngestSkip] = []
        try:
            f4, f4_skipped, f4_txn_rejected, f4_skips = _form4_leg(
                conn, client, sec, tenant_id=thesis.tenant_id, known_skips=known_skips
            )
            skips.extend(f4_skips)
            conn.commit()
        except (
            Exception
        ) as e:  # noqa: BLE001 — fail-visible: capture, roll back this leg, keep going
            conn.rollback()
            errs.append(f"form4: {e}")
            leg_errs.append(("form4", str(e)))
        f8k_appended, f8k_reversioned = 0, 0
        try:
            f8k_appended, f8k_reversioned = _form8k_leg(
                conn, client, sec, tenant_id=thesis.tenant_id
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — same per-leg isolation as the form4 leg
            conn.rollback()
            errs.append(f"form8k: {e}")
            leg_errs.append(("form8k", str(e)))
        s13_appended, s13_reversioned, s13_skipped = 0, 0, 0
        try:
            s13_appended, s13_reversioned, s13_skipped, s13_skips = _schedule13_leg(
                conn, client, sec, tenant_id=thesis.tenant_id, known_skips=known_skips
            )
            skips.extend(s13_skips)
            conn.commit()
        except Exception as e:  # noqa: BLE001 — same per-leg isolation as the form4 leg
            conn.rollback()
            errs.append(f"schedule13: {e}")
            leg_errs.append(("schedule13", str(e)))
        px_appended, px_reversioned, px_withheld, px_held = 0, 0, 0, 0
        px_not_found = False
        try:
            bars = timed("price")(ingest_bars_for_security)(  # X5 — the price leg's wall time
                conn,
                sec,
                tenant_id=thesis.tenant_id,
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
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            errs.append(f"price: {e}")
            leg_errs.append(("price", str(e)))
            px_not_found = _is_not_found(
                e
            )  # T3 — a FACT (the vendor 404'd), judged in pipeline.daily
        # THE TAPE EDGE (G5a) — read AFTER the price leg so an appended tail counts, and OUTSIDE its
        # try/except so a name whose price leg FAILED still reports where its stored tape actually ends (a
        # failing name and a dead tape are different facts; the monitor must see both). Its OWN try: this is
        # a monitor reading a fact, so a fault here degrades to None — it must never blank a name's whole
        # result. Cheap: one indexed MAX(d) per name. The JUDGMENT (is this edge too old?) is NOT made here
        # — `pipeline.daily` makes it against the run's asof (`pipeline/tape_health.py`), which is why this
        # function needs no `asof` parameter and no ambient clock.
        tape_edge: date | None = None
        try:
            tape_edge = latest_bar_date(conn, sec.id, tenant_id=thesis.tenant_id)
        except Exception as e:  # noqa: BLE001 — a monitor read never costs a name its result
            print(f"  warn: {sec.ticker or sec.id} tape-edge read failed: {e}")
        # THE STOP-CLASSIFICATION FACTS (G5b/T1) — every SEC delisting form (25/25-NSE/15-12B/15-12G) and
        # EDGAR's current ticker list, from the name's submissions, so a STOPPED tape can be told apart:
        # closed, renamed, or a feed gap. FREE on a daily pass: the Form 4 leg above already fetched +
        # cached this CIK's submissions, so this is a cache-hit disk read + two list walks, exactly like the
        # 8-K / 13D legs (a cache hit never touches the network or the freeze counter). Gated on `sec.cik` —
        # a name with no CIK has no submissions (the Form 4 leg's own guard). Its OWN try, degrade to the
        # empty defaults: this is a monitor read feeding a classification, so a fault must never cost a name
        # its result, and "no evidence" is the SAFE default — a name without positive delisting evidence
        # stays the loud "stale — repair", never the quiet "closed" (erring toward visible, #7/#9). NOT
        # judged here — `pipeline.daily` classifies a stale tape from these, so this unit needs no `asof`.
        # T2 — the RENAME EVIDENCE rides the same read (the company's current name, its former names, and
        # its item-5.03 8-Ks): three more walks of the cached doc, display evidence only.
        delisting: tuple[DelistingFiling, ...] = ()
        listed: tuple[str, ...] | None = None
        current_name: str | None = None
        formers: tuple[FormerName, ...] = ()
        renames: tuple[NameChangeFiling, ...] = ()
        if sec.cik:
            try:
                subs = fetch_submissions(client, sec.cik)
                delisting = delisting_filings(subs)
                listed = edgar_tickers(subs)
                current_name = entity_name(subs)
                formers = former_names(subs)
                renames = name_change_filings(subs)
            except Exception as e:  # noqa: BLE001 — a monitor read never costs a name its result
                print(f"  warn: {sec.ticker or sec.id} delisting read failed: {e}")
        fs_appended, fs_reversioned = 0, 0
        try:
            shares = timed("fund_shares")(ingest_fund_shares_for_security)(  # X5
                conn,
                sec,
                tenant_id=thesis.tenant_id,
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
        # stale forever. GATED on `fund_shares_applies`, which is load-bearing twice over: it keeps ~500
        # equity members from paying a pointless query every night, and it stops them being JUDGED at all
        # (an equity's edge is None, and None means STALE to the rule — ungated, the monitor would report
        # the entire basket). OUTSIDE the leg's try, so an ETF whose sampler RAISED still reports where its
        # stored series actually ends — a failing leg and a dead series are different facts and the monitor
        # must see both. Its OWN try: a monitor read never costs a name its result.
        fund_shares_edge: date | None = None
        fund_shares_tracked = fund_shares_applies(sec)
        if fund_shares_tracked:
            try:
                fund_shares_edge = latest_shares_date(conn, sec.id, tenant_id=thesis.tenant_id)
            except (
                Exception
            ) as e:  # noqa: BLE001 — same degrade-to-None discipline as the tape edge
                print(f"  warn: {sec.ticker or sec.id} fund-shares-edge read failed: {e}")
        results.append(
            NameResult(
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
                delisting_filings=delisting,
                edgar_tickers=listed,
                price_symbol=sec.price_symbol,
                leg_errors=tuple(leg_errs),
                price_not_found=px_not_found,
                edgar_name=current_name,
                former_names=formers,
                name_change_filings=renames,
                ingest_skips=tuple(skips),
            )
        )
    return results


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
        print(
            f"  {r.ticker or r.security_id}: +{r.form4_appended} form4, "
            f"+{r.price_bars_appended} bars{backfill}{f8k}{s13}{fund}{skips}{rejects}{tail}"
        )
    sk = f", {total_sk} form4 skipped" if total_sk else ""
    rj = f", {total_rj} insider txn REJECTED (impossible date)" if total_rj else ""
    fs = f", +{total_fs} fund shares" if total_fs else ""
    e8k = f", +{total_8k} 8-K events" if total_8k else ""
    s13 = f", +{total_s13} 13D/G" if total_s13 else ""
    s13 += f" ({total_s13_sk} identity unresolved)" if total_s13_sk else ""
    print(
        f"done: {len(results)} names, +{total_f4} insider txns, +{total_px} price bars{e8k}{s13}"
        f"{fs}{sk}{rj}, {len(errored)} errored"
    )
    print_withheld_line(results)
    print_held_lines(results)
    return len(errored)


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

"""Annual-cover shares for the DARK names (Retrieval Slice 1) — 20-F/40-F, FLAG-only by construction.

A basket name with NO 10-K/10-Q (a foreign private issuer) gets an honest, current shares-outstanding
count — and therefore a market cap — from the cover of its latest annual foreign filing (20-F or 40-F),
at tier FLAG, carrying its located cover passage and its age. 48 of 250 resolved names were dark
(``docs/RETRIEVAL_ANSWER_KEY.md`` §0); this lights up shares for 43 of them.

THE STRUCTURAL BOUND — why this is its own module, not a branch inside ``extract_facts``: the periodic
extractor's confirm-and-go gate (``extract.py::_shares``) passes when the cover as-of >= the period end,
which is TRUE BY SEC FORM INSTRUCTION on every annual cover ("…as of the close of the period covered by
the annual report") while the value is 204-569 days old (answer key Finding A). An annual filer routed
through it would pre-fill a year-old count at the lowest-friction tier. This module is structurally
unable to do that: the pre-fill tier's token appears NOWHERE in this file (a source-scan test asserts
it — the display-signals idiom), and every emitted fact is ``Tier.FLAG`` — the operator ratifies, always.

FAIL CLOSED: no cover-instruction match -> NO value. Never a looser pattern, and never companyfacts
alone — a fact without its located passage would break the no-passage-no-fact contract the operator
chose (Option B). PBM is the proof case: its cue-lookalike is an EPS note ~400k chars deep ("number of
outstanding shares - basic and diluted") and its real cover uses nonstandard phrasing outside the SEC
instruction — both yield nothing. Deterministic parse only (#3): no LLM anywhere on this path.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, LocatedPassage, Tier
from ingest.edgar.client import EdgarClient
from ingest.edgar.converts import clean_filing_text
from ingest.edgar.extract import _doc_url, companyfacts_url
from ingest.edgar.submissions import fetch_submissions, filings_of

# The SEC form instruction ITSELF — the discriminator between a real annual cover and a footnote
# lookalike. VALIDATED against all 48 dark names (spec §4 / answer key §6); every arm is load-bearing:
# - `issuer` OR `registrant`: a 40-F cover reads "of the Registrant's classes" (OGI, CRLBF, DRUG)
#   where a 20-F reads "of the issuer's classes" — a draft matching only `issuer` silently dropped
#   three real names, invisibly (#9).
# - `each of` is OPTIONAL: OGI and DRUG omit it; CRLBF and the 20-Fs include it.
# - `\W{0,3}s` for the possessive: the apostrophe survives tag-stripping as `'`, `’`, or a space, and
#   EHVVF renders `issuer&rsquo;s` — only ``clean_filing_text``'s html.unescape normalises it.
# Run it against ``clean_filing_text`` output ONLY, and do not narrow it: a "simplified" version
# silently dropped four real names during validation.
_COVER_INSTRUCTION_RE = re.compile(
    r"outstanding shares of (?:each of )?the (?:issuer|registrant)\W{0,3}s classes", re.IGNORECASE
)

# A share count with grouped thousands — BOTH separators: `385,417,665` and NVS's European-convention
# `1 908 151 679` (answer key Finding C). The leading `\b` keeps a match from starting mid-number,
# which also keeps 4-digit years inert ("December 31, 2025" cannot match: `\d{1,3}` never spans the
# year and no digit triplet follows its separator).
_COUNT_RE = re.compile(r"\b\d{1,3}(?:[,\s ]\d{3})+\b")

# How much REAL context the located passage shows around the instruction + the counts (display window,
# not a match bound): a little before the instruction, then through the last matched count plus a tail
# so subset/class wording ("… including 17,371,450 ADSs …") is visible where the operator ratifies.
_EXCERPT_PRE_CHARS = 60
_EXCERPT_TAIL_CHARS = 140


def _latest_dei_cover(companyfacts: dict[str, Any] | None) -> tuple[float, date] | None:
    """The latest ``dei:EntityCommonStockSharesOutstanding`` (value, as-of end date). ``dei`` is
    taxonomy-independent, so it is present for US-GAAP and IFRS filers alike; ``None`` when
    companyfacts is absent or carries no cover concept (GLAS/CRLBF/TRSG have none at all — the
    cover-only path must work)."""
    rows = (
        (companyfacts or {})
        .get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    )
    if not rows:
        return None
    end = max(r["end"] for r in rows)
    at = [r for r in rows if r["end"] == end]
    return float(max(at, key=lambda r: r.get("filed", ""))["val"]), date.fromisoformat(end)


def extract_annual_shares(
    companyfacts: dict[str, Any] | None,
    annual_text: str,
    *,
    annual_ref: str,
    annual_form: str,
    report_date: date,
    today: date,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> list[ExtractedFact]:
    """Pure + deterministic: (companyfacts-or-None + the CLEANED annual filing text) -> the one
    shares_outstanding FLAG candidate, or ``[]`` when the cover cannot be read (FAIL CLOSED — the
    caller stamps the honest empty reason). ``report_date`` is the filing's PERIOD OF REPORT — what
    the SEC instruction dates the cover count to; ``today`` ages the chosen count for the staleness
    flag + the note (time is a parameter, never an implicit now).
    """
    m = _COVER_INSTRUCTION_RE.search(annual_text)
    if m is None:
        return (
            []
        )  # fail closed — no instruction, no value (PBM: nonstandard cover + a deep EPS lookalike)
    segment = annual_text[m.end() : m.end() + cfg.annual_cover_segment_chars]
    nums = list(_COUNT_RE.finditer(segment))
    if not nums:
        return (
            []
        )  # cue but no parseable count (measured empty across all 48) — same fail-closed reason
    # FIRST number, NEVER a sum: CAJPY's second number is an ADS *subset* of the first ("1,015,513,368
    # shares of common stock, including 17,371,450 ADSs"), so summing overstates; CRLBF's four are real
    # classes but composition is the operator's ratify, guided by the passage. (Deliberately NOT the
    # 10-Q ``_cover_class_sum`` behaviour — a different form with a different cover convention.)
    cover_value = float(re.sub(r"[,\s]", "", nums[0].group(0)))
    cover_asof = report_date

    cf = _latest_dei_cover(companyfacts)
    # THE RULE IS LATER-AS-OF-WINS, not prefer-the-document (spec §3.4): companyfacts sometimes LAGS
    # the filing (NVMI: cf 29,278,401 @2024-12-31 vs cover 31,780,111 @2025-12-31) and is sometimes
    # FRESHER (CMND: cf 158,076 @2026-01-19 beats the 2025-10-31 cover). A tie prefers the cover —
    # it is the value the located passage shows.
    if cf is not None and cf[1] > cover_asof:
        value, asof, winner = cf[0], cf[1], "companyfacts"
    else:
        value, asof, winner = cover_value, cover_asof, "cover"

    flags = ["annual-cover"]
    age_days = (today - asof).days
    if age_days > cfg.annual_stale_cover_days:
        flags.append("stale-cover")
    if cf is not None and cf[0] != cover_value:
        flags.append("source-disagreement")
    if len(nums) > 1:
        flags.append("multi-value-cover")
    if value < cfg.annual_implausible_floor_shares:
        # emitted anyway — recall #9: a suppressed value is worse than a flagged one. The floor is the
        # backstop for garbage being the LATER value (companyfacts `dei` can carry nonsense: QNTM = 12).
        flags.append("implausible-count")

    # the note: the chosen count, its as-of, its AGE IN DAYS, the form — and BOTH values on disagreement
    cover_part = f"{annual_form} cover count {cover_value:,.0f} as of {cover_asof.isoformat()}"
    if winner == "cover":
        note = f"{cover_part} ({age_days} days old)"
        if cf is None:
            note += ". No companyfacts to compare (cover-only)."
        elif cf[0] == cover_value:
            note += f"; companyfacts agrees ({cf[0]:,.0f} as of {cf[1].isoformat()})."
        else:
            note += (
                f" — CHOSEN over companyfacts {cf[0]:,.0f} as of {cf[1].isoformat()} "
                "(later as-of wins; companyfacts lags the filing)."
            )
    else:
        note = (
            f"companyfacts count {value:,.0f} as of {asof.isoformat()} ({age_days} days old) — "
            f"CHOSEN over the {cover_part} (later as-of wins). Ratify against the located cover."
        )
    if "implausible-count" in flags:
        note += (
            f" IMPLAUSIBLY SMALL (< {cfg.annual_implausible_floor_shares:,.0f} shares) — verify "
            "against the cover passage before ratifying; a data-source can carry garbage."
        )
    if "multi-value-cover" in flags:
        note += (
            " The cover carries more than one count (classes / an ADS subset / a second date) — "
            "the FIRST is offered, never a sum; ratify the composition against the passage."
        )

    # the located passage — REQUIRED evidence (no passage -> no fact, enforced by the fail-closed
    # returns above): a bit before the instruction, through the LAST matched count + a tail, so the
    # subset/class wording is readable where the operator ratifies. Offset recorded, never filtered on.
    start = max(0, m.start() - _EXCERPT_PRE_CHARS)
    end = m.end() + nums[-1].end() + _EXCERPT_TAIL_CHARS
    excerpt = re.sub(r"\s+", " ", annual_text[start:end]).strip()
    passage = LocatedPassage(
        kind="cover",
        source_ref=annual_ref,
        anchor=m.group(0),
        excerpt=f"… {excerpt} …",
        offset=m.start(),
    )

    return [
        ExtractedFact(
            fact_type="shares_outstanding",
            tier=Tier.FLAG,  # ALWAYS — an annual count is the operator's to ratify, never confirm-and-go
            value=value,
            source="annual-cover",  # NOT "10-q-cover" — the provenance must not lie
            source_ref=annual_ref,
            event_date=asof,
            flags=flags,
            located_passages=[passage],
            note=note,
        )
    ]


def _latest_annual_filing(client: EdgarClient, cik: int) -> tuple[str, str, date, str] | None:
    """(doc_url, CLEANED text, report_date, form) of the latest annual foreign filing across 20-F AND
    40-F — selected from submissions METADATA first, then ONE document fetched (these run 0.2-25 MB;
    fetching both to compare would double the spend the cost thread bounds).

    Never prefer a form: ``filings_of('20-F') or filings_of('40-F')`` short-circuits — CRDL files
    both, and its non-empty 20-F list (2023) hid a newer 2025 40-F (the answer-key probe's own bug).
    Compare report dates; a tie keeps the 20-F (iteration order — date-identical either way).
    """
    subs = fetch_submissions(client, cik)
    best: tuple[date, str, dict[str, str]] | None = None
    for form in ("20-F", "40-F"):
        hits = filings_of(subs, form)
        if not hits:
            continue
        f = hits[0]
        d = date.fromisoformat(f.get("report_date") or f["filed"])
        if best is None or d > best[0]:
            best = (d, form, f)
    if best is None:
        return None
    d, form, f = best
    url = _doc_url(cik, f["accession"], f["primary_doc"])
    text = clean_filing_text(
        client.get_text(url, f"forms/{f['accession']}/{f['primary_doc'].rsplit('/', 1)[-1]}")
    )
    return url, text, d, form


def _companyfacts_or_none(client: EdgarClient, cik: int) -> dict[str, Any] | None:
    """companyfacts, tolerating ONLY the genuinely-absent case: some real filers have none at all
    (GLAS, CRLBF, TRSG — the cover-only path must work), which data.sec.gov serves as HTTP 404.
    Anything else (network fault, 5xx after retries) stays fail-visible — a transient error must not
    silently degrade the later-as-of comparison to cover-only."""
    try:
        return client.get_json(companyfacts_url(cik), f"companyfacts/CIK{cik:010d}.json")
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 404:
            return None
        raise


def annual_shares_for_security(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    today: date | None = None,
) -> ExtractionResult:
    """Live (cache-first) wrapper for a DARK name: CIK -> the latest 20-F/40-F cover + companyfacts ->
    the one FLAG shares candidate, or an honest, DISTINCT empty reason (the three empty states):

    - ``no-annual-filing``  — no 20-F/40-F either: genuinely nothing on EDGAR to read (SKHY, AGNPF).
    - ``cover-not-located`` — an annual filing exists but its cover instruction wasn't matched (PBM),
      or matched with no parseable count: the name is UNREAD, not empty. companyfacts is deliberately
      NOT served alone here (no passage -> no fact).

    An EXPLICIT operator action via the extract endpoint, never fired on a render (the cost thread).
    """
    cik = int(cik)
    filing = _latest_annual_filing(client, cik)
    if filing is None:
        return ExtractionResult(facts=[], empty_reason="no-annual-filing")
    url, text, report_dt, form = filing
    facts = extract_annual_shares(
        _companyfacts_or_none(client, cik),
        text,
        annual_ref=url,
        annual_form=form,
        report_date=report_dt,
        today=today or date.today(),
        cfg=cfg,
    )
    if not facts:
        return ExtractionResult(facts=[], empty_reason="cover-not-located")
    return ExtractionResult(facts=facts)


__all__ = ["extract_annual_shares", "annual_shares_for_security"]

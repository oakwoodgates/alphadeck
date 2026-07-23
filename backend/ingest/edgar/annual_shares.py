"""Annual-cover shares for the DARK names (Retrieval Slice 1) — 20-F/40-F, FLAG-only by construction.

A basket name with NO 10-K/10-Q (a foreign private issuer) gets an honest, current shares-outstanding
count — and therefore a market cap — from the cover of its latest annual foreign filing (20-F or 40-F),
at tier FLAG, carrying its located cover passage and its age. Roughly a fifth of a real basket was dark
this way. The rules below are canon in ``docs/WORKBENCH_EXTRACTION.md`` ("The annual-cover path"); the
dated measurement that established them is PR #221, and the values are pinned in
``tests/ingest/test_annual_shares.py`` + its fixtures — the tests are the oracle, not a prose doc.

THE STRUCTURAL BOUND — why this is its own module, not a branch inside ``extract_facts``: the periodic
extractor's confirm-and-go gate (``extract.py::_shares``) passes when the cover as-of >= the period end,
which is TRUE BY SEC FORM INSTRUCTION on every annual cover ("…as of the close of the period covered by
the annual report") while the value is a fiscal year or more old (measured 204-569 days; PR #221). An annual filer routed
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

# ---------------------------------------------------------------------------------------------------------
# the ADS ratio (spec §10) — apply where READ, SUPPRESS where not
# ---------------------------------------------------------------------------------------------------------
# The cover states ORDINARY shares; the price feed carries the ADS price. Ratios measured from the
# filings' own words run 1:1 up to 120:1 — a raw shares×price overstates the cap N-fold, and the mid-size
# errors (2x, 5x) are exactly the ones the operator's market-cap intuition does NOT catch. Detection can
# never be proven complete, so the rule is fail-closed one layer down from the cover cue: a READ ratio is
# applied; ADR evidence WITHOUT a defensible ratio (missing / fractional / CONFLICTING statements)
# suppresses the cap; no evidence at all computes 1:1 with the assumption recorded. Better detection later
# moves a name from suppressed→correct, never from wrong→right.

# word-numbers: covers spelled ratios ("five", "ten", "twenty") and compounds ("four hundred",
# "one hundred and twenty" — both real; a naive single-token map read "four hundred" as 4).
_RATIO_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_RATIO_MULT = {"hundred": 100, "thousand": 1000}

# the number-phrase chunk each arm captures: lazily up to the word "share(s)"
_NP = r"(.{1,40}?)\s*(?:new\s+)?(?:ordinary|common|equity|Ordinary|Common)?\s*[Ss]hares?\b"
# arm A — an explicit ADS/ADR noun then "represents/representing" (doc-wide prose): "each ADS
# represents five (5) common shares", "each of which represents ten shares". ("deposit[ao]r?y" nets
# the real "depository" misspelling seen in the wild.)
_ADS_ARM_A = re.compile(
    r"each (?:of which |of our )?(?:ADSs?|ADRs?|American [Dd]eposit[ao]r?y (?:Shares?|Receipts?))?"
    r"\s*(?:will )?represent(?:s|ing)?\s+" + _NP,
    re.IGNORECASE,
)
# arm C — "one ADS represents/representing N shares" (price-table footnotes; the TARGET of a
# ratio-change sentence). The "changed FROM one ADS representing X" arm is HISTORY and is excluded at
# match time (a from-preceded match) — real filings state both the old and new ratio in one sentence.
_ADS_ARM_C = re.compile(
    r"one (?:ADS|ADR|American Depositary Share)\s+(?:will )?represent(?:s|ing)?\s+" + _NP,
    re.IGNORECASE,
)
# arm D — the noun-BEFORE-each form: "American Depositary Shares, each representing one share"
# (the noun and "each" straddle a comma/paren) — doc-wide, noun explicit.
_ADS_ARM_D = re.compile(
    r"(?:ADSs?|ADRs?|American [Dd]eposit[ao]r?y (?:Shares?|Receipts?))[^.]{0,40}?[,)]\s*"
    r"each representing\s+" + _NP,
    re.IGNORECASE,
)
# arm B — the securities-registered TABLE row: "each representing four ordinary shares" with NO
# adjacent ADS noun (table-cell boundaries separate them — the form a prose-only parser misses).
# REGION-SCOPED to the cover's registration block, never doc-wide (elsewhere "each representing"
# belongs to warrants/units).
_ADS_ARM_B = re.compile(r"each (?:of which )?represent(?:s|ing)\s+" + _NP, re.IGNORECASE)

_ADS_MENTION_RE = re.compile(r"American Depositar|\bADSs?\b|\bADRs?\b", re.IGNORECASE)
# The registration block starts at the FIRST "Securities registered" heading — the 12(b) table (where
# the ADS row lives) precedes the 12(g)/15(d) ones, so `find`, never `rfind` (the last heading sits
# PAST the ADS row and hid it). Retrieval windows (the `_PURITY_WINDOW` precedent), not match bounds:
_SR_ANCHOR = "securities registered"
_SR_MAX_SPAN = (
    8000  # a "Securities registered" further than this from the cue isn't the cover's block
)
_SR_FALLBACK_PRE = (
    4000  # no heading found -> the window just before the cue stands in for the block
)


def _parse_ratio_phrase(chunk: str) -> int | None:
    """``'five (5)'``->5 · ``'20'``->20 · ``'four hundred ( 400 )'``->400 · ``'one hundred and
    twenty'``->120 · fractional (``'one-half of one'``) or junk -> None (never a guess). Parenthesized
    digits must AGREE with the words — a disagreement is unparseable, not a choice."""
    if re.search(r"\bhalf\b|\bquarter\b|\bthird\b", chunk, re.IGNORECASE):
        return (
            None  # a fractional ratio is real (1 ADS = half a share) but never a divisor we apply
        )
    paren = re.search(r"\(\s*([\d,]+)\s*\)", chunk)
    paren_val = int(paren.group(1).replace(",", "")) if paren else None
    tokens = re.findall(r"[a-z]+|\d[\d,]*", chunk.lower().replace("-", " "))
    cur = 0
    seen = False
    for t in tokens:
        if t == "and":
            continue
        if t[0].isdigit():
            if (
                seen
            ):  # a bare digit AFTER words is the parenthesized restatement, cross-checked below
                continue
            cur += int(t.replace(",", ""))
            seen = True
        elif t in _RATIO_WORDS:
            cur += _RATIO_WORDS[t]
            seen = True
        elif t in _RATIO_MULT:
            cur = max(cur, 1) * _RATIO_MULT[t]
            seen = True
        else:
            break  # a non-number word ends the phrase ("the right to receive …" never starts one)
    if not seen or (paren_val is not None and paren_val != cur):
        return None
    return cur


def _detect_ads_ratio(
    text: str, cue_start: int, has_f6_filing: bool, cfg: ExtractorConfig
) -> tuple[str | None, int | None, list[tuple[str, int, int | None]]]:
    """(status, ratio, hits) over the CLEANED annual text. status: ``"known"`` (exactly one defensible
    ratio read) · ``"unread"`` (ADR evidence but no/fractional/CONFLICTING ratio — suppress the cap) ·
    ``None`` (no ADR evidence — 1:1). ``hits`` = (matched text, offset, parsed value|None) for the
    note + the evidence passage. Evidence = any ratio-shaped phrase, an ADS/ADR mention inside the
    cover's securities-registered block, or an F-6-family filing on record — F-6 is a POSITIVE signal
    only; its absence never implies "not an ADR" (a 5:1 name with no recent F-6 was measured)."""
    sr = text.lower().find(_SR_ANCHOR)
    if 0 <= sr < cue_start and cue_start - sr <= _SR_MAX_SPAN:
        region_start, region = sr, text[sr:cue_start]
    else:
        region_start = max(0, cue_start - _SR_FALLBACK_PRE)
        region = text[region_start:cue_start]

    values: set[int] = set()
    hits: list[tuple[str, int, int | None]] = []
    evidence = has_f6_filing or bool(_ADS_MENTION_RE.search(region))
    for arm, scope, base in (
        (_ADS_ARM_A, text, 0),
        (_ADS_ARM_C, text, 0),
        (_ADS_ARM_D, text, 0),
        (_ADS_ARM_B, region, region_start),
    ):
        for h in arm.finditer(scope):
            # "changed FROM one ADS representing X … to a new ratio of …" — the from-arm is history
            if scope[max(0, h.start() - 8) : h.start()].lower().endswith("from "):
                continue
            if arm is not _ADS_ARM_B and not re.search(
                r"ADS|ADR|American [Dd]epositar", h.group(0)
            ):
                continue  # the noun group is optional in arm A's grammar; outside the block, demand it
            v = _parse_ratio_phrase(h.group(1))
            hits.append((re.sub(r"\s+", " ", h.group(0))[:120], base + h.start(), v))
            evidence = True
            if v is not None:
                values.add(v)
    if len(values) == 1:
        v = next(iter(values))
        if 1 <= v <= cfg.annual_ads_ratio_max:
            return "known", v, hits
        return "unread", None, hits  # absurd — evidence yes, defensible divisor no
    if evidence:  # zero parses, or CONFLICTING distinct values (a mid-change filing states both)
        return "unread", None, hits
    return None, None, hits


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
    has_f6_filing: bool = False,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> list[ExtractedFact]:
    """Pure + deterministic: (companyfacts-or-None + the CLEANED annual filing text) -> the one
    shares_outstanding FLAG candidate, or ``[]`` when the cover cannot be read (FAIL CLOSED — the
    caller stamps the honest empty reason). ``report_date`` is the filing's PERIOD OF REPORT — what
    the SEC instruction dates the cover count to; ``today`` ages the chosen count for the staleness
    flag + the note (time is a parameter, never an implicit now). ``has_f6_filing`` = an F-6-family
    registration exists on EDGAR for this issuer — POSITIVE ADR evidence for the ratio detector
    (spec §10; its absence proves nothing and is never used as a negative).
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

    # the ADS ratio (spec §10) — the count stays the true ORDINARY count; the ratio rides as
    # derivation metadata for the market-cap scorer (apply where read, suppress where not).
    ads_status, ads_ratio, ads_hits = _detect_ads_ratio(annual_text, m.start(), has_f6_filing, cfg)
    if ads_status == "known":
        note += (
            f" ADS ratio {ads_ratio}:1 read from the filing — the market cap divides this ordinary "
            f"count by {ads_ratio} to price against the ADS."
        )
    elif ads_status == "unread":
        flags.append("ads-ratio-unread")
        distinct = sorted({v for _, _, v in ads_hits if v is not None})
        why = (
            f"CONFLICTING ratios stated ({', '.join(str(v) for v in distinct)})"
            if len(distinct) > 1
            else "no defensible ratio statement found"
            + (" (an F-6 is on file)" if has_f6_filing else "")
        )
        note += (
            f" ADR evidence present but the ADS ratio is UNREAD — {why}. The market cap is "
            "WITHHELD rather than guessed at 1:1; the ordinary count above is still the fact."
        )
    else:
        note += " No ADS/ADR evidence on the cover — the count prices 1:1 against the listed line."

    # the located passage — REQUIRED evidence (no passage -> no fact, enforced by the fail-closed
    # returns above): a bit before the instruction, through the LAST matched count + a tail, so the
    # subset/class wording is readable where the operator ratifies. Offset recorded, never filtered on.
    start = max(0, m.start() - _EXCERPT_PRE_CHARS)
    end = m.end() + nums[-1].end() + _EXCERPT_TAIL_CHARS
    excerpt = re.sub(r"\s+", " ", annual_text[start:end]).strip()
    passages = [
        LocatedPassage(
            kind="cover",
            source_ref=annual_ref,
            anchor=m.group(0),
            excerpt=f"… {excerpt} …",
            offset=m.start(),
        )
    ]
    if ads_hits:
        # the ratio's OWN evidence rides too (#6): the first parsed statement (the one that set the
        # value), else the first ratio-shaped hit — so the operator ratifies the division against the
        # filing's words, not a bare number. F-6-only evidence has no text site; the note carries it.
        best = next((h for h in ads_hits if h[2] is not None), ads_hits[0])
        htext, hoff, _ = best
        hexcerpt = re.sub(
            r"\s+", " ", annual_text[max(0, hoff - 60) : hoff + len(htext) + 100]
        ).strip()
        passages.append(
            LocatedPassage(
                kind="cover",
                source_ref=annual_ref,
                anchor=htext[:80],
                excerpt=f"… {hexcerpt} …",
                offset=hoff,
            )
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
            located_passages=passages,
            note=note,
            ads_ratio=ads_ratio,
            ads_ratio_status=ads_status,
        )
    ]


def _latest_annual_filing(
    client: EdgarClient, cik: int, *, subs: dict[str, Any] | None = None
) -> tuple[str, str, date, str] | None:
    """(doc_url, CLEANED text, report_date, form) of the latest annual foreign filing across 20-F AND
    40-F — selected from submissions METADATA first, then ONE document fetched (these run 0.2-25 MB;
    fetching both to compare would double the spend the cost thread bounds).

    Never prefer a form: ``filings_of('20-F') or filings_of('40-F')`` short-circuits — CRDL files
    both, and its non-empty 20-F list (2023) hid a newer 2025 40-F (the answer-key probe's own bug).
    Compare report dates; a tie keeps the 20-F (iteration order — date-identical either way).
    ``subs``: an already-fetched submissions JSON (the wrapper shares one read across selection and
    the F-6 scan); fetched here when omitted.
    """
    subs = subs if subs is not None else fetch_submissions(client, cik)
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
    subs = fetch_submissions(client, cik)
    filing = _latest_annual_filing(client, cik, subs=subs)
    if filing is None:
        return ExtractionResult(facts=[], empty_reason="no-annual-filing")
    url, text, report_dt, form = filing
    # F-6-family registrations (F-6, F-6/A, F-6EF, F-6 POS) = POSITIVE ADR evidence for the ratio
    # detector. Positive only — a real 5:1 name with no recent F-6 was measured, so absence proves
    # nothing (spec §10).
    forms = subs.get("filings", {}).get("recent", {}).get("form", [])
    has_f6 = any(str(f).startswith("F-6") for f in forms)
    facts = extract_annual_shares(
        _companyfacts_or_none(client, cik),
        text,
        annual_ref=url,
        annual_form=form,
        report_date=report_dt,
        today=today or date.today(),
        has_f6_filing=has_f6,
        cfg=cfg,
    )
    if not facts:
        return ExtractionResult(facts=[], empty_reason="cover-not-located")
    return ExtractionResult(facts=facts)


__all__ = ["extract_annual_shares", "annual_shares_for_security"]

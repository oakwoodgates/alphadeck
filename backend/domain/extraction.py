"""The scoring-fact extraction candidate model (the hybrid, Slice hybrid-1).

An ``ExtractedFact`` is what the FILING-PARSER produces per scoring fact — NOT a persisted fact. It is the
three-tier hybrid made concrete:

- ``AUTO`` — the extractor reproduces the value from companyfacts; pre-fill, confirm-and-go.
- ``FLAG`` — the raw value + a DETECTED risk + the LOCATED passage; the operator ratifies the composition in
  seconds. *The extractor never decides — it puts the right passage in front of the decision.*
- ``HUMAN`` — interpretation-bound (purity). The extractor LOCATES the evidence and the operator authors the
  value; it is NEVER auto-valued (purity is the operator's exposure-concentration edge).

Nothing here persists. On the operator's confirm (hybrid-2), the existing ``ingest_*`` writers append the
real fact — ``source_ref`` and ``note`` flow straight from this candidate.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from domain.base import DomainModel


class Tier(StrEnum):
    AUTO = "auto"  # reproduces the value (companyfacts) — pre-fill, confirm-and-go
    FLAG = "flag"  # raw value + a detected risk + a located passage — operator ratifies
    HUMAN = "human"  # interpretation-bound (purity) — located only, NEVER auto-valued


class LocatedPassage(DomainModel):
    """A deterministically-retrieved passage that backs a fact — the evidence put in front of the operator
    (or, later, S5). Retrieval only: a keyword/section match, never a model's reading."""

    kind: str  # "cash-flow" | "cover" | "balance-sheet" | "segment" | "business-description"
    source_ref: str  # the filing URL the excerpt came from
    anchor: str  # the keyword / heading that matched
    excerpt: str  # the surrounding text (deterministic window)
    # The anchor match's character offset in the CLEANED filing text — recorded for AUDITABILITY (the
    # annual-cover path stamps it), never filtered on: an offset bound is a tuned magic number that
    # drops real covers (CAJPY's genuine cover sits ~52k chars deep behind the inline-XBRL context
    # block). None on the passages that predate it.
    offset: int | None = None


# The sign of ``quarterly_burn_usd``, defined ONCE here. Every producer agrees: the periodic extractor
# (``ingest.edgar.extract._cash_burn``: ``burn = -operating_cash_flow``) and the annual runway path
# (``ingest.edgar.annual_runway``: negative OCF -> a POSITIVE quarterly burn) emit it, the ``cash_burn`` writer
# (``ingest.cash_burn``) stores it as given and the runway meter (``workbench.scoring._runway``) reads ``<= 0``
# as cash-generative. The read-only agent tools copy this sentence into their descriptions.
QUARTERLY_BURN_SIGN = (
    "quarterly_burn_usd is positive when the company consumed cash in operations over the quarter; "
    "zero or negative means cash-generative"
)


class ExtractedFact(DomainModel):
    """One candidate scoring fact for a security, with its tier, raw value(s), located evidence, and flags.
    The shape mirrors the three ``ingest_*`` writers: shares -> ``value``; cash_burn -> ``cash_usd`` +
    ``quarterly_burn_usd``; revenue_mix (purity) -> no value (HUMAN)."""

    fact_type: str  # "revenue_mix" | "shares_outstanding" | "cash_burn"
    tier: Tier
    source: str  # the provenance kind the ingest_* will store ("10-q" | "10-q-cover" | "10-k-segment" | ...)
    source_ref: str  # the filing URL
    event_date: date  # the filing period / cover date -> the fact's valid_from (no lookahead)
    note: str = ""  # the composition / derivation -> ingest_*.note
    # raw figures — None for HUMAN (purity) and for any value the extractor would only LOCATE, not parse
    value: float | None = None  # shares_outstanding
    cash_usd: float | None = None  # cash_burn
    quarterly_burn_usd: float | None = (
        None  # cash_burn; sign: see QUARTERLY_BURN_SIGN (positive = cash consumed)
    )
    # The statement's own ISO currency, set ONLY by the annual-statements runway path — a foreign filer
    # reports in its own currency (CNY/JPY/TWD/EUR/CAD/…) and the ``*_usd`` fields then carry those values
    # (the runway RATIO stays currency-correct). Since PR 4 (2026-11-15) it is ESTABLISHED, never guessed:
    # the companyfacts unit that reproduces the statement's figures exactly, else the header's explicit
    # markers (a bare "$" is not USD); None = UNREAD and flagged ``currency-unread`` (the FE renders "?",
    # never "$"). It rides through DISPLAY-ONLY; it is NEVER converted to USD and NEVER a scoring input. None
    # for every domestic/us-gaap 10-Q/10-K filer (the periodic ``_cash_burn`` path leaves it unset) → "$".
    statement_currency: str | None = None  # cash_burn (annual-statements path only)
    # The INVESTMENT part already inside ``cash_usd`` (operator decision D1, 2026-11-15: one "cash and
    # short-term investments" number, the investment part carried on its own so a screen can show the split):
    # the short-term investments plus the long-term MARKETABLE securities counted, on the cash date. None =
    # no investment tag on the cash date (never a fake 0). The annual-statements path sets it to the
    # short-term deposits/investments rows it added (PR 4). Stated in the note and stored on the candidate and
    # the fact (``investments_usd``, migration 0208).
    investments_usd: float | None = None  # cash_burn
    # One OBSERVED condition, one label (a flag is evidence, #6 — never a catch-all). A flag marks an
    # EXCEPTION needing judgment; COMPOSITION (a cleanly-derived quarter, the marketable-securities
    # basis) rides the note as provenance, never a flag — a flag true of ~every filer carries no
    # information (the re-tier; honest loudness):
    # shares -> "dual-class" | "stale-cover" | "no-companyfacts"
    #           | "cover-after-filing" (a companyfacts cover row dated after its own filing was refused;
    #             it rides beside the condition label, on any tier — the note names the row)
    # cash   -> "ytd-raw" | "possible-one-time" | "stale-cash"
    #           | "cash-tags-disagree" (two balance-sheet cash tags of one date contradict each other: a
    #             narrower line exceeds a wider one, SOAR's zero cash-and-equivalents beside 8.4M of cash)
    #           | "investments-unreconciled" (cash + short-term investments miss the same-dated combined
    #             ``CashCashEquivalentsAndShortTermInvestments`` beyond the tolerance)
    #           | "investments-off-date" (an investment tag sits on a NEWER balance sheet than the cash and
    #             not on the cash date: the composition at one date is not established)
    #           | "burn-span" (the burn covers fewer than quarterly_span_min_days or more than
    #             quarterly_span_max_days, native or derived: not established as one quarter; offered as found)
    #           | "burn-off-period" (the burn does not end on the cited filing's period of report)
    #           | "cash-after-period" (the cash is dated after the cited filing's period of report; an older
    #             cash date is "stale-cash")
    #           | "no-companyfacts" | "no-cashflow-column" | "no-cash-instant"  (missing-data: value None)
    # annual cash (``annual-statements``, always FLAG; PR 4 adds the last six):
    #           "annual-statements" | "unit-scale-unread" | "column-ambiguous" | "stale-runway"
    #           | "currency-unread" (the currency could not be established: a bare "$", headers that
    #             disagree, and no companyfacts unit reproducing the figures; statement_currency None)
    #           | "currency-mismatch" (companyfacts reproduces the figures in one currency, the header names
    #             another; the reproducing unit is used and both are stated)
    #           | "convenience-column" (a translated US$ column is printed beside the figure and
    #             companyfacts did not confirm which column was read)
    #           | "figures-disagree" (companyfacts has a row on the statement's date and none reproduces the
    #             statement figure at its printed precision; both numbers in the note. Was
    #             ``source-disagreement`` on this path, which compared only the guessed currency at 1%)
    #           | "table-unverified" (every candidate table looked like a parent-only, subsidiary or
    #             segment breakdown and companyfacts reproduced none)
    #           | "cash-unread" (the balance-sheet cash row was located but its value could not be read)
    flags: list[str] = Field(default_factory=list)
    located_passages: list[LocatedPassage] = Field(default_factory=list)
    # How an UNVERIFIED value estimate was produced, when one is present (SURFACE 1b): "llm_proposed" (the
    # grounded purity seam), and — as the surface grows — "computed" / "parsed". None = no estimate value
    # (a plain located-only HUMAN candidate, or the deterministic AUTO/FLAG value whose tier already says how).
    # Display-only provenance so the operator (and the UI) can see WHERE a proposed number came from before
    # they confirm/override; it never makes the value a fact (only the operator's ratify does).
    estimate_source: str | None = None
    # The ADS/ADR ratio for an ANNUAL-cover shares candidate (Retrieval Slice 1 §10): ordinary shares
    # per ADS. The cover states ORDINARY shares while the price feed carries the ADS price — they only
    # multiply at 1:1, so the ratio modulates the market-cap DERIVATION (never the count itself; the
    # value stays the cover's true ordinary count, married to its located passage).
    # ``ads_ratio_status``: "known" (a ratio N>=1 was READ from the filing — the scorer divides by it)
    # | "unread" (ADR evidence present but no defensible ratio: missing, fractional, or CONFLICTING —
    # the scorer SUPPRESSES the cap rather than guess 1:1) | None (no ADR evidence — 1:1; also every
    # 10-Q candidate). Rides the ratify body into ``fact_shares_outstanding`` as derivation metadata.
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    # THE BURN PACE (V2 extractor correctness PR 3; operator decision D3, ``domain.burn_pace``) — cash_burn, the
    # quarterly path only. ``ytd_quarterly_burn_usd`` = the year to date ending on the burn's end, scaled to one
    # quarter (burn sign: positive = burning); ``ytd_burn_days`` its span. None when there is no separate year to
    # date (a first fiscal quarter, an annual filer, a burn outside the quarter window). ``pace_caveat`` = one plain
    # sentence when the quarter is far off that pace (``pace_divergence``), else None. A CAVEAT, never a flag: it
    # never changes the tier. Carried here and stated in the note; not a stored column (the note is the storage).
    ytd_quarterly_burn_usd: float | None = None
    ytd_burn_days: int | None = None
    pace_caveat: str | None = None


class ExtractionResult(DomainModel):
    """The extract endpoint's envelope (Retrieval Slice 1): the candidates plus an HONEST reason when
    there are none. The three empty states are DISTINCT (interaction #2 — "we couldn't read it" must
    never masquerade as "there is nothing"; the old bare ``[]`` + the FE's *"nothing to extract or
    ratify here"* copy was false for 44 of the 48 dark names):

    - ``facts`` non-empty → covered. For an annual-cover name that is SHARES ONLY — cash + purity are
      still not covered for it (the FE says so rather than implying the data doesn't exist).
    - ``empty_reason="no-annual-filing"`` → no 10-K/10-Q AND no 20-F/40-F: genuinely nothing on EDGAR
      the extractor can read (SKHY, a brand-new F-1/DRS listing). The only case where "nothing to
      extract" is true.
    - ``empty_reason="cover-not-located"`` → an annual filing EXISTS but its cover instruction could
      not be matched: the name is UNREAD, not empty — it stays a visible candidate for the next
      pass, and companyfacts alone is deliberately NOT served (a fact without its located passage
      would break the no-passage-no-fact contract).

    ``runway_empty_reason`` (Retrieval Slice A) is the RUNWAY leg's own honest state for an annual
    filer whose filing exists but yielded no cash_burn candidate — kept SEPARATE from ``empty_reason``
    because the shares leg usually still emits (facts non-empty), and the three states must stay
    distinct (interaction #2):

    - ``"cash-generative"`` → operating cash flow is positive: a STATE, not a gap — no runway applies
      and none is computed (a finite number here would be bogus).
    - ``"financials-in-exhibit"`` → a BURNING name whose financial statements were found in NEITHER
      the main document NOR any EX-99 exhibit the bounded scan read (Retrieval Slice A-2 hunts the
      exhibits by content signature — both statement rows — before this reason fires): deferred,
      never a companyfacts-only number (no passage → no fact).
    - ``"exhibit-ambiguous"`` → MORE THAN ONE exhibit carries the statement signature (Slice A-2's
      fail-closed rule): deferred rather than guessed — a wrong statement source is the runway
      analog of a confident-wrong cover match.
    - ``"statements-not-located"`` → neither the statements nor a companyfacts sign could be read:
      unread, not empty.
    """

    facts: list[ExtractedFact] = Field(default_factory=list)
    empty_reason: str | None = None  # "no-annual-filing" | "cover-not-located" | None
    # "cash-generative" | "financials-in-exhibit" | "exhibit-ambiguous" | "statements-not-located"
    # | None (covered, or a domestic 10-Q/10-K name — the periodic path never sets it)
    runway_empty_reason: str | None = None

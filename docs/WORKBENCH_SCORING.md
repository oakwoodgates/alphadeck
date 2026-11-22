# Workbench scoring

How the Workbench scores a basket member: four 0-4 pip meters (purity, runway, catalysts, dilution) and the
market-cap figure, re-derived on every read and never stored. A user sees them on the scored rows of their own cards,
the Cockpit name panel and the `get_triage` agent tool. Read this before you change `workbench/scoring.py` or a pip
cutoff. How a filed value is parsed: [EXTRACTOR](EXTRACTOR.md). The shared share count and cash:
[PLATFORM_VALUES](PLATFORM_VALUES.md).

## Code

| Piece | Module |
|---|---|
| The engine: `score_member`, `score_thesis`, `_fit` | `workbench/scoring.py` |
| The cutoffs (`purity_pip_pct`, `runway_pip_months`, `catalyst_pip_*`, `dilution_pip_pct`) | `CallConfig` in `domain/config.py` |
| The scored read: `GET /workbench/theses/{thesis_id}/scored` (`get_scored`, `read_scored`) | `app/routers/workbench.py` |
| Screens | `frontend/src/workbench/ScoredRow.tsx`, `Meter.tsx` |

## How it works

- **Scores never persist.** A card reopened months later shows current numbers, never a snapshot; only the card's
  structure is stored.
- **Every figure comes from a tenant-filtered as-of read** (`PointInTimeData`), so no-lookahead and tenant isolation
  hold ([invariant 1](INVARIANTS.md#1-no-lookahead-history-is-judged-on-the-public-clock),
  [invariant 12](INVARIANTS.md#12-two-tenant-scopes-every-read-names-its-scope)). A past `asof` reads on the public
  clock (`domain/market_time.serve_view`): purity and cash count from when they were recorded, a share count from its
  filing's acceptance (`db/public_clock.py`).
- **"—" is not 0.** A meter with no underlying fact reads "—", never a zero.

| Meter | Reading | Fact |
|---|---|---|
| purity | the largest revenue-line `mix_pct` among the user's own purity facts; the basis rides `source` | `fact_revenue_mix` (workspace) |
| runway | months at last quarter's pace: `cash_usd` over a third of `quarterly_burn_usd`, from the one picked value (`pick_current`); a burn of 0 or less reads the top pip | `fact_cash_burn` (platform value) |
| catalysts | the count of live catalysts (`catalyst_conviction.liveness`, the call's own window) and their grade: none 0, one flip 1, one core 2, then the multi and dense counts | `fact_catalyst` |
| dilution | the raw convertible overhang (`dilution_clock.overhang_pct`); the top bar reuses the risk veto's `dilution_overhang_severe_pct`; converts below the first bar read 0, no converts "—" | `fact_dilution` |
| market cap (a figure, no pips) | the latest close times the picked share count, divided by a `known` ADS ratio and put on the close's split scale | `fact_price_eod`, `fact_shares_outstanding`, the split list |

With one input missing, market cap shows the other with a note naming the gap; an `unread` ADS ratio withholds it;
a split between the count's date and the close rescales the count (`tests/workbench/test_market_cap_split.py`).

## Cutoff rationale

Every cutoff is grounded in what the metric means, stated before any result, never fit to seed names: a bar moved so
a seed name lands on a pip overfits a handful of clustered names. Seed scores check the result, never source it.

- **Purity** measures concentration: the top bar is the conventional pure-play threshold, then a majority, a clearly
  material segment (above the SEC's segment-materiality floor) and the floor of relevance.
- **Runway** is a funding-risk gauge set by the financing cycle: raises tend to land a year to eighteen months out,
  the lowest bar signals distress, the top is about two raise cycles of cushion.
- **Catalysts** are deliberately strict: a binding (core) catalyst counts more than a provisional (flip) one, and one
  award is not a dense surface.
- **Dilution** reuses the risk veto's severe bar, so the two never disagree on "severe".

All cutoffs live in `CallConfig`: `tests/workbench/test_scoring.py::test_pip_cutoffs_are_config_driven` (a changed
cutoff moves a pip) and `::test_scorer_has_no_magic_number_thresholds` (no float literal in the scorer). Changing one
follows the dial rule in [CALL_LOGIC](CALL_LOGIC.md).

## Rules that must not regress

- **Purity is concentration, not safety.** A pre-revenue pure-play reads near the top: it is fully exposed to the
  thesis, and its funding risk belongs to runway and dilution, so purity is never discounted for it. The basis
  (`10-k-segment` or `10-k-business-description`) stays distinct.
- **Cash has one basis**: [EXTRACTOR](EXTRACTOR.md) "The uniform cash rule". The scorer trusts `cash_usd` as stored.
- **The dilution overhang has one source**
  ([invariant 8](INVARIANTS.md#8-the-dilution-overhang-is-computed-once)): the meter buckets the raw overhang and
  never recovers it from the clamped risk severity, which saturates at the severe bar.
- **Share counts are total economic**: every class counts toward market cap ([EXTRACTOR](EXTRACTOR.md) "Shares on
  the periodic path").
- **No `if kind ==` in the engine** ([invariant 14](INVARIANTS.md#14-behavior-keys-on-the-property-that-drives-it)):
  each meter keys on its own driving property.

### The recurring-vs-one-time burn pattern

The runway is meaningful only on the recurring burn: a one-time outflow (a settlement, a milestone payment) makes it
look far shorter. The extractor flags it (`possible-one-time`) and never backs it out; a person decides, and the
note records what was backed out and why. No single pass is authoritative on a sourced figure: check the filing.

## Fit label and provenance

- **Fit** (`_fit`) is a pure function of the pips: the purity pip picks the base label (`_FIT_BY_PURITY`; `unrated`
  without purity), and "funding risk" or "dilution risk" is appended past `_FIT_FUNDING_RISK_PIP` or
  `_FIT_DILUTION_RISK_PIP`.
- **Provenance** ([invariant 6](INVARIANTS.md#6-explainability-every-output-shows-its-source)): each meter carries
  its facts or computation, with filing links. `detail` carries the fact's own figures (the purity panel shows what
  is on file) and `set_by`, a kind (`machine`, `steward`, `carried`, or `you` for the user's own purity), never an
  email (`tests/app/test_no_steward_email_for_users.py`).
- **Dilution is the only risk meter**, drawn as the ember pressure meter (`Meter.tsx`): more pips is worse. The
  call's other risk signals have no meter.

## Display-only additions

- **Runway does not apply**: for a company `runway_applies` rules out, `score_thesis(..., runway_na=)` gives no pips
  or months, keeps the cash fact's provenance, adds a `runway:not-applicable` entry, and the fit adds no funding risk.
  The rule: [PLATFORM_VALUES](PLATFORM_VALUES.md) "Runway does not apply".
- **Pace and currency**: the runway's `detail` carries the year-to-date pace (`ytd_quarterly_burn_usd`,
  `ytd_burn_days`, `ytd_runway_months`, `pace_caveat`, from `domain.burn_pace.stored_pace`), the stored `currency`
  and `investments_usd`. The pace never moves the pips, months, fit or sort
  (`tests/workbench/test_runway_pace_display.py`); screens label cash in its own currency.
- There is no "unconfirmed estimates" count: a blank runway or market cap is a platform value's state, read from
  `GET /platform-values/...`; a blank purity is the user's to set.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /workbench/theses/{thesis_id}/scored` | anyone signed in, a thesis in their own workspace (404 otherwise) | `get_thesis_or_404` |

## Known limits

- Market cap prices off the latest bar however old, so a stale or delisted company still shows one.

## Related

[EXTRACTOR](EXTRACTOR.md), [PLATFORM_VALUES](PLATFORM_VALUES.md), [WORKBENCH](WORKBENCH.md),
[CALL_LOGIC](CALL_LOGIC.md), [INVARIANTS](INVARIANTS.md).

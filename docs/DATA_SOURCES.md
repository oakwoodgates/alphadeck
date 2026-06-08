# DATA_SOURCES.md

> Repo path: `docs/DATA_SOURCES.md`. Consolidates where Alpha Deck's data comes from, free vs. paid,
> and which capability each source powers. Posture: **bootstrap on free sources; pay case-by-case for the
> right thing.** Cadence is EOD (nightly batch + on-demand pulls) — no streaming/intraday in v1.

---

## Free baseline (v1 runs entirely on these)

| Source | What it gives | Powers |
|---|---|---|
| **SEC EDGAR** — submissions API, full-text search, daily index/RSS | 8-Ks, Form 4 (insider), 10-K/Q, S-1/S-3 (offerings), 13D/G, 13F | filing intelligence, insider-conviction, dilution clock, language-diff, first-footprint |
| **EDGAR — XBRL financial statement datasets** | structured financials (cash, burn, shares outstanding) | dilution clock, fundamentals |
| **EDGAR — N-1A / 485 registrations** | new fund/ETF registrations & launches | **ETF radar** (coming launches = emergence signal) |
| **USASpending.gov** — federal award API (`spending_by_award` + `awards/{id}`) | DOE contracts, grants, OTAs, loan guarantees + structured terms (obligation, period of performance) | **catalyst-conviction** (the automated DOE feed, §below) — the first automated Key-1 source |
| **Public ETF holdings** (issuer/fund daily holdings files) | constituents, weights, expense ratio, AUM | **ETF radar** (universe seed + holdings/flows); pure-play scoring |
| **FINRA short interest** (bi-monthly) | SI %, days-to-cover | squeeze radar (context tier) |
| **OpenFIGI** (free API) | CIK ↔ ticker ↔ CUSIP ↔ FIGI mapping | security master / entity resolution |
| **Free EOD price/fundamentals** (e.g. Stooq/Tiingo-free tier/equivalent) | daily OHLCV, splits/dividends, market cap | momentum-health, laggard scanner, liquidity/float, corporate actions |
| **On-chain / crypto data** (free, rich) | TVL, holders, flows for crypto-adjacent themes | emergence detector *for crypto-adjacent themes only* |

ETF **flows** (inflows/outflows) can be **derived for free** from daily shares-outstanding × NAV deltas
before paying a vendor.

## Paid, case-by-case (later, only when a module needs it)

| Source | Unlocks | Note |
|---|---|---|
| **Borrow fee / utilization** (daily) | the *real* squeeze signal (leads FINRA SI) | the squeeze radar's upgrade path |
| **Options / gamma** (e.g. ORATS-class) | gamma overlay, dealer positioning | gamma squeezes; options expression |
| **Premium fundamentals** | cleaner/deeper financials, estimates | quality-of-life, not required for v1 |
| **ETF flow data** | precise creation/redemption flows | only if the derived flows prove insufficient |

Decision rule: a paid source is justified only when a module's *signal quality* materially depends on it
and the free proxy is demonstrably inadequate. Default to free + derive.

## EDGAR etiquette (a correctness requirement, not a courtesy)

- Declared **User-Agent** with contact (config/env, not hardcoded).
- Respect the documented **rate limit** (token-bucket gate in the client).
- **Cache-first** on disk (`data/edgar_cache/`); the test transport raises on cache miss so tests never hit the network.
- Live pulls are explicit opt-in (env flag) and write only to the cache.

## USASpending (DOE awards) — the automated catalyst feed `[BUILT, #37]`

The first **automated** catalyst-conviction source (`ingest/doe/`). It discovers DOE awards for a hand-curated
set of nuclear-basket entities and emits catalyst facts deterministically (grade + horizon from the structured
terms — invariant #3, never model-sourced). Grade rule = customer-vs-sponsor (`docs/CATALYST_CONVICTION.md`).

**Entity resolution is a curated allowlist, keyed on exact `recipient_id` — never fuzzy.** This is the
load-bearing decision; the spike found three reasons it has to be:

1. **API quirk — the `recipient_id` filter is silently ignored.** Passing `recipient_id` to
   `spending_by_award` does **not** filter; it returns *all* DOE awards by size. So we can't ask the API for
   "this recipient's awards" directly.
2. **Trap — fuzzy over-match.** `recipient_search_text="Centrus"` also returns **NAC INTERNATIONAL INC.**
   (unrelated). Text search is a wide net, not a resolver.
3. **Trap — the polluted homonym.** `recipient_search_text="Oklo"` surfaces **OKLO TECHNOLOGIES, INC.** — a
   *different* recipient_id carrying **$48B of national-lab management contracts** (Sandia/LANL/ORNL `DEAC…`).
   The real awardee is **OKLO INC.** Fuzzy-matching "Oklo" onto it would pin $48B of M&O contracts to a
   pre-revenue ticker, silently.

**So: fuzzy search is only a discovery NET; the ticker is assigned solely by exact membership in the curated
table** (`ingest/doe/entities.py`), keyed on `recipient_id`. It's an **allowlist, not a denylist** — an
unknown recipient is *dropped* (unresolved), never guessed. Subsidiary → parent → ticker is encoded by giving
each recipient_id its own row: `AMERICAN CENTRIFUGE OPERATING, LLC` + `CENTRUS ENERGY CORP.` both → `LEU`;
`OKLO INC.` → `OKLO`. Both traps have **rejection tests** pointed at them
(`tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy` asserts NAC / OKLO TECHNOLOGIES / unknown
all resolve to `None`).

**Pipeline:** discover (search net, one award-type group per call — the API 422s on mixed groups) → resolve
exactly by recipient_id → fetch award detail → derive grade + base-period-of-performance horizon → emit a
`fact_catalyst`. Expired awards are emitted too (real, provenanced) but liveness keeps them off the card.

**Etiquette** (mirrors EDGAR): cache-first on disk (`data/doe_cache/`; committed fixtures in
`backend/seed_data/doe/`), live pulls explicit opt-in, a rate-limit gate, the test transport raises on a
cache miss so the suite never hits the network.

## Point-in-time discipline (applies to every source)

Every ingested fact lands with `valid_from` = event/effective time and `recorded_at` = ingest time, into
the bitemporal store (see `CLAUDE.md`). No source is read in a way that lets a detector see data dated
after its `asof`. This is what makes the replay harness honest.

## Notes on the ETF radar's three faces

1. **Availability** — map theme → expressing ETFs (curation + holdings-overlap against the theme universe).
2. **Coming** — watch EDGAR N-1A/485 for new thematic registrations; a fresh launch is an emergence-kind
   signal (early tell, occasionally a top).
3. **Holdings & flows** — holdings seed the decomposition for free; holdings changes + derived flows are a
   positioning signal. Always expose fund internals so a mislabeled/thin/expensive fund is visible.

# Post-mortem: the 11-day EDGAR cache freeze (2026-07)

A point-in-time record, true as of 2026-07-17, never updated. Current rules live in the docs it names.

## What happened

For about 11 days the nightly call-of-record pass ran "successfully" while seeing no new insider filings for any
company already cached. Each night recorded a call on frozen data, and its tally (0 facts appended) matched a
healthy night with nothing filed. The operator caught it: a fresh thesis "has no insider buys". Scoring treats
arms from 2026-07-10 to 2026-07-17 as made inside the freeze (`FREEZE_WINDOW`, `backend/scoreboard/provenance.py`).

## Cause

- `EdgarClient` served any cached key forever. The per-issuer `submissions` index changes when a Form 4 is
  filed, but was treated as immutable.
- A frozen pass and a quiet day looked identical; nothing recorded whether the pass reached the network.
- It was latent: container rebuilds used to wipe the cache, an accidental refresh. PR #125 persisted the cache,
  a correct change that removed the mitigation.

## Fix

- R1 (#196): key-classed cache. `forms/<accession>/<doc>` is cached forever; every other prefix refreshes on a
  12h TTL when live.
- R2 to R6 (#197 to #200): a recording gate, a per-run log, a freeze page and catch-up on boot.
- A live catch-up thawed the cache: 18,190 requests (a frozen pass makes 0), about 70 minutes, six of six
  theses fresh, and the fresh thesis armed on real insider and price data.
- #202 pinned the container `TZ`; a cache-served rerun (1 fetch, 53.6 s) put the record on the right night.

## The rules it left

| Rule | Lives in |
|---|---|
| Freshness comes from the cache key's class, never a per-call flag; a new mutable endpoint is safe by default | [DATA_SOURCES](../DATA_SOURCES.md#cache-freshness) |
| A pass that cannot ingest withholds its call; a partial ingest records `ingest_fresh=false` | [FEED_LOOP](../FEED_LOOP.md#recording-the-call) |
| The freeze page reads completed fetches per company, not a zero count (zero also fits a healthy run inside the TTL) | `docs/FEED_MONITORS.md` |
| A pass that never runs is caught by an external heartbeat; a missed night is caught up | [CRON_SIDECAR](../CRON_SIDECAR.md#failure-handling), [FEED_LOOP](../FEED_LOOP.md#missed-and-late-nights) |
| "Today" is a domain fact: `market_today` on `Settings.market_tz`, never the ambient clock | [INVARIANTS](../INVARIANTS.md#13-the-call-assembler-is-pure-and-deterministic) |
| A Form 4/A is stored apart and resolved at read (an amended-away code-P buy had still fired) | [FILINGS](../FILINGS.md#form-4a-stored-apart-resolved-at-read) |
| An old cover share count wears the `stale-cover` flag (#201) | `domain/extraction.py` |

Lessons: a test on a fresh entity cannot prove freshness for aged ones; a
guard that fires is information (`already_ran_live` returned False after a clean run and exposed the TZ bug).

## Evidence

- **The freeze.** DELL's cached latest Form 4 was 2026-06-30; live was 2026-07-14. Of 1,477 cached
  `submissions` files, 423 dated 2026-07-06 were 11 days blind.
- **The thaw.** One thesis went from +0 to +583 insider transactions; DELL advanced 2026-06-26 to 2026-07-10
  (+246 rows); the 07-06 cohort fell from 423 to 351.
- **Misreads.** Runs logged at 22:30, 23:00 and 23:35 on 07-13 to 07-15 took about 1 s, 30 min and 65 min:
  duration, not schedule drift. The 07-13 cron run
  (1.64 s, 0 insider facts, 24 price bars) was a total ingest failure that fell through and recorded: the gap
  the recording gate closed.
- **Amendments.** In the 250-name basket universe: 1,126 Form 4/A, 1.8% of all Form 4s, on 162 names.
- **ENDV.** Auto-applied shares reproduced a 2.5-year-old cover count (318,751,597, 2023-12-28). Four of 11
  pre-fix counts checked at 0.0% delta: no harm landed.
- **The TZ forensic.** The manual catch-up ran in the backend container (`TZ` unset, so UTC); at about 02:xx
  UTC `date.today()` gave 2026-07-18 and recorded tomorrow's `asof`; the cron container (EDT) gave the 17th.
  The nine ambient `date.today()` sites it found are what `market_today` replaced.

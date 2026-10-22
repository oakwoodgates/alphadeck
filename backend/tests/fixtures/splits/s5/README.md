# Split plan S5 — the split-night hold's golden fixtures (real data only)

Read by `tests/split_s5_fixtures.py`. Nothing here is synthesized, rounded or rescaled.

| File | What | Where it came from |
|---|---|---|
| `nights.json.gz` | For each company: EVERY stored version of `fact_price_eod` in its window (numerics as strings, each with its real `recorded_at`), and the newest version of each of its split-list rows (`security_split`, every content column) | A read-only `pg_dump` of PROD taken 2026-10-22 11:26 ET under `default_transaction_read_only=on`, restored into a scratch database on the DEV Postgres and read there in a read-only session (the scratch DB was dropped afterwards). |

The companies and why each is here:

| Ticker | Window | The case |
|---|---|---|
| GAME | 2026-04-15 .. 09-04 | 1-for-8 reverse split, first session 08-24 (SEC 8-K 08-19 + Polygon). Carried 10 fake armed rows on the record (Q10). |
| NXL | 2026-04-20 .. 09-04 | 1-for-30, first session 08-31; the vendor re-scaled the 08-28 bar a session early. |
| MDCX | 2026-06-01 .. 10-16 | 1-for-50 (Polygon 10-12; the SEC 8-K came 10-16); the vendor re-scaled 10-08 on the 10-09 night. |
| VRME | 2026-05-20 .. 10-08 | 1-for-10, first session 09-29 (SEC 8-K 09-25). |
| ZCSH | 2026-05-20 .. 10-07 | A real FORWARD split, 1-for-3 (3 new shares per old): Polygon only, still needs review; the tape steps down on 09-29. |
| PTIX | 2025-01-05 .. 05-27 | A genuine x3.7 day on 2025-05-19 (volume x1,239) — two weeks after a listed 1-for-14. |
| JAGX | 2026-05-15 .. 09-30 | A genuine x12.9 day on 2026-09-22 (volume x383) and the fall after it — days after a listed 1-for-15. |

A test varies ONE thing: whether the company's split row is seeded. The bars are always the stored ones, so the
public clock (`db.public_clock`) decides what a history view saw: without the row the night reads two scales;
with it, one.

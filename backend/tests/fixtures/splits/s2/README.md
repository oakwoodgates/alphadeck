# Split plan S2 — test fixtures (real data only)

Read by `tests/split_s2_fixtures.py`. Nothing here is synthesized, rounded or rescaled.

| File | What | Where it came from |
|---|---|---|
| `tapes.json.gz` | Stored price tapes (the latest version per date; EVERY version for BTOC, OPTT, VRME, YYAI and LRHC) and each company's split-list rows | Read-only from the DEV database (seeded one-way from prod) on 2026-10-20, after the S1 backfill. Numerics kept as strings. KITT (latest per date + its three split rows) was added the same way the same day for the B1 review fix; it is outside the dev repair set and was never touched by it (asserted at extraction). |
| `yahoo_<SYM>.json.gz` | The vendor's chart payloads for BTOC, OPTT, BURU, VWAV (pull 1) and PBM (pull 3) | The split-tape diagnosis's `fetch_eod` mirror, 2026-10-20 05:19 UTC (same URL and `range=2y` as the price leg). Byte-for-byte, gzipped. |
| `insider.json` | Code-P Form 4 rows: OPAD 2026-06-02 / 06-03 (CEO), BURU 2025-12-31 (co-CEO), YYAI 2025-10-08 (10% owner) | `fact_insider_txn`, newest version per natural key, read-only from DEV. |
| `shares.json` | 10-Q cover share counts for BTOC and OPTT | `fact_shares_outstanding`, read-only from DEV. |

The raw (unadjusted) Polygon prints the repair tool's `--spot-file` reads live beside the tool:
`backend/pipeline/split_evidence/polygon_raw_2026-10.json` (its `_provenance` names the grouped-daily files).

Split statuses are the list's real ones: MI, WNRS and ATRO still need review. A test that needs one of them in
force names it in `confirmed=` (the stand-in for the operator's confirm; the values are the list's own).

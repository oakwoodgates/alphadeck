# Split-list fixtures (split plan S1) — real data, trimmed

Every file here is a real SEC or Massive.com response downloaded for the 2026-10-20 split-tape diagnosis
(`docs/temp/split-tape-diagnosis-2026-10-20.md`, §2's per-company table is the acceptance key). Nothing is
hand-written or edited; the only change is TRIMMING, and the source's spelling is kept byte-for-byte.

- `sec/<accession>.txt.gz` — an EDGAR full submission (`https://www.sec.gov/Archives/edgar/data/<cik>/<acc>/<acc>.txt`)
  cut to its SGML header plus the documents the extractor reads: the form's own document (8-K / 6-K) and its
  `EX-99.x` press releases. Graphics, XBRL, charter exhibits (EX-3.x) and other exhibits are dropped. Gzipped
  (the kept documents are HTML; 2.0 MB of it compresses to ~0.3 MB) — `zcat` reads them.
- `sec/MI_ex991_0930.htm.gz` — NFT Ltd (MI) 6-K `0001213900-26-105247` EX-99.1 (interim financial statements;
  the 1-for-80 consolidation is in a note). Byte-exact.
- `sec/WNRS_1aa.htm.gz` — Winners, Inc. (WNRS) 1-A/A `0001493152-26-016607`, the offering circular document
  (`PART II AND III`). Byte-exact.
- `submissions/CIK<10>.json` — the submissions indexes of NFT Ltd (MI), Pop Culture Group (CPOP) and Mega
  Matrix (MPU), as `data.sec.gov` served them. Byte-exact.
- `polygon/page{1,2,3}.json` — Massive.com `/v3/reference/splits?execution_date.gte=2025-08-01` (3 pages, 2,219 rows),
  with `results` filtered to the diagnosis tickers (32 rows); `status`, `request_id` and `next_url` as served
  (the request carried no API key, so none is stored).
- `polygon/rows_2024-10.json` — the 7 real rows, from the DEV backfill's 2024-10-01 pull (2026-10-20, the
  cached pages in the dev container), that pair with the filings added from that run (below).
- `polygon/rows_2026-10-22.json` — S4: the 6 real rows for GAME / NXL / MDCX / VRME / AXG / ENHA from the
  nightly-shaped pull of 2026-10-22 (`execution_date.gte=2026-07-24`), `results` filtered to those tickers.
- `tape_2026-10-22.json` — S4: EVERY stored version (close, volume, `recorded_at`) of the price bars around the
  GAME / NXL / VRME / MDCX / FDCT splits, read from a read-only copy of prod on 2026-10-22, untouched — the
  tape-confirmation tests read each bar's FIRST-served version from it, exactly as the detector does.

The table's LAST SEVEN rows (ELBM … LGHL) were added from the 2026-10-20 dev backfill: each was copied from
the dev EDGAR cache (the text the backfill read) and trimmed the same way — filings the first extractor sent to
review, kept as the regression set for the phrasings it then learned.

| accession | filer | form | what it states |
|---|---|---|---|
| 0001640334-26-001421 | ARTL | 8-K | 1-for-9, split-adjusted trading 2026-08-31 |
| 0001213900-26-108468 | BTOC | 8-K | 1-for-16, post-split trading 2026-10-15 |
| 0001193125-26-379008 | BURU | 8-K | 1-for-40, OTC split-adjusted 2026-09-02 |
| 0001213900-26-098435 | CPOP | 6-K | 15-for-1 consolidation, 2026-09-14 |
| 0001213900-26-024093 | DUKR | 8-K | 1-for-25 (as "twenty five (25) -for- one (1)"), 2026-03-06 |
| 0001493152-26-043847 | GGRP (prices as BTLN) | 8-K | 1-for-8, 2026-09-28 |
| 0001213900-26-097860 | LRHC | 8-K | 1-for-6, 2026-09-08 (plus the authorized range) |
| 0001213900-26-046153 | MI | 6-K | proxy: a RANGE only ("up to one-for-two hundred (1:200)") — the extractor abstains |
| 0001213900-26-053864, -055196 | MI | 6-K | no split ratio |
| 0001213900-26-098825 | MPU | 6-K | 1-for-20, 2026-09-16 |
| 0001193125-26-263723 | OPAD | 8-K | 1-for-10, 2026-06-09 (plus the proxy's range) |
| 0001493152-26-042282 | OPTT | 8-K | 1-for-30, 2026-09-14 (plus the range) |
| 0001104659-26-100333 | REAX | 6-K | 10-for-1 consolidation, 2026-08-25 |
| 0001493152-26-035388 | SEGG | 8-K | "7 for 1" reverse, 2026-08-03 |
| 0001493152-25-023264 | SMX | 6-K | "8:1", 2025-11-18 |
| 0001493152-26-021807 | SMX | 6-K | 1-for-20, 2026-05-11 |
| 0001214659-26-011966 | VRME | 8-K | 1-for-10, 2026-09-29 |
| 0001731122-26-001258 | VWAV | 8-K | 1-for-20, 2026-09-22 |
| 0001493152-26-039258 | YYAI | 8-K | 1-for-20, 2026-08-17 |
| 0001213900-26-009093 | PBM | 6-K | 1-for-6.25, moved to 2026-02-02 (restates a past 1-for-7.97) |
| 0001171843-24-007098 | ELBM | 6-K | "four (4) pre-Reverse Split shares for every one (1) post-Reverse Split share", trading 2025-01-02 |
| 0001213900-26-105269 | CMND | 6-K | 1-for-8; "will begin trading … after implementation of the reverse split will be Monday, October 5, 2026" |
| 0001641172-25-014877 | ERNA | 8-K | "Effective June 12, 2025, … at a ratio of 1-for-15" |
| 0001493152-25-001874 | SMX | 6-K | "every twenty-eight and one half ordinary shares … combined into one", 2025-01-15 |
| 0001493152-26-031419 | NIPG | 6-K | ADS ratio change, "the same effect as a one-for-thirty reverse ADS split", effective 2026-07-06 |
| 0001213900-25-110404 | ASNS | 8-K | 1-for-10, 2025-11-18 (plus "… in the aggregate, exceeds 1-for-12", a ceiling) |
| 0001213900-24-111258 | LGHL | 6-K | interim results restating a PAST (2023-07-13) one-for-fifty ADS ratio change |

S4 (the nightly detection, 2026-10-22) added six more, copied from the EDGAR cache the S4 recall run read and
trimmed the same way (each extracts identically to its full submission — checked when trimmed):

| accession | filer | form | what it states |
|---|---|---|---|
| 0001493152-26-039213 | GAME | 8-K | 1-for-8, split-adjusted trading 2026-08-24 (filed 08-19, ahead of the split) |
| 0001829126-26-009387 | NXL | 8-K | 1-for-30, trading 2026-08-31 (filed 08-27) |
| 0001062993-26-005382 | MDCX | 8-K | 1-for-50, a LEGAL effective date 2026-10-12, filed 10-16 — after the split |
| 0001493152-26-040184 | FDCT | 8-K | 1-for-100, trading 2026-07-10, filed 08-26 — 47 days after, outside S1's window |
| 0001185185-26-004872 | MTEK | 6-K | a proxy RANGE, "every two (2) to two hundred and fifty (250) … into one (1)" — abstains |
| 0001493152-26-039876 | WSHP | 6-K | interim results restating a PAST 1-for-4 (effective 2025-10-22) |

The Item 8.01 / 7.01 reader (2026-10-23) added ten more 8-Ks and one submissions index, copied from the scratch
EDGAR cache the 2026-10-23 prod dry run read (the app's `EdgarClient`, live) and trimmed the same way (each
extracts identically to its full submission — checked when trimmed). Items as the filing's own index lists them:

| accession | filer | items | what it states |
|---|---|---|---|
| 0001628280-26-066196 | ENHA | 8.01 | 1-for-10, "effective at 12:01 a.m. Central Time on October 29, 2026" AND split-adjusted trading from the 10-29 open (Massive.com lists 10-28) |
| 0001628280-26-063260 | ENHA | 5.02, 7.01, 9.01 | no split |
| 0001628280-26-058985 | ENHA | 8.01, 9.01 | "one warrant for every two shares acquired … if the business combination is consummated" — no split |
| 0000913760-26-000034 | SNEX | 8.01, 9.01 | three-for-two as a stock dividend ("one additional share … for every two"), trading 2026-07-20 |
| 0000913760-25-000021 | SNEX | 2.02, 8.01, 9.01 | the same inside an earnings release, trading 2025-03-24 |
| 0001193125-26-408382 | ZCSH | 8.01, 9.01 | a trust's 3-for-1 forward share split ("two additional shares for every one"), 2026-09-30 |
| 0000950170-24-129219 | ETH | 8.01, 9.01 | a trust's 1-for-10, effective 5:00 p.m. ET 2024-11-19 (a LEGAL date) |
| 0001493152-26-034724 | JUNS | 5.07, 8.01, 9.01 | a RANGE only, "not less than 1:10 and not more than 1:100" — abstains |
| 0001628280-26-064753 | ASST | 8.01 | restates a PAST 1-for-20 "effected on February 6, 2026" (filed 10-05) |
| 0001213900-26-102384 | AIRE | 7.01, 9.01 (8-K/A) | "as adjusted for the Company's 1-for-25 reverse stock split"; the only date a cue reaches is 2025-12-31, in a tax table |

- `submissions/CIK0001956439.json` — Enhanced Group (ENHA)'s submissions index as data.sec.gov served it on
  2026-10-23. Byte-exact. Its 8-Ks in the backfill window of the 10-28 Massive.com row are the three above, plus
  three under other items (5.07 / 5.02 / 2.02) the reader never opens.

The data-hygiene pass (2026-10-24: the restatement screen, the reading fixes, the targeted wide-form reader) added
ten full submissions and three wide-form PRIMARY documents, copied read-only from prod's EDGAR cache
(`/data/edgar_cache/forms/`) and trimmed. A full submission keeps its SGML header and the documents the
extractor reads, as above; inside them — and inside a primary document — whole `<table>`, `<tr>` and `<p>`
elements that no reading needs were dropped too (the 6-K interim reports are 4–9 MB). EVERY trimmed file was
CHECKED to extract identically to its full source under every reading: this branch's (bare, the catch-all
screen, the restatement screen; `wide=True` for a primary document) AND the 2026-10-23 reader's (origin/main
16c201d), so each still reproduces the wrong reading its test names.

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001213900-26-093515 | ZNB | 6-K | 1-for-8 "with effect from 27 July 2026"; a charter exhibit dated by the EGM "held on 10 September 2026" (read as the first session until 10-24) |
| 0001213900-26-078551 | DFNS | 8-K (3.03, 5.03) | raises the 1-for-50 "as disclosed on the … Form 8-K filed … July 13" to 1-for-125, split-adjusted open 2026-07-20 |
| 0001213900-26-077658 | DFNS | 8-K (3.03, 5.03) | the superseded 1-for-50, 2026-07-20 |
| 0001493152-26-024723 | MEDS | 8-K (3.03, 5.02, 5.03) | "will become effective … on May 26, 2025" (a typo; its EX-99.1 says May 26, 2026) |
| 0001213900-26-026965 | CMND | 6-K | interim results: 1-for-40 of 2025-12-15, an investee's 1-for-6, a boilerplate 2026-01-31 |
| 0001213900-26-067098 | CPOP | 6-K | meeting results: 10 -> 1 resolution, a 2023 consolidation, a proxy dated 2026-04-24 |
| 0001213900-26-002516 | PBM | 6-K | a past 1-for-7.97 and a "250:1 is not exceeded" ceiling |
| 0001104659-26-108260 | KXIN | 6-K | interim results restating 60 -> 1 (2024-10-25, also undated) and 15 -> 1 (2026-03-13) |
| 0001575872-24-001124 | RDY | 6-K | a 1:5 sub-division "effective October 28, 2024" (the record date) beside fiscal dates |
| 0001493152-26-041303 | CANG | 6-K | "a 10-for-1 share consolidation …, which became effective on July 20, 2026" (45 days back — the open review card) |

- `primary/<accession>/<file>.gz` — a wide form's primary document, under the name the targeted reader fetches it
  by (`forms/<accession>/<file>`): DFNS's 10-Q `0001185185-26-003604` (1-for-125, first session 2026-07-20),
  MI's 20-F `0001213900-26-050939` (restates its 2024 one-for-fifty), OFAL's 10-K `0001493152-26-033093` (the
  approved 1-for-10, undated, beside a 2024 reorganization's "Effective on August 29, 2024").
- `submissions/CIK0001787518.json` (DFNS) and `submissions/CIK0002036307.json` (OFAL) — their submissions indexes,
  copied byte-exact from prod's cache (2026-10-24): the targeted reader selects their wide forms from them.
- `polygon/rows_list_2026-10-24.json` — the Massive.com rows prod's split list CARRIES for LGHL 2025-03-26, MEDS,
  DFNS, ZNB, OFAL and SDCO (its 1-for-500 of 2025-04-24, listed under its FORMER ticker HLLK) (its stored `polygon_*` columns: Massive.com's own id, ticker, execution date and ratio, as the
  backfill stored them from the 2026-10-20/22 pulls), copied — not typed — into Massive.com's page shape.

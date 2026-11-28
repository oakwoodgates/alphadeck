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
- `polygon/v1_2026-11-25.json` — W6-1: Massive.com
  `/stocks/v1/splits?execution_date.gte=2024-10-01&limit=5000&sort=execution_date.asc` as the 2026-11-25 probe
  pulled it (`api.massive.com`, one page, 3,521 rows, no `next_url`; the probe cached it parsed, so the rows are
  re-serialized, keys and values as served), `results` filtered to 42 rows: the 32 Massive.com ids of
  `page{1,2,3}.json` (the same rows on the new endpoint), GAME and NXL (S4), and the label cases: SCCO 2026-08-11
  and 2026-11-03, MTH 2025-01-03 and GPUS 2024-12-13 (`stock_dividend`), HON 2025-10-30 and SNDK 2026-11-20
  (`forward_split`), GNS 2026-11-20 (`reverse_split`), JZ 2026-12-24 (one of the 97 rows with no
  `historical_adjustment_factor`). No row carries an unknown label; `tests/ingest/test_polygon_splits.py`
  synthesizes one from HON's row, marked there.
- `tape_2026-10-22.json` — S4: EVERY stored version (close, volume, `recorded_at`) of the price bars around the
  GAME / NXL / VRME / MDCX / FDCT splits, read from a read-only copy of prod on 2026-10-22, untouched — the
  tape-confirmation tests read each bar's FIRST-served version from it, exactly as the detector does.
- `unplaced/nightly_2026-11-25.json` — F1-1: prod's 2026-11-25 nightly run artifact (`data/cron_runs`, cron
  `code_sha` 6ecc8802), cut to its run identity and its `split_detect` block: `unplaced` keeps 5 of its 65 rows
  (WLDS, META, EVGN, TURB, SPRC), `appended`, `kept` and `errors` are emptied. Rows written before an unplaced
  filing carried its evidence (no `url`, `passage` or ratio).

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

The date-sentence fixes (2026-11-25, W2-5) added nine full submissions, fetched from EDGAR
(`https://www.sec.gov/Archives/edgar/data/<cik>/<acc-no-dashes>/<acc>.txt`) through the steward review's paced,
cached reader and trimmed to the SGML header plus the documents the extractor reads, each kept WHOLE and
byte-exact (nothing inside a kept document is dropped, so every reading of the trimmed file is the full
submission's). On the pre-change extractor each reproduced the reading prod's split list stored
(`docs/temp/platform-fixes-w0-w2-plan-2026-11-25.md` §2):

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001493152-25-028222 | COPR | 8-K (8.01, 9.01) | 1-for-20 "effective on the OTC Markets at the open of business on December 15, 2025"; the release's dateline 12-16 |
| 0001493152-25-018165 | NVA | 6-K | an ADS ratio change (4 additional ADSs for each ADS); "The first day of trading under the new ratio is expected to be October 29, 2025" |
| 0001015820-25-000012 | QGEN | 6-K | a 36 -> 35 consolidation; "the last trading day of the prior ISIN" 01-28, trading from 2025-01-29 |
| 0001178913-26-004186 | IMCC | 6-K | 30 -> 1, post-consolidation trading 2026-08-27; the risk factors' "year ended December 31, 2025" |
| 0001213900-26-113338 | SCAG | 6-K | an ADS ratio change to 1:15, effective 2026-10-27; the dateline 10-22 in a headline naming the "Effective Date" |
| 0001213900-26-122017 | PBM | 6-K | interim results restating its 6.25:1 of 2026-02-02; "not yet effective for the period ended September 30, 2026" |
| 0001493152-26-022840 | EZRA | 8-K (5.03, 7.01, 9.01) | 1-for-40, split-adjusted trading 2026-05-18; "the June 10, 2026 compliance deadline" |
| 0001213900-26-093996 | VEEA | 8-K (1.01, 2.03, 3.03, 5.03, 7.01, 9.01) | 1-for-20, effective 4:30 p.m. 08-28, trading 2026-08-31 (written "August 31 , 2026") |
| 0001493152-26-049433 | ALPS | 6-K | 5 -> 1; "The Authorized Share Capital Changes will be effective at market open on November 9, 2026" (tied to the split only by a defined term: still no date) |

The ratio phrasings (2026-11-25, W2-6) added five full submissions, fetched and trimmed the same way (the SGML
header plus the documents the extractor reads, each kept whole and byte-exact; every reading of each trimmed file,
on the pre-change and the changed extractor, CHECKED identical to its full submission's). On the pre-change
extractor each reproduced the reading prod's split list stored (`docs/temp/platform-fixes-w0-w2-plan-2026-11-25.md`
§2):

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001373715-25-000321 | NOW | 8-K (5.03, 5.07, 9.01) | a 5-for-1 "with a proportionate increase in … authorized common stock"; "four additional shares for each share"; split-adjusted trading 2025-12-18 (read: no direction) |
| 0001292814-26-005066 | EMBJ | 6-K | an ADS ratio change 1:4 -> 1:1, "three additional ADSs for every ADS held" (no split word in that sentence); trading 2026-11-16 (read: no ratio) |
| 0001279569-26-001064 | DFSC | 6-K | "a consolidation of its common shares on a 9-to-1 basis", effective 12:01 a.m. / trading 2026-11-16 (read: no ratio) |
| 0001279569-26-001077 | DFSC | 6-K | the completion of the same 9-to-1 consolidation, trading 2026-11-16 (read: no ratio) |
| 0001213900-26-110501 | POM | 6-K | terminates its ADR program: ordinary shares consolidate 10 -> 1 while "each holder of ten (10) ADSs will receive three (3)" shares (read: 10 -> 1; now abstains, ADS termination) |

The known past splits (2026-11-25, W2-7) added three full submissions, fetched and trimmed the same way (the SGML
header plus the documents the extractor reads, each kept whole and byte-exact; for the 10-Q, its own document,
which the wide reading keeps; every reading of each trimmed file, on the pre-change and the changed extractor, with
and without the company's confirmed splits, CHECKED identical to its full submission's, and the 10-Q's to its
standalone primary document's). Each restates a split prod's list already held CONFIRMED (the steward review's read
of 2026-11-25); on the pre-change extractor each reproduced the reading prod's list stored
(`docs/temp/platform-fixes-w0-w2-plan-2026-11-25.md` §2). PBM's interim 6-K `0001213900-26-122017` (above, W2-5) is
the fourth.

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001493152-26-048139 | GCTK | 8-K (1.01, 2.03, 3.02, 7.01, 9.01) | a 1-for-2, trading 2026-10-26, beside "On August 28, 2026, the Company implemented a 1-for-15 reverse stock split" — the listed 1-for-15 of 2026-08-31 (read: two ratios, abstain) |
| 0001214659-24-019590 | GPUS | 8-K (7.01, 9.01) | a special dividend of preferred stock (record date 2024-12-13); "… the one-for-thirty-five reserve stock split that was effectuated on November 25, 2024" — the listed 1-for-35, the day before (read: no direction, abstain) |
| 0001437749-26-036465 | TRT | 10-Q | "On January 5, 2026, the Company effected a two-for-one forward stock split", in footnotes beside current dates — the listed 2-for-1 (read: 1 -> 2, no date; since 2026-11-26 a past split's ratio only: the filing dates nothing currently) |

The date sentences the long-window check misread (2026-11-26, split-reader misreads PR-A) added three full
submissions, copied from the evidence folder of prod's SEC cache (`docs/temp/split-reader-misreads-2026-11-26/forms/`)
and trimmed the same way (the SGML header plus the documents the extractor reads, each kept whole and byte-exact;
every reading of each trimmed file — bare, catch-all, screened, `wide` both ways, with and without the company's
confirmed splits — CHECKED identical to its full submission's, on the pre-change and the changed extractor). On the
pre-change extractor each reproduced the reading prod's 800-day check planned
(`docs/temp/split-reader-misreads-2026-11-26/classification.md`):

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001628280-26-048496 | CYCU | 8-K (3.01, 7.01, 9.01) | a delisting notice: "Because the Company effected a 1-for-30 reverse stock split on October 27, 2025, … trading … is expected to be suspended at the opening of business on July 21, 2026" (read: 1-for-30, first session 2026-07-21; since PR-B a past split's ratio only — its own clause dates it 262 days back — the trimmed file re-checked identical to the full submission on every reading path) |
| 0001193125-25-000195 | VRM | 8-K (7.01) | a 1-for-5 effective at the end of a Chapter 11 case (no date) beside "trading of the Common Stock formally suspended at the opening of business on December 2, 2024" (read: first session 2024-12-02) |
| 0001641172-25-007892 | SKYA | 8-K (5.03, 9.01) | a 1-for-300 "effective at 11:59 p.m., Eastern Time, on April 27, 2025 and began trading on a post-split basis" — a Sunday (read: first session 2025-04-27) |

A stated ratio that is not the listed security's (2026-11-26, split-reader misreads PR-C) added six full submissions,
copied from the same evidence folder and trimmed the same way (each kept document whole and byte-exact; every reading
of each trimmed file — bare, catch-all, screened, `wide` both ways, with and without the company's confirmed splits —
CHECKED identical to its full submission's, on the pre-change and the changed extractor). On the pre-change extractor
each reproduced the reading prod's 800-day check planned (`classification.md`, R1 / R5 / R6):

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0000950170-24-120712 | OPRA | 6-K | an AGM notice: "every two (2) … ordinary shares … consolidated into one (1)" beside "a change in the ADS ratio … from one (1) ADS representing two (2) ordinary shares to … one (1) ADS representing one (1) ordinary share" — the ADS keeps its price (read: 2 -> 1, legal 2024-12-06; now abstains, ADS ratio change) |
| 0001213900-25-088581 | MOB | 6-K | a meeting notice: the ordinary shares consolidated "on a 275 to 1 basis", and "each ADS will represent one Share, instead of … (275) pre-Consolidation Shares" (read: 275 -> 1, legal 2025-10-10; now abstains, ADS ratio change) |
| 0001292814-26-002268 | AZUL | 6-K | a 150,000-to-1 reverse split of the B3 common shares; the ADS from 500,000 old shares to two new, holders "receive 0.666666 of a new ADS for every one (1) existing ADS" (read: 1 -> 0.666666, trading 2026-04-20; now abstains, ADS ratio change) |
| 0001193125-25-269718 | BETA | 8-K (1.01, 3.02, 3.03, 5.02, 5.03, 8.01, 9.01) | "in connection with the IPO": "(iv) a 6.3811681-for-1 forward split of the Company's capital stock … the 'IPO Recapitalization'" (read: 1 -> 6.3811681, legal 2025-11-04; now abstains, pre-listing) |
| 0001628280-26-044474 | FRVO | 8-K (2.02, 7.01, 9.01) | an earnings release: "the 0.7194 -for-1 reverse stock split effected on May 14, 2026 in connection with the Company's IPO" (read: 1 -> 0.7194, legal 2026-05-14; now abstains, pre-listing) |
| 0001171843-26-002879 | ANTA | 6-K | "Aurelion, Inc. (Nasdaq: AURE), a publicly traded subsidiary of Antalpha …, issued a press release …, incorporated by reference as Exhibit 99.1" — Aurelion's "1-for-10 share consolidation effective February 19, 2026" (read: 10 -> 1, legal 2026-02-19; now no ratio, the exhibit unread) |

PR-A's replay over prod's whole split corpus (2026-11-27) added four more, from the review copies of prod's SEC
cache (`docs/temp/split-reader-misreads-evidence/prod-a/forms/`), trimmed and checked the same way. Each is a split
prod lists whose reading PR-A's first commit lost; each reads as before again:

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001493152-25-020018 | CJET | 6-K | a 1-for-100 "on November 3, 2025"; the shares "will trade … on a consolidation-adjusted basis" from "the opening of trading on November 3, 2025" (read: trading 2025-11-03) |
| 0001178913-25-000145 | BLRX | 6-K | an ADS ratio change, "the same effect as a 1-for-40 reverse stock split of the ADSs"; "The new ADS to ordinary share ratio … will be effective prior to the commencement of trading … on Thursday, January 30, 2025" (read: trading 2025-01-30) |
| 0001171843-26-000847 | BWAY | 6-K | one additional ADS for each ADS held; "The new 1-to-1 ADS to ordinary share ratio will take effect prior to the commencement of trading … on Tuesday, March 3, 2026" (read: 1 -> 2, trading 2026-03-03) |
| 0001493152-26-030305 | XAIR | 8-K (2.02, 5.03, 7.01, 9.01) | "the Board approved a 1-for-20 reverse stock split, which is expected to enable the Company to regain compliance" (its 2026 split) beside its 2025 one-for-twenty (read knowing the 2025 one: 20 -> 1, no date) |

The PR-C review (2026-11-27: prod's split corpus replayed on the merged stack) added two full submissions, copied
from prod's cache (`docs/temp/split-reader-misreads-evidence/prod-stack/forms/`) and trimmed the same way (every
reading of each trimmed file CHECKED identical to its full submission's, on the pre-change and the changed
extractor). Each is a split prod lists, abstained by PR-C's ADS rule:

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001178913-25-000177 | BLRX | 6-K | an investor letter: "change the ratio of our American Depositary Shares to ordinary shares, from 15 ordinary shares per ADS to 600 ordinary shares per ADS. This is equivalent to a 1-for-40 reverse stock split" — the equating sentence is the ADS change itself (read: ADS ratio change abstain; now 40 -> 1, legal 2025-01-30: "will go into effect on" is a legal cue since 2026-11-27) |
| 0001213900-26-051029 | TDTH | 6-K | the ADS ratio "from … one (1) ADS to eight (8) Class B ordinary shares to … one (1) ADS to two hundred and forty (240)" beside "Effective April 24, 2026, the Company effected a 1-for-30 reverse share split" — the ADS change's own factor (30) is the split's (read: ADS ratio change abstain; now 30 -> 1, legal 2026-04-24) |

The call-off reader (2026-11-28, framework completion F10 P4) added one full submission, copied from prod's EDGAR cache
(`docs/temp/framework-completion-evidence/f10-measure/forms/`; no SEC fetch) and trimmed the same way (the SGML header
plus the 8-K and its EX-99.1, each kept whole and byte-exact; every reading of the trimmed file, bare, catch-all and
screened, `wide` both ways, CHECKED identical to the full submission's on the pre-change and the changed reader). On
the pre-change reader it reproduced the reading prod's split list met (the same folder's report):

| accession | filer | form | what it states / why it is here |
|---|---|---|---|
| 0001213900-26-122096 | AIRI | 8-K (8.01, 9.01) | "announced that it would not be effecting the reverse stock split … at a ratio of 1-for-5" (8-K and EX-99.1), the day before the listed 1-for-5's first session (read: 5 -> 1, no date; now a call-off: abstains, `called off: decision (5->1 stated)`) |

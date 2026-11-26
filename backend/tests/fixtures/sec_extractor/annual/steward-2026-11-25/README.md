# Real 20-F fixtures: the steward review of 2026-11-25 (W1-3)

These back the W1-3 tests at the end of `backend/tests/ingest/test_annual_runway.py` and
`backend/tests/ingest/test_annual_shares.py`. Each document is the real filing, read from the steward review's cache
(`docs/temp/steward-review-2026-11-25/sec_cache`, fetched through its `sec.py`), cut down; companyfacts and
submissions are the real JSON (data.sec.gov, read 2026-11-25/26), cut down, every kept row verbatim (`_fixture` key).
Each cut was checked to give the same candidate as its full document with the full companyfacts (values, dates,
flags, note, passages except offsets), on the code before and after W1-3.

| Files | Company, filing | What it pins |
|---|---|---|
| `ASND-20f-fin.txt`, `cf-ASND.json` | Ascendis Pharma, 20-F for 2025, accession 0001193125-26-046590 | "Cash and cash equivalents 17, 18 616,041 559,543" under "(EUR’000)": a note-reference pair, never the cash (was 18.0) |
| `ARM-20f-fin.txt`, `cf-ARM.json`, `subs-ARM.json` | Arm Holdings, 20-F for the year to 2026-03-31, accession 0001973239-26-000097; companyfacts' fresher rows from the Q2 6-K 0001973239-26-000142 (filed 2026-11-04) | the candidate cites the 6-K; its quarter is 1,109 - 902 (six months less three, USD millions), never the six months pro-rated |
| `ZENA-20f-fin.txt`, `cf-ZENA.json` | ZenaTech, 20-F for 2025, accession 0001376474-26-000319 | an own-line current "Marketable securities 3 9,093,887 -" row counts like short-term investments (CAD) |
| `NVO-20f-cover.htm`, `cf-NVO.json` | Novo Nordisk, 20-F for 2025, accession 0000353278-26-000012 | A and B shares tagged per class on the IFRS class axis: their sum 4,465,000,000, still `multi-value-cover` |
| `AZN-20f-cover.htm`, `cf-AZN.json` | AstraZeneca, 20-F for 2025, accession 0001104659-26-019130 | 50,000 redeemable preference shares tagged beside the ordinary shares: never summed |
| `KEP-20f-cover.htm`, `cf-KEP.json` | Korea Electric Power, 20-F for 2025, accession 0001193125-26-190113 | one undimensioned count beside a printed par value ("Won 5,000"): unchanged |

## How they were cut

- **Statements** (`*-20f-fin.txt`): the cleaned text of the full 20-F, cut to windows around every balance-sheet cash
  site and every operating-cash-flow site (300 characters before the statement heading through past the row) and the
  filing's presentation-currency phrases, joined by `[… trimmed …]`. companyfacts cut to the cash, operating cash
  flow and short-term investment concepts the runway path reads, rows ending on or after 2024-01-01.
- **Covers** (`*-20f-cover.htm`): the raw document cut to its head, the `ix:header` contexts the kept facts cite, the
  cover page from the end of `ix:header` past the last cover-share fact, and the blocks holding each ADS-ratio
  statement the reader finds in the full text. companyfacts cut to the `dei` taxonomy.
- `subs-ARM.json`: the 20-F and 6-K rows filed from 2026-05-01.

The scripts and the check output are in `docs/temp/platform-fixes-evidence/w1-3/` (`cut_fixtures.py`,
`trim_check.py`, `trim_check_old.txt`, `trim_check_new.txt`; gitignored).

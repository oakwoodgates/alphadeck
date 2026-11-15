# Reconstructed fixtures: the 2026-11-15 annual-filer audit cases

These fixtures back `backend/tests/ingest/test_annual_statement_checks.py` (the extractor correctness plan's PR 4:
annual currency, table and line selection, and the companyfacts cross-check).

**They are RECONSTRUCTED, not copied from EDGAR.** The builder could not read the fork's EDGAR cache, so each
fixture was rebuilt from the values and wording recorded in `docs/temp/v2-extractor-audits-2026-11-15/
annual-filers-AUDIT.md`, its `cash_sample.txt` and the build plan (`docs/temp/v2-extractor-correctness-plan-
2026-11-15.md`, findings 6 to 9 and question 6). They are shaped like the real trimmed statement text beside them
(`../*-fin.txt`, `../cf-runway-*.json`). Every fixture was checked to reproduce, under the extractor as it stood
before PR 4, the wrong (or right) values the audit recorded, so the tests fail before the change for the audit's
own reasons.

## What is recorded and what is filler

| Case | Recorded (audit or plan) | Reconstructed filler |
|---|---|---|
| NTES | cash RMB 47,167,904K, USD column 6,744,921K, OCF RMB 50,739,800K; companyfacts us-gaap CNY and USD units | the USD OCF 7,255,695K (audit: "7.26B"), prior-year columns, other rows, header wording |
| JCSE | cash S$9,931K, OCF S$2,686K, USD column "7.72M" and "2.09M"; companyfacts SGD unit | 7,720 / 2,088 (rounded to the audit's figures), prior years, other rows |
| DNN | cash 465,918K, OCF -68,148K; "Canadian dollars" on the balance sheet, "CAD dollars" on the cash-flow header; companyfacts CAD unit and a USD unit holding 12,380,000 for 2017 | prior years, other rows |
| EH | old read 257,338K (a breakdown column) and OCF -179,506K yuan (USD -25.7M); consolidated RMB 256.4M | the breakdown table's layout and its other columns, USD column 36,665 / 25,670, prior years, other rows. companyfacts' 256,400,000 is exact only to the recorded precision |
| ZEPP | parent column 137K, consolidated 57,046K, OCF -25,692K; companyfacts us-gaap USD | the breakdown's other columns (chosen to sum to 57,046), prior years, other rows |
| TM | segment ("Non-Financial Services Businesses") cash ¥9,885,097M, consolidated ¥12,659,622M, OCF ¥5,472,920M, blank currency; companyfacts JPY only, ending 2025-03-31 with OCF ¥3,696,934M | the segment table's other rows, the 2025-03-31 cash ¥8,982,404M, prior years |
| QGEN | the lease line "Cash paid for operating leases included in cash flows from operating activities $ 31,741"; old currency EUR; cash empty; companyfacts USD cash 839,005K and OCF 654,326K | WHY the old cash was empty (here: an MD&A "Effect of exchange rate changes on cash and cash equivalents" row read as a negative balance), the euro mention, prior years, other rows |
| BLRX | cash 3,250K, OCF -8,074K, both agreeing with companyfacts; short-term deposits 17.6M left out | the deposits row label and value 17,600K, its companyfacts concept, prior years, other rows |
| PLSM | ILS cash 21,604K, OCF -32,876K, the shekel column chosen over the dollar column; short-term deposits 47.5M left out | the deposits row 47,500K and its companyfacts concept, the USD column values, prior years, other rows |
| SNY | EUR cash 7,657M, OCF 10,750M, companyfacts agreeing | the statement layout (the real cash row's note reference is dropped), prior years, other rows |

Accession numbers and filed dates in the `cf-*.json` files are placeholders unless the audit recorded them (the
filing URLs in `cash_sample.txt` give the 2025 accessions). Each `cf-*.json` carries a `_fixture` key saying the
same; the extractor ignores it.

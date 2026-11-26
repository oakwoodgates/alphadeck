# Inline-XBRL cash fixtures (cut-down real 10-Q documents)

Read by `backend/tests/ingest/test_extract_inline_cash.py` (and one unit test in `test_inline_xbrl.py`). Each
`<TICKER>-10q.htm` is the cited primary document of a cash candidate that waited for the steward on 2026-11-25, read
from SEC EDGAR through the steward review's paced, cached reader and cut down. Nothing in a kept part is edited,
rounded or rescaled:

- the document head up to `ix:header` (the `html` root with its namespace declarations);
- `ix:resources` holding only the `xbrli:context` and `xbrli:unit` elements the kept facts cite (no `ix:hidden`);
- every table row (outside a table, the enclosing `div`) holding an `ix:nonFraction` fact of a concept the periodic
  cash reader reads, or of `CashAndCashEquivalentsFairValueDisclosure`, `DebtSecuritiesCurrent` or
  `DebtSecuritiesNoncurrent`, when its context is undimensioned or on `dei:LegalEntityAxis` alone (another
  registrant of a combined filing: kept to prove it is never read); a cash-equivalents fair value on any axis.

`<TICKER>-cf.json` is the company's real companyfacts (data.sec.gov, read 2026-11-25/26), cut to the us-gaap concepts
the periodic cash reader reads (cash, investments, operating cash flow, the `IncreaseDecreaseIn*` lines) and to rows
ending in 2025 or later plus each unit's newest. Every kept row is verbatim. On the code before this reader, the full
document and companyfacts reproduce each waiting candidate's tier, cash, burn, date and flags, and the cut ones give
the same candidate (values, flags, note; the located passages differ: the cut drops the text they first hit).

| Ticker | Accession | Filed | Period | What it pins |
|---|---|---|---|---|
| CEG | 0001868275-26-000119 | 2026-11-09 | 2026-09-30 | companyfacts one quarter behind (Q2): the document's cash and nine months, the quarter derived with companyfacts' six months |
| DUK | 0001326160-26-000051 | 2026-11-05 | 2026-09-30 | two quarters behind (Q1): offered with `burn-span`; eight registrants, the parent's facts only; a lone debt total quoted |
| FE | 0001031296-26-000144 | 2026-10-28 | 2026-09-30 | two quarters behind; long-term investments named, not counted |
| AMGN | 0000318154-26-000155 | 2026-11-04 | 2026-09-30 | a lone debt total equal to the same-dated cash-equivalents fair value (on `FinancialInstrumentAxis`): named in the note |
| NVDA | 0001045810-26-000075 | 2026-08-26 | 2026-07-26 | `DebtSecuritiesCurrent` 34,143M, a tag the composition does not count: named in the note |

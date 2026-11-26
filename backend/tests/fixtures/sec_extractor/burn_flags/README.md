# Burn flag fixtures (cut-down real documents)

Read by `backend/tests/ingest/test_extract_burn_flags.py`. Each `<TICKER>-10q.htm` (FUBO: `-10k.htm`) is the cited
primary document of a cash candidate that waited for the steward on 2026-11-25, read from SEC EDGAR through the steward
review's paced, cached reader and cut down. Nothing in a kept part is edited, rounded or rescaled:

- the document head up to the `body` tag (the `html` root with its namespace declarations);
- a hidden `ix:header` whose `ix:resources` hold only the `xbrli:context` and `xbrli:unit` elements the kept facts cite
  (FEIM's own header sits at the end of its body; no `ix:hidden`);
- every table row (outside a table, the enclosing paragraph, else `div`) holding an undimensioned `ix:nonFraction` fact
  of a concept the periodic cash reader reads or of a reconciliation line (`IncreaseDecreaseIn*`), in document order.

`<TICKER>-cf.json` is the company's real companyfacts (data.sec.gov, read 2026-11-25/26), cut to the us-gaap concepts
the periodic cash reader reads (cash, investments, operating cash flow) plus every `IncreaseDecreaseIn*` line, and to
rows ending in 2025 or later plus each unit's newest. Every kept row is verbatim. On the code before W1-4 the full
document and companyfacts reproduce each waiting candidate's tier, cash, burn, date and flags, and the cut ones give the
same candidate (values, flags, note; the located passages differ only where the cut drops text).

| Ticker | Accession | Filed | Period | What it pins |
|---|---|---|---|---|
| FEIM | 0001185185-26-003983 | 2026-09-14 | 2026-07-31 | `possible-one-time` on two lines: "Net changes in operating assets and liabilities (4,102)" (`IncreaseDecreaseInOperatingCapital`) and a $4.0 million contract-liabilities sentence tagged `IncreaseDecreaseInCommodityContractAssetsAndLiabilities`, against 2.6M of operating cash flow |
| COHR | 0000820318-26-000023 | 2026-11-04 | 2026-09-30 | `possible-one-time` on "Accrued compensation and benefits (90,196)" (`IncreaseDecreaseInEmployeeRelatedLiabilities`), not the larger routine lines beside it |
| FUBO | 0001628280-26-074590 | 2026-11-18 | 2026-09-30 | a 10-K whose year starts 2025-09-28, three days before its 10-Qs' (2025-10-01): the quarter derived across filings, `ytd-derived-across-filings` |
| AMSC | 0001437749-26-034936 | 2026-11-04 | 2026-09-30 | six months (April-September) tagged from 2026-01-01, 90 days off its first quarter's start: stays `ytd-raw` |
| KULR | 0001104659-26-124824 | 2026-11-10 | 2026-09-30 | nine months under the continuing-operations tag, earlier quarters under the plain one: stays `ytd-raw` (one tag only) |

# Cash fixtures for the composition guards (cut-down real 10-Q/10-K documents)

Read by `backend/tests/ingest/test_extract_cash_guards.py`. Each `.htm` is a cited primary document read from SEC EDGAR
through the steward review's paced, cached reader and cut down: the document head, the contexts and units the kept
facts cite, `dei:DocumentPeriodEndDate`, and every table row holding an undimensioned (or `LegalEntityAxis`-only) fact
of a concept the periodic cash reader reads, an equity-securities balance, a balance-sheet total (`Assets`,
`AssetsCurrent`, `AssetsNoncurrent`, `LiabilitiesAndStockholdersEquity`), an `IncreaseDecreaseIn*` line or a deferred-tax
line. Unlike `../inline_cash/` and `../auto_cash/`, **each kept row stays in its own source table**: rows of one source
`<table>` share one `<table>`, rows of different tables never do (the statement guard reads table membership). Nothing in
a kept part is edited, rounded or rescaled.

Each `-cf.json` is the company's real companyfacts (data.sec.gov, read 2026-11-26), cut to those concepts and to rows
ending in 2025 or later plus each unit's newest; every row is verbatim. On the code before the guards, each document
and companyfacts give the candidate the fork applied (and the same as the full document, old and new code).

| File | Accession | Filed | Period | What it pins |
|---|---|---|---|---|
| CNP-10q.htm | 0001130310-26-000048 | 2026-10-27 | 2026-09-30 | companyfacts two quarters behind; "Investment in equity securities" 425M tagged `MarketableSecuritiesCurrent` on the balance sheet and `EquitySecuritiesFvNiCurrentAndNoncurrent` in a note: cash 132M, not 557M |
| CNP-q2-10q.htm | 0001130310-26-000041 | 2026-07-28 | 2026-06-30 | CNP's previous 10-Q (six months 1,060M): the quarter is 991M generated; applied |
| LEXX-10k.htm | 0001640334-26-001880 | 2026-11-25 | 2026-08-31 | `MarketableSecuritiesNoncurrent` 349,120 only on the income-tax note's deferred-tax row: 4,737,396 offered, flagged `investments-unconfirmed` (was 5,086,516 applied) |
| XNCR-10q.htm | 0001326732-26-000063 | 2026-11-04 | 2026-09-30 | balance-sheet `DebtSecuritiesCurrent` 318,487K and `DebtSecuritiesNoncurrent` 177,581K stay counted: 555,690K |
| NVDA-10q.htm | 0001045810-26-000075 | 2026-08-26 | 2026-07-26 | balance-sheet "Marketable debt securities" 34,143M (`DebtSecuritiesCurrent`) stays counted: 56,586M |

# Cash fixtures for the automatic companyfacts-behind case (cut-down real 10-Q documents)

Read by `backend/tests/ingest/test_extract_auto_cases.py`. Each `.htm` is a cited 10-Q primary document read from SEC
EDGAR through the steward review's paced, cached reader and cut down the way `../inline_cash/` is, plus every table
row holding an undimensioned `IncreaseDecreaseIn*` reconciliation line (the one-time check's). Nothing in a kept part
is edited, rounded or rescaled.

`NEE-cf.json` is NEE's real companyfacts (data.sec.gov, read 2026-11-26), cut like `../inline_cash/`'s (the periodic
cash reader's us-gaap concepts and every `IncreaseDecreaseIn*`, rows ending in 2025 or later plus each unit's newest).
CEG's companyfacts is `../inline_cash/CEG-cf.json`. On the code before this case, each document and companyfacts give
the waiting candidate's tier, cash, burn, date and flags.

| File | Accession | Filed | Period | What it pins |
|---|---|---|---|---|
| CEG-10q.htm | 0001868275-26-000119 | 2026-11-09 | 2026-09-30 | companyfacts one quarter behind: cash $603M, quarter derived with companyfacts' six months ($3,426M generated), six reconciliation lines; applied |
| NEE-10q.htm | 0000753308-26-000081 | 2026-10-21 | 2026-09-30 | companyfacts two quarters behind: cash $5,579M, nine months $12,322M |
| NEE-q2-10q.htm | 0000753308-26-000060 | 2026-07-24 | 2026-06-30 | NEE's previous 10-Q: six months $7,276M, so the quarter is $5,046M generated; applied |

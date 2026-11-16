# Reconstructed fixtures: the 2026-11-16 THIRD annual cash re-audit cases

These fixtures back `backend/tests/ingest/test_annual_reaudit3.py`.

**They are RECONSTRUCTED, not copied from EDGAR.** The builder could not read any EDGAR cache, so each fixture
was rebuilt from the values and row wording recorded in `docs/temp/v2-extractor-audits-2026-11-15/
reaudit3-annual-cash-2026-11-16.md` (its part 1 diff, part 4 rows and the regressions section). Each was checked
to reproduce, under the extractor as it stood before the change it backs, the EXACT stored candidate (cash,
investment part, quarterly burn, currency and flags), so the tests fail before the change for the re-audit's
own reasons.

## What is recorded and what is filler

| Case | Recorded (re-audit, stored candidate) | Reconstructed filler |
|---|---|---|
| VNET | RMB thousands with a US$ convenience column; balance-sheet cash 5,523,571 and a short-term investment row whose RMB value is 379,198 and whose US$ value is 54,225; the stored candidate CNY 5,577,796,000 with investment part 54,225,000, OCF 1,918,550,000 over 364 days, flags `annual-statements` only, and a note accepting a USD companyfacts match for the CNY investment part (here us-gaap `ShortTermInvestments` 54,225,000 USD on 2025-12-31); the filing URL's accession 0001104659-26-044361 | WHY the US$ figure was read (here: the investments row prints only two figures, its 2024 cell blank, under the three-column header "2024 2025 2025 RMB RMB US$"; the re-audit did not record the row's real shape), the cash row's 2024 and US$ figures, the companyfacts CNY cash and OCF rows (INFERRED: no `convenience-column` flag means companyfacts confirmed CNY, and the 364-day span means an exact companyfacts duration row), every other row, prior years, the filed date |
| ALC | USD millions; balance-sheet cash 1,527 and a separate "Time deposits" row printing note 17, then 80 (2025) and 153 (2024); OCF 2,271 over 364 days; the stored third-run candidate USD 1,527,000,000, no investment part, flags `annual-statements` and `investments-unconfirmed`, with the false "current-year cell is blank" reason; the second run's 1,607,000,000 with 80,000,000 (this fixture reproduces BOTH under the extractor before and after the deposits guard); the filing URL's accession 0001167379-26-000014 | WHY the guard called the cell blank (here: the heading "CONSOLIDATED BALANCE SHEET at December 31, 2025 and 2024" above the "Note 2025 2024" column header, so four year tokens; the re-audit did not record the real heading), the cash row printed without a note reference, the cash row's 2024 figure, the row order, every other row, the ifrs-full USD cash and OCF rows (INFERRED: no `figures-disagree`, a 364-day span), the filed date |

Accession numbers and filed dates in the `cf-*.json` files are placeholders unless the re-audit recorded them.
Each `cf-*.json` carries a `_fixture` key saying the same; the extractor ignores it.

IONR (the re-audit's other deposits-guard regression: cash assets 17,863 = bank 10,843 + deposits 7,020, USD
thousands, December 2025; the stored candidate moved from the right 17,863,000 to a wrong 11,513,000 and gained
`figures-disagree`) has NO fixture: the re-audit does not record what the 11,513 is or which row and column it
came from, so its cause could not be established from the evidence and the code, and it was left as is.

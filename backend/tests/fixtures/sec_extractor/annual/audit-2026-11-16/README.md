# Reconstructed fixtures: the 2026-11-16 annual cash re-audit cases

These fixtures back `backend/tests/ingest/test_annual_deposits_guard.py` (the deposits/investments guard, the
closing-balance rule, the table-header year column, currency prose and the NTES false alarm).

**They are RECONSTRUCTED, not copied from EDGAR.** The builder could not read the fork's EDGAR cache, so each
fixture was rebuilt from the values and row wording recorded in `docs/temp/v2-extractor-audits-2026-11-15/
reaudit-annual-and-shares-2026-11-16.md` (its sample rows, the "Material residuals" and the appendix of stored
candidate notes) and the parent's confirmation of the stored candidates. They are shaped like the 2026-11-15
fixtures beside them (`../audit-2026-11-15/`). Every fixture was checked to reproduce, under the extractor as it
stood before this change, the EXACT stored candidate (cash, investment part, operating cash flow, currency and
flags), so the tests fail before the change for the re-audit's own reasons.

## What is recorded and what is filler

| Case | Recorded (re-audit, stored candidate) | Reconstructed filler |
|---|---|---|
| QGEN | balance-sheet cash 839,005 and short-term investments 259,913 (USD thousands); the cash-composition note (Note 3) whose "deposits 54,653" are inside cash 839,005; the note's heading text "balance sheet at fair value. Changes in fair values of derivatives"; OCF 654,326; companyfacts USD cash, restricted-cash total, OCF and TimeDepositsAtCarryingValue 54,653,000; the stored candidate 893,658,000 | WHY the balance sheet's own cash row lost before (here: it prints its note reference as "(3)", which the old reader took as a negative first column; the re-audit did not record the real cause), the note's "Cash at bank and on hand 784,352" line, prior years, other rows, the filed date |
| CGNT | cash 116,878 (2026-01-31, USD thousands) agreeing with companyfacts; OCF 40,331; the added 113,100 is LAST year's total of cash, restricted cash and deposits from a summary line; the MD&A heading text "balance sheet arrangements that we believe have or are reasonably" and "Statements of Cash Flows”. Based on past performance and current"; the balance sheet has no current investment row; companyfacts has no time-deposit row | the summary line's exact wording and layout (an oldest-first "January 31, 2025 2026" header with a dash in the 2026 cell — the shape that makes the old reader take 113,100 for 2026), prior-year columns, the F-pages statements' other rows, the filed date |
| WAVE | USD thousands; cash 6,022 agreeing with companyfacts; the deposits row "note 5, 2025 a dash, 2024 1,254"; OCF columns 2025 / 2024 / 2023 = (3,040) / (1,817) / (2,601) with the old pick the 2023 column; the headings "STATEMENTS OF FINANCIAL POSITION December 31 Note 2025 2024 in USD" and "STATEMENTS OF CASH FLOWS Year ended December 31 2025 2024 2023 in USD"; companyfacts' lagging -2,462,000 for 2022 | WHY the old reader picked 2023 (here: a row label above the total, "Share-based payment (2024 and 2025 plans)", carries year tokens; the re-audit did not record the real cause), the cash row's note "4", prior-year cash 2,400, other rows, accessions |
| ARQQ | USD thousands; the stored cash 18,264 is the net increase in cash; the closing row "Cash and cash equivalents at end of period" reads 36,978 / 18,705 / 44,455; OCF (29,553); the filing says it is presented in United States Dollars (USD 000); the heading "Statement of Financial Position As at 30 September 2025"; companyfacts' lagging -12,476,000 for 2024-10-01 to 2025-03-31 | WHY the balance sheet's own cash row was never located (here: it reads "Cash at bank", which the cash label does not match), the net-increase line's "/(decrease)" wording (the shape that slips past the old increase-in veto), the other cash-flow rows and prior years (chosen to reconcile: 18,705 + 18,264 + 9 = 36,978), the taxonomy and accession |
| NTES | RMB thousands: cash 47,167,904, current time deposits 92,639,378, short-term investments 22,803,503, OCF 50,739,800; companyfacts us-gaap ShortTermInvestments 22,803,503,000 (CNY) and 3,260,858,000 (USD) on 2025-12-31; the stored false `figures-disagree` on "Time deposits" | the USD convenience-column value of the time deposits (13,247,365), prior years, other rows; the cash and OCF rows and companyfacts are the 2026-11-15 NTES fixture's |

Accession numbers and filed dates in the `cf-*.json` files are placeholders unless the re-audit recorded them
(the filing URLs in the re-audit give the accessions). Each `cf-*.json` carries a `_fixture` key saying the same;
the extractor ignores it.

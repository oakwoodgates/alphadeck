# Insider Buying

`insider-tracker` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Every open-market insider purchase (a Form 4 with a code-P line) across the counted cards' names in a 30 or 90 day
window, one row per Form 4: filer, role, size, cluster, whether it became a trigger, the return since against IWM,
approximate cap. It lists and never aggregates; outcome statistics belong to the Track Record (rule 6). Tape look
([Looks](../DASHBOARDS.md#looks)), the cluster word its one accent.

## Who counts

- Counting names of the cards counted at the as-of; Context and Bench join behind a toggle, tagged.
- A Form 4 the reader did not fold as routine is always a row; a sales-only filing is counted, not listed.
- A filing naming no filer joins no cluster; a flagged or unpriced name keeps its row.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/insider_tracker.py` (`purchase_of`) |
| Route | `GET /dashboards/insider-tracker` (`days`, `silent`) |
| Shared readers | `baskets`, `filings`, `series`, `nights`, `market_cap` |
| Cache key | `(window, silent, clock)`; grouping, filters and sorts are the page's |
| Frontend | `frontend/src/dashboards/insider-tracker/` (`readsRecord`) |
| Tests | `backend/tests/dashboards/test_insider_tracker.py`, frontend tests |

## As-of behavior

The window ends at the as-of, by filing date on the public clock; a later filing is not a row. Bars end at the as-of;
the trigger match reads nights recorded by then. Cap is as of the as-of, a filter only.

## Rules not visible in code

1. A row is dated by its filing date, never the trade date.
2. "Became a trigger" matches the filing's accession on every recorded night in the window, not only the card in
   effect. Why: a flip insider trigger lives fewer days (`insider_flip_alpha_liveness_days`) than the window. "Not a
   trigger" says nothing about why; the detector's screens are not re-derived.
3. `$ size` sums the code-P lines, not the reader's net `usd`. Why: a filing with sales would understate the purchase.
4. A cluster is two or more distinct buyers on a name within `CLUSTER_WINDOW_DAYS` of the row's filing date,
   inclusive; "and N more" co-filers are not buyers.
5. The page reads back the reader's own words (`FilingRow` has no purchase fields); a round-trip test fails loud on a
   rewording.
6. The reader is narrowed to `FORM4_KINDS`. Why: the page shows no other kind and a full read was far slower.

## Known limits

- Code P only: option exercises, grants and conversions are not purchases here.
- Foreign private issuers file no Form 4: ADR names are absent, not clean.

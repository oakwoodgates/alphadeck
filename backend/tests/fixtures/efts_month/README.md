# Term-mentions fixtures — SEC EDGAR full-text search replies, real, trimmed

Every file here is a real reply from SEC EDGAR full-text search (`https://efts.sec.gov/LATEST/search-index`).
Nothing is hand-written and no value is edited. The only change is TRIMMING to the fields
`pipeline.term_mentions.parse_month` and its tests read: `hits.total`; each hit's `_source.ciks` and
`_source.file_date`; the `form_filter` and `entity_filter` aggregations whole (buckets, `sum_other_doc_count`,
`doc_count_error_upper_bound`); and, for the two date-ranged replies, SEC's echo of the query it ran
(`query.query`, which shows the `file_date` range filter).

Recorded LIVE on 2026-10-29 (two requests, through the job's own `month_url` and `EdgarClient` with the declared
User-Agent) — the date-ranged request the job makes:

- `psilocybin_2026-09.json` — `q="psilocybin"&dateRange=custom&startdt=2026-09-01&enddt=2026-09-30`. 14
  documents (`relation: eq`), all on the first page, 6 filers: the EXACT case (every hit's `file_date` falls in
  September 2026).
- `artificial_intelligence_2026-08.json` — `q="artificial intelligence"&dateRange=custom&startdt=2026-08-01&enddt=2026-08-31`.
  5,706 documents (`relation: eq`, not capped), 100 on the first page, `entity_filter.sum_other_doc_count`
  5,211: the SAMPLED case ("at least" 80 filers).

From the discovery walk's disk cache (`data/edgar_cache/efts/`, an all-time query with no date range — the same
reply shape), because no live month came back capped:

- `therapy_all_time.json` — `therapy_0.json`, `q="therapy"&from=0` (cached 2026-06-25). `hits.total` is
  `{"value": 10000, "relation": "gte"}` (capped): the aggregation-total case.
- `classic_psychedelics_all_time.json` — `classic_psychedelics_0.json`, `q="classic psychedelics"&from=0`
  (cached 2026-06-24). 106 documents, 100 on the page, but `entity_filter` lists every filer
  (`sum_other_doc_count: 0`, each key with its CIK): the exact-by-entity case (9 filers; the page alone shows 8).

# Inline-XBRL share-count fixtures (cut-down real 10-Q documents)

Read by `backend/tests/ingest/test_inline_xbrl.py` and `backend/tests/ingest/test_extract_inline_shares.py`. Each
`<TICKER>-10q.htm` is the cited primary document of a share-count candidate that waited for the steward on
2026-11-25, read from SEC EDGAR through the steward review's paced, cached reader and cut down. Nothing in a kept
part is edited, rounded or rescaled:

- the document head up to `ix:header` (the `html` root with its namespace declarations);
- `ix:resources` holding only the `xbrli:context` and `xbrli:unit` elements the kept facts cite (no `ix:hidden`);
- the cover page, from the end of `ix:header` through the block holding the last
  `dei:EntityCommonStockSharesOutstanding` fact and on until 300 characters of text follow it (the located cover
  passage's tail);
- every table row holding a `us-gaap:WeightedAverageNumberOfSharesOutstandingBasic` fact (the EPS share base),
  appended in document order.

`<TICKER>-cf.json` is the company's real companyfacts (data.sec.gov, read 2026-11-25/26), cut to the `dei` taxonomy.
Each reproduces the waiting candidate's tier, value, date and flags on the code before the inline reader, and the
cut-down document gives the same share-count candidate as the full one on the code before and after it (the located
passage of the old reader excepted: its first anchor hit sits in the notes the cut drops).

| Ticker | Accession | Filed | Period | What it pins |
|---|---|---|---|---|
| GOOGL | 0001652044-26-000084 | 2026-10-29 | 2026-09-30 | three classes in millions (`scale="6"`, "5,888 million"), as of 2026-10-21; per-class EPS facts under another class-C member name |
| HOOD | 0001783879-26-000167 | 2026-10-28 | 2026-09-30 | "were N and M": two classes after the class names |
| GEMI | 0002055592-26-000074 | 2026-11-13 | 2026-09-30 | "N and M shares of Class A and Class B, respectively" |
| META | 0001628280-26-066773 | 2026-10-22 | 2026-09-30 | a cover table |
| DELL | 0001571996-26-000046 | 2026-09-08 | 2026-07-31 | dated by the cover, 2026-09-01, not the period end |
| CEG | 0001868275-26-000119 | 2026-11-09 | 2026-09-30 | one undimensioned count newer than companyfacts (whose newest row is the Q2 cover's) |
| EXC | 0001109357-26-000095 | 2026-10-30 | 2026-09-30 | a combined filing: the parent's undimensioned count, never a `dei:LegalEntityAxis` member |
| ENHA | 0001628280-26-073927 | 2026-11-13 | 2026-09-30 | a class outside the EPS share base (13.4M against Class A 13.8M and a sum of 39.7M) |
| GLXY | 0001859392-26-000112 | 2026-11-09 | 2026-09-30 | a class outside the EPS share base (193.3M against a sum of 385.7M) |
| ZLAB | 0001628280-26-070521 | 2026-11-05 | 2026-09-30 | an ADR line (`dei:AdrMember`, ordinary shares held as ADSs) beside the ordinary count: not summed |
| KNSA | 0001104659-26-119186 | 2026-10-27 | 2026-09-30 | a total (`us-gaap:CommonStockMember`) beside its four class parts: not added again |

"""Fund shares-outstanding sampling (ETF net flow, F1) — the ``FundSharesSource`` seam.

A sibling of ``ingest/prices``: cache-first page fetchers + pure parsers (``snapshot_loader``), the
keyed Polygon adapter with the dated read the backfill walks (``polygon``), the swappable source
protocol with the primary-then-fallback composite (``source`` — Polygon-primary when keyed, the
scraper pair keyless), and the per-security incremental ingest into ``fact_fund_shares``
(``ingest_security``). Display-feeding FEED data only — nothing here touches the call path.
"""

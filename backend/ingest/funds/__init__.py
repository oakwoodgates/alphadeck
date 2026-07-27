"""Fund shares-outstanding sampling (ETF net flow, F1) — the ``FundSharesSource`` seam.

A sibling of ``ingest/prices``: cache-first page fetchers + pure parsers (``snapshot_loader``), the
swappable source protocol with its two adapters and the issuer-first composite (``source``), and the
per-security incremental ingest into ``fact_fund_shares`` (``ingest_security``). Display-feeding FEED
data only — nothing here touches the call path.
"""

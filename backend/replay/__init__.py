"""The replay / backtest harness (Phase 1, the trust instrument).

Sweeps an as-of date across history, runs the REAL call pipeline (the same detectors + ``assemble_call``
that serve live, via ``pipeline.core.assemble_from_pit``) over the bitemporal facts known as-of, records the
per-thesis call timeline, then — in a STRICTLY SEPARATE pass — scores the recorded calls against realized
forward prices. The fact source is the only thing that differs from live: a ``ReplayPointInTimeData`` reads a
Parquet mirror (rebuildable, non-authoritative) through DuckDB, parity-tested against ``db.bitemporal.as_of``.

The lookahead boundary is structural: the replay pit is as-of-capped (constructor-bound), the scorer's
``RealizedPrices`` is forward-windowed, and the two are disjoint reader types that never share a read path.
See ``docs/REPLAY.md``.
"""

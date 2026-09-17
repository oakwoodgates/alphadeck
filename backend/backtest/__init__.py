"""The BACKTEST — runs, identity, and (later) the ``/backtest`` surface.

``backend/replay/`` is the ENGINE: the as-of harness, the arm-episode derivation, the forward scorer and
the metric set. It knows how to replay one window once. It does not know what a RUN is.

This package owns that: a run is an immutable, addressable artifact — one directory under
``data/backtest/runs/<run_id>/`` holding the mirror, the episodes, the outcomes, the metrics, and a
``manifest.json`` that says exactly which code, which dials, which window, which clock and which rosters
produced them. A run is never overwritten; a re-run is a new run_id in the registry. That is what makes
"count your trials" and "promotion cites run ids" possible instead of aspirational.

Nothing here writes to Postgres, and no simulated row ever reaches ``calls``. Runs are CLI-kicked, never
ambient: there is no backtest cron, because cost is the operator's to spend.
"""

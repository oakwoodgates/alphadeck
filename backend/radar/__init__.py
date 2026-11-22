"""The SPAC Radar (the nightly leg: docs/FEED_LOOP.md): a standing, deterministic watch
over the blank-check universe's TRANSITION filings, matched against every thesis's term set.

Structurally outside the call path: nothing here imports ``calls/`` or emits a ``SignalEvent`` —
radar output is a tape + recommendations (#10), never a trigger (#3)."""

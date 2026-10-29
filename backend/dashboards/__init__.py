"""The Dashboards: experimental views across the cards (``docs/DASHBOARDS.md``).

One module (or subpackage) per dashboard, ``dashboards/<slug>.py``, each served by its own router in
``app/routers/dashboards/<slug>.py``. Brainstorming-phase views: some will be improved, some cut, so each one
is self-contained and deleting it is a clean cut.

Display only. A dashboard READS (the recorded calls, the live call, the facts behind them) and never feeds
a call, never changes a card's state, never writes to the database, never sends a notification and never
calls the LLM. This package is a LEAF: nothing on the call path (pipeline, signals, calls, editions, the
nightly) imports it. ``tests/dashboards/test_isolation.py`` enforces both.
"""

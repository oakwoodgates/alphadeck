"""Scoreboard EDITIONS — the Scoreboard as a re-run of TODAY's rules, published in frozen editions.

Edition 0 is the original record: the immutable ``calls`` log, read exactly as the Scoreboard always read it.
Edition 1+ is a re-run of the current call logic from the night each company joined its basket, on the
EDITION clock (``replay.export``: a disclosed fact counts from the moment it became public, an
operator-entered fact from the day it was entered), stored in its own tables and never written to ``calls``.

Nothing on the call-of-record path imports this package (an import-graph test pins it). Design and the
operator's decisions: ``docs/SCOREBOARD.md`` §Editions.
"""

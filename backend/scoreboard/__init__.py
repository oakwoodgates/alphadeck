# The live Scoreboard (the SCORE stage) — the forward twin of the replay harness, over the RECORD.
#
# Scores what the platform ACTUALLY said — the immutable calls log (the daily call-of-record) — against
# realized, asof-capped prices. Never a recompute with today's code/dials (that is replay's job; the
# record, not the recompute, is attribution's source — docs/BOARD.md). Compute-on-read: this package
# owns no tables, writes nothing, and has no LLM anywhere on its path. Reuses the replay engine as-is
# (CallSnapshot / derive_episodes / score_episode / the metric set); the one live-only addition is
# honesty about the record itself: open episodes (still armed at the record edge), censored starts
# (armed since before the record began), and maturity (judged only once its own exit_by has elapsed).
# See docs/SCOREBOARD.md.

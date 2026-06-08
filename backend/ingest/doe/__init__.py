"""DOE / USASpending automated catalyst feed (the first automated catalyst source).

Discovers DOE awards for a hand-curated set of nuclear-basket entities and turns them into
catalyst-conviction facts deterministically (invariant #3 — never model-sourced). See ``entities.py``
for why resolution is exact-by-recipient_id, never fuzzy.
"""

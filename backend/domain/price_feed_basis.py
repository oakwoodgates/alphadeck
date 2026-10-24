"""The SYSTEM authors of a price-symbol change (``security_master.price_symbol_basis`` and the
``price_symbol_change.basis`` the repair log records) — the two writes the company-list refresh and the nightly
rename probe make on their own, beside the operator's ``operator:adopt`` / ``operator:set`` / ``operator:undo``.

Kept in ``domain`` (no I/O, no imports) so the pure tape monitor (``pipeline/tape_health.py``) can read them
without importing a repository.

- ``BASIS_RENAME_PIN`` — the company-list refresh renamed a master row in place (SEC lists the company under a
  NEW ticker) and the row already had stored price bars and no price symbol: the price leg is PINNED to the OLD
  ticker so the stored tape is never silently switched to a series whose overlap could re-version history.
  Written through the repair log (``repositories/price_feed_repo.apply_change``, action ``set``), so it is
  visible on Admin and undoable like any repair.
- ``BASIS_AUTO_ADOPT`` — the nightly rename probe SPLICE-VERIFIED the new SEC ticker against a pinned name's
  stored tape under the strict rule (``pipeline/rename_probe.auto_adoptable``) and adopted it through the same
  repair log (action ``adopt``). Undoable; an undone auto-adopt is never re-applied (the operator's call).
"""

from __future__ import annotations

BASIS_RENAME_PIN = "system:rename-pin"
BASIS_AUTO_ADOPT = "system:auto-adopt"


def is_rename_pin(basis: str | None) -> bool:
    """Is this price symbol the refresh's pin to a renamed company's OLD ticker?"""
    return basis == BASIS_RENAME_PIN

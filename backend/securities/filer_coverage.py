"""Foreign-filer coverage — WHETHER a name is a §16-exempt foreign filer that files NO Form 4, derived on
read from the master's stored raw ingredients.

The foreign-filer explainability tell: a foreign private issuer (files a 20-F) or a Canadian MJDS filer
(files a 40-F) is EXEMPT from Section 16, so it files no Form 3/4/5 — the insider-flow signal is not quiet,
it is STRUCTURALLY UNAVAILABLE. ``foreign_filer_form`` turns the two raw ingredients (migration 0031 — parsed
by ``parse_identity``, persisted by ``master.enrich``) into the specific form string ("20-F" / "40-F"), or
``None`` (not a foreign filer, or unknown).

DERIVE-ON-READ by design (the origin-chip discipline, ``securities/origin.py``): no computed label/bool is
stored, so the rule can be tuned in code with zero re-enrich, and the stored row always carries the raw
evidence (#6). Machine-parsed SURFACE identity — a submissions-level fact, NO Form-4 dependency: never a fact,
never a number, never a call input, and NOT a display signal (those live in ``signals/display/``; this module
is pure and imports nothing from the call path). It EXPLAINS, never filters (#9). Unknown → ``None``.

The rule (both ingredients required):

    a §16-exempt foreign filer that files NO Form 4
        iff  (a recent 20-F or 40-F is present)  AND  (no recent 10-K/10-Q is present)

The domestic veto (``files_domestic_forms``) is load-bearing — it kills the Energy-Fuels (UUUU) false
positive: UUUU has a legacy 40-F on file, but its RECENT 10-K/10-Q filings mean it DOES file Form 4, so the
tell must abstain. Measured live against EDGAR from the prod container: CCJ→"40-F", DNN→"20-F", NXE→"40-F"
(all foreign, no domestic forms); UUUU→None (40-F present but domestic-vetoed); UEC/LEU→None (no foreign form).
"""

from __future__ import annotations


def foreign_filer_form(
    *,
    recent_foreign_form: str | None,
    files_domestic_forms: bool | None,
) -> str | None:
    """The pure foreign-filer rule: the recent foreign form ("20-F" / "40-F") IFF it is present AND the issuer
    files no recent 10-K/10-Q (the domestic veto); otherwise ``None`` (not a §16-exempt no-Form-4 foreign
    filer, or un-enriched). No I/O, no inference — reads the SEC's own filing presence, or abstains.

    ``files_domestic_forms`` is truthy only when a recent 10-K/10-Q was parsed; ``None``/``False`` both mean
    "no domestic forms found" and do NOT veto — so an un-enriched domestic flag never suppresses a real
    foreign form (the honest direction: the veto must be an affirmative domestic-filing presence).
    """
    if recent_foreign_form and not files_domestic_forms:
        return recent_foreign_form
    return None

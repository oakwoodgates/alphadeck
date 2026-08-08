"""``foreign_filer_form`` — the pure derive-on-read foreign-filer tell (the explainability chip).

Pins the full truth table over the raw 0031 ingredients: a §16-exempt foreign filer that files NO Form 4
iff (a recent 20-F or 40-F is present) AND (no recent 10-K/10-Q is present). The domestic veto is the
load-bearing rung — the Energy-Fuels (UUUU) row (a legacy 40-F, but recent domestic forms) MUST derive
``None``, the exact false positive the rule kills. Pure and inference-free: SEC-stated filing presence only,
no I/O, no call-path import.
"""

from __future__ import annotations

from securities.filer_coverage import foreign_filer_form


def _tell(form=None, domestic=None):
    return foreign_filer_form(recent_foreign_form=form, files_domestic_forms=domestic)


def test_fpi_20f_no_domestic_forms_reads_20f():
    """The FPI arm (measured live: DNN): a 20-F present, no 10-K/10-Q → the tell fires "20-F"."""
    assert _tell(form="20-F", domestic=False) == "20-F"


def test_mjds_40f_no_domestic_forms_reads_40f():
    """The Canadian-MJDS arm (measured live: CCJ, NXE): a 40-F present, no 10-K/10-Q → "40-F"."""
    assert _tell(form="40-F", domestic=False) == "40-F"


def test_energy_fuels_domestic_veto_abstains_none():
    """THE KEY ASSERTION — the Energy-Fuels (UUUU) false positive the domestic veto exists to kill: a
    legacy 40-F on file, BUT recent 10-K/10-Q filings (files_domestic_forms True) mean it DOES file Form 4,
    so the tell MUST abstain → None. Without this veto UUUU would wrongly read "40-F, no Form 4"."""
    assert _tell(form="40-F", domestic=True) is None


def test_domestic_only_filer_abstains_none():
    """A plain US filer (measured live: UEC, LEU): no foreign form at all → None, veto irrelevant."""
    assert _tell(form=None, domestic=True) is None
    assert _tell(form=None, domestic=False) is None


def test_unenriched_row_abstains_none():
    """Un-enriched ingredients (both NULL) → the honest abstain, None (never a guessed regime)."""
    assert _tell() is None


def test_foreign_form_with_unknown_domestic_flag_still_fires():
    """A foreign form present with the domestic flag NULL (un-enriched, not an affirmative domestic filing)
    must NOT be vetoed — the veto is an affirmative 10-K/10-Q presence, so None/False both mean "no veto".
    """
    assert _tell(form="20-F", domestic=None) == "20-F"

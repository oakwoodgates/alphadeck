"""F10 (framework completion P4): the tape never confirms a Massive.com row whose window holds a filing calling off a
split of its ratio (``pipeline/detect_splits.detect_security``). No database: the SEC side is a stand-in serving the
REAL reading of AIRI's 8-K, the tape a constructed step.

AIRI's 8-K of 2026-11-19 (Items 8.01 / 9.01, ``tests/fixtures/splits``) "announced that it would not be effecting the
reverse stock split … at a ratio of 1-for-5". Until the reader read call-offs it was a 1-for-5 with no date: the
Massive.com row's related filing (``domain.splits.related``), so the row's SEC evidence states its ratio and
``with_tape`` never confirmed it alone. Read as a call-off it abstains, and only this guard keeps the tape from
confirming the row by itself.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from db.session import DEFAULT_TENANT_ID
from domain.splits import (
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_POLYGON_TAPE,
    PolygonEvidence,
    SecEvidence,
    TapeEvidence,
)
from ingest.edgar.client import EdgarClient
from pipeline import backfill_splits as bf
from pipeline import detect_splits as ds
from tests.split_fixtures import edgar_cache

AIRI_CIK = "0001009891"
AIRI_8K = bf.FilingRef("0001213900-26-122096", "8-K", date(2026, 11, 19), ("8.01", "9.01"))
# a Massive.com row in the shape of the 1-for-5 prod lists for AIRI (first session 2026-11-20)
AIRI_ROW = PolygonEvidence(
    "E-AIRI-2026-11-20", "AIRI", date(2026, 11, 20), Decimal(5), Decimal(1), "reverse_split"
)
ASOF = date(2026, 11, 25)


class _Filings:
    """A ``SecReader`` stand-in: every window holds these readings (no fetch)."""

    def __init__(self, *filings: SecEvidence) -> None:
        self.filings = list(filings)
        self.evidence = {s.accession: s for s in filings}
        self.read_here = [s.accession for s in filings]
        self.submissions: dict = {}

    def refs(self, conn, m, since, asof):
        return [s.accession for s in self.filings]

    def read(self, m, refs, *, confirmed=()):
        return list(self.filings), 0


def _steps_by_five(session: date) -> TapeEvidence:
    """Constructed: the first-served close steps x5 on ``session`` on falling volume (what a 1-for-5 does)."""
    return TapeEvidence(
        session=session,
        prior=date(2026, 11, 19),
        prior_close=0.62,
        close=3.1,
        prior_volume=400_000.0,
        volume=80_000.0,
        prior_served_before=True,
    )


def _airi_8k(tmp_path) -> SecEvidence:
    """The 8-K as the nightly reads it (``read_filing``: the catch-all screen, its filing date)."""
    edgar = EdgarClient(cache_dir=edgar_cache(tmp_path, only={AIRI_8K.accession}), allow_live=False)
    return bf.read_filing(edgar, AIRI_CIK, AIRI_8K)


def _detect(monkeypatch, *filings: SecEvidence) -> ds.SecurityDetection:
    monkeypatch.setattr(
        ds, "tape_at", lambda conn, *, tenant_id, security_id, session: _steps_by_five(session)
    )
    m = bf.Member(DEFAULT_TENANT_ID, uuid.uuid4(), "AIRI", None, None)
    return ds.detect_security(
        None,
        _Filings(*filings),
        m,
        [AIRI_ROW],
        asof=ASOF,
        sec_since=date(2026, 9, 26),
        distribution_max_factor=None,
        label_overrides_abstain=False,
        confirmed=(),
    )


def test_the_tape_confirms_the_row_when_no_filing_is_in_its_window(monkeypatch):
    """The control: this tape confirms the row on its own (Massive.com + tape)."""
    (d,) = _detect(monkeypatch).decisions
    assert (d.status, d.confirmed_via) == (STATUS_CONFIRMED, VIA_POLYGON_TAPE)


def test_the_tape_never_confirms_a_row_its_window_calls_off(monkeypatch, tmp_path):
    """Passes before P4 (the 8-K read as a 1-for-5 kept the row off the tape) and after it (the guard)."""
    (d,) = _detect(monkeypatch, _airi_8k(tmp_path)).decisions
    assert (d.status, d.confirmed_via) == (STATUS_NEEDS_REVIEW, None)


def test_a_same_ratio_calloff_is_the_rows_evidence_and_is_listed_unplaced(monkeypatch, tmp_path):
    """P4 moves this: the call-off is no longer read as the row's 1-for-5, so it is not absorbed by the row; it is
    the row's evidence as a filing that abstained, and it is listed as unplaced with its reason."""
    s = _airi_8k(tmp_path)
    det = _detect(monkeypatch, s)
    (d,) = det.decisions
    assert d.sec == s and d.sec.calloff is not None
    assert "called off: decision (5->1 stated)" in d.note
    (u,) = det.unplaced
    assert (u.accession, u.reason) == (AIRI_8K.accession, "called off: decision (5->1 stated)")

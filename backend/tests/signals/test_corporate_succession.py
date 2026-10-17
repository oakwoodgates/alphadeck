"""Succession reports (8-K12B / 8-K12G3 / 8-K15D5) are STORED on the 8-K tape and SKIPPED by the event
detectors (operator decision, 2026-10-17).

Built on REAL filings (``tests/fixtures/edgar/successor_cik_2026_10.json`` — the successor CIKs' submissions,
fetched live 2026-10-17): UROY's 8-K12B 0001493152-26-034947 (2026-07-28; items 1.01, 2.01, 2.02, 3.01,
3.02, 3.03, 5.01, 5.02, 5.03, 7.01, 9.01) and REAX's 8-K12B 0001104659-26-100334 (2026-08-24; 1.01, 2.01,
2.03, 3.01, 3.03, 5.01, 5.02, 5.03, 8.01, 9.01). In a succession report 3.01 is the listing TRANSFER, 3.02 /
3.03 the share exchange and 5.02 the carried-over officers — read as ordinary events they would fire the live
``corporate_risk`` (3.01 / 3.02 at 0.50) and feed the parked ``corporate_catalyst`` (5.02).

The DB tests run the production path end to end — ``form8k_filings`` over the real doc -> ``ingest_form8k``
-> the live ``PointInTimeData`` read -> the detector — with ``known_at`` far in the future, so the stored row
IS visible to the read and the form cut alone is what keeps it from firing. The plain-8-K test is the
contract counterfactual: the same real row with only its ``form`` changed still fires (the cut is by FORM,
never by item)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.edgar_forms import ORDINARY_8K_FORMS, SUCCESSION_8K_FORMS
from domain.enums import Kind, Role
from ingest.edgar.form8k import filing_index_url, ingest_form8k
from ingest.edgar.submissions import form8k_filings
from signals import corporate_catalyst, corporate_risk
from signals.base import PointInTimeData
from signals.corporate_events import event_facts
from tests import successor_cik_fixtures as fx

_FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
_UROY_8K12B = "0001493152-26-034947"
_UROY_8K_0807 = "0001493152-26-036666"  # a plain 8-K: 5.02, 7.01, 9.01
_REAX_8K12B = "0001104659-26-100334"
_CATALYST_ON = DEFAULT_CONFIG.model_copy(update={"corporate_catalyst_enabled": True})


def _row(ticker: str, accession: str) -> dict:
    """One REAL filing from the successor doc, in the tape-row shape the detectors read (``valid_from =
    filed``, ``source_ref`` = the EDGAR index URL the ingest stores)."""
    f = next(x for x in form8k_filings(fx.successor_doc(ticker)) if x["accession"] == accession)
    filed = date.fromisoformat(f["filed"])
    return {
        "accession": f["accession"],
        "form": f["form"],
        "items": f["items"],
        "filed": filed,
        "valid_from": filed,
        "source_ref": filing_index_url(fx.successor_cik(ticker), f["accession"]),
    }


def _ingested(db, ticker: str) -> uuid.UUID:
    """The member after the operator's Adopt (its ticker on the successor CIK), with its REAL successor
    8-K tape ingested through the production writer."""
    sid = uuid.uuid4()
    cik = fx.successor_cik(ticker)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, date(2026, 1, 1)),
        )
    ingest_form8k(db, sid, cik, form8k_filings(fx.successor_doc(ticker)))
    db.commit()
    return sid


# --- the shared cut (pure) ---------------------------------------------------------------------------


def test_event_facts_drops_succession_forms_only_and_keeps_order():
    """Every succession form is dropped, every ordinary form kept, order preserved — and on a tape with
    NO succession rows (every fixture and golden before this change; prod's 62,526 stored rows,
    read-only 2026-10-17) the cut is the identity, so no existing detector output can move."""
    uroy = _row("UROY", _UROY_8K12B)
    rows = [{**uroy, "accession": f"A-{f}", "form": f} for f in sorted(SUCCESSION_8K_FORMS)]
    ordinary = [{**uroy, "accession": f"A-{f}", "form": f} for f in sorted(ORDINARY_8K_FORMS)]
    mixed = [rows[0], ordinary[0], rows[1], ordinary[1], *rows[2:]]
    assert event_facts(mixed) == ordinary
    assert event_facts(ordinary) == ordinary


@pytest.mark.parametrize("form", sorted(SUCCESSION_8K_FORMS))
def test_every_succession_form_is_skipped_by_both_detectors(form):
    """UROY's real items under each succession form string (the amendments included) fire neither
    side — at the filing date, where every policy item is live."""
    row = {**_row("UROY", _UROY_8K12B), "form": form}
    asof = row["valid_from"]
    assert corporate_risk.score([row], uuid.uuid4(), asof) is None
    assert corporate_catalyst.score([row], uuid.uuid4(), asof) is None


def test_the_same_items_on_a_plain_8k_still_fire_the_cut_is_by_form():
    """The contract counterfactual: UROY's real 8-K12B row with ONLY its form changed to an ordinary
    "8-K" fires exactly what an ordinary filing with those items fires — the moderate (0.50) risk on
    3.01 + 3.02, and (switch on) the 5.02 personnel catalyst. So the skip is by form, never by item.
    """
    real = _row("UROY", _UROY_8K12B)
    plain = {**real, "form": "8-K"}
    asof = real["valid_from"]
    sid = uuid.uuid4()

    risk = corporate_risk.score([plain], sid, asof)
    assert risk is not None and risk.role is Role.RISK_SIGNAL and risk.kind is Kind.CORPORATE_RISK
    assert risk.score == DEFAULT_CONFIG.corporate_event_items["3.01"].score == 0.50
    assert {p.detail["item"] for p in risk.provenance} == {"3.01", "3.02"}
    assert corporate_risk.score([real], sid, asof) is None

    cat = corporate_catalyst.score([plain], sid, asof)
    assert cat is not None and cat.kind is Kind.CATALYST
    assert {p.detail["item"] for p in cat.provenance} == {"5.02"}
    assert corporate_catalyst.score([real], sid, asof) is None


# --- the production path, end to end (DB) ------------------------------------------------------------


def test_uroy_real_8k12b_is_stored_and_corporate_risk_does_not_fire(db):
    """THE REGRESSION. UROY's real 8-K12B is on the tape and visible to the live as-of read, yet the LIVE
    ``corporate_risk`` (DEFAULT_CONFIG — the switch is ON) emits nothing, at the filing date and through
    the whole 180-day window of its 3.01 / 3.02 (UROY's two plain 8-Ks carry 2.02 / 5.02 / 7.01 / 9.01 —
    no risk item, so the tape has no other risk to fire)."""
    sid = _ingested(db, "UROY")
    for asof in (date(2026, 7, 28), date(2026, 10, 16), date(2027, 1, 24)):
        pit = PointInTimeData(db, asof=asof, known_at=_FAR)
        stored = {r["accession"]: r for r in pit.corporate_event_facts(sid)}
        assert stored[_UROY_8K12B]["form"] == "8-K12B"  # STORED and read (#9)
        assert {"3.01", "3.02"} <= set(stored[_UROY_8K12B]["items"])
        assert corporate_risk.detect(pit, sid, asof, DEFAULT_CONFIG) is None


def test_uroy_real_tape_the_catalyst_reads_only_the_ordinary_8k(db):
    """The trigger side over UROY's real tape (switch forced on, the replay lab's path): on the 8-K12B's
    filing day its 5.02 fires nothing; from 2026-08-07 the PLAIN 8-K's 5.02 (a real officer change)
    fires, and the provenance names that filing alone — the 8-K12B never rides it."""
    sid = _ingested(db, "UROY")
    day0 = PointInTimeData(db, asof=date(2026, 7, 28), known_at=_FAR)
    assert corporate_catalyst.detect(day0, sid, date(2026, 7, 28), _CATALYST_ON) is None

    asof = date(2026, 8, 7)
    e = corporate_catalyst.detect(
        PointInTimeData(db, asof=asof, known_at=_FAR), sid, asof, _CATALYST_ON
    )
    assert e is not None and e.kind is Kind.CATALYST
    assert [p.ref for p in e.provenance] == [_UROY_8K_0807]


def test_reax_real_8k12b_is_stored_and_neither_detector_fires_on_it(db):
    """REAX's real 8-K12B (3.01 the listing transfer, 5.02 the carried-over officers) is stored; the live
    risk side is silent across its window, and the trigger side fires only from the PLAIN 8-K of
    2026-08-28 (5.02), never from the 8-K12B."""
    sid = _ingested(db, "REAX")
    for asof in (date(2026, 8, 24), date(2026, 10, 16)):
        pit = PointInTimeData(db, asof=asof, known_at=_FAR)
        assert _REAX_8K12B in {r["accession"] for r in pit.corporate_event_facts(sid)}
        assert corporate_risk.detect(pit, sid, asof, DEFAULT_CONFIG) is None
    day0 = PointInTimeData(db, asof=date(2026, 8, 24), known_at=_FAR)
    assert corporate_catalyst.detect(day0, sid, date(2026, 8, 24), _CATALYST_ON) is None
    asof = date(2026, 8, 28)
    e = corporate_catalyst.detect(
        PointInTimeData(db, asof=asof, known_at=_FAR), sid, asof, _CATALYST_ON
    )
    assert e is not None and [p.ref for p in e.provenance] == ["0001104659-26-103266"]

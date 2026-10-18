"""Q13 — ONE SPAC push per ANNOUNCEMENT, on a REAL multi-match announcement from the radar tape.

Pyrophyte Acquisition Corp. II (CIK 0002069238, PAII) announced its deal on 2026-10-15 with two DA-class
filings (an 8-K carrying item 1.01 and a 425). The nightly radar pass of 2026-10-15 (recorded 2026-10-16
02:40 UTC) recorded both as the company's FIRST events (prior state ``searching``) and matched both documents
against three theses' term sets. Every value below is copied verbatim from prod's ``fact_spac_event`` /
``fact_spac_match`` / ``security_master`` / ``thesis`` rows, read-only on 2026-10-17. Before Q13 this
announcement paged THREE times (one per thesis); now it is one page naming all three.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from db.session import DEFAULT_TENANT_ID
from domain.thesis import Thesis
from notify import SlackNotifier
from radar import repo
from radar.spac import RadarRunResult, _notify_announced, announcements
from radar.state import StateEvent, deal_state
from repositories import thesis_repo

CIK = "0002069238"
SID = UUID("827097e1-269e-4ebf-9427-f963be1e46b4")
AI = UUID("8b49122e-428d-487b-85a4-e2fdd458d14f")  # AI Memory & Storage
DEF = UUID("41c022c7-62d5-4b5f-bd67-3748ce43c12a")  # Modern Defense
RR = UUID("e7aa7aa2-b61f-46e3-b745-7b66ac95b2ed")  # Rainbow Rush
A425 = "0001213900-26-109601"
A8K = "0001213900-26-109597"
FILED = date(2026, 10, 15)
_IDX = "https://www.sec.gov/Archives/edgar/data/2069238/{}/{}-index.htm"
_TXT = "https://www.sec.gov/Archives/edgar/data/2069238/{}.txt"

EVENTS = [
    repo.SpacEvent(
        cik=CIK,
        company_name="Pyrophyte Acquisition Corp. II",
        form=form,
        filed=FILED,
        accession=acc,
        source_ref=_IDX.format(acc.replace("-", ""), acc),
        items=items,
        security_id=SID,
    )
    for form, acc, items in (("425", A425, None), ("8-K", A8K, ["1.01", "7.01", "9.01"]))
]
MATCHES = [
    repo.SpacMatch(
        thesis_id=tid,
        cik=CIK,
        accession=acc,
        matched_signal=signal,
        matched_broad=broad,
        truncated=True,
        source_ref=_TXT.format(acc),
        filed=FILED,
    )
    for acc in (A425, A8K)
    for tid, signal, broad in (
        (AI, ["HBM"], []),
        (DEF, [], ["aerospace", "defense"]),
        (RR, ["TMS"], ["2CB", "DMT", "LSD"]),
    )
]


class _Capture:
    def __init__(self):
        self.spac_status: list = []

    def notify(self, event):  # pragma: no cover — the radar leg never calls this
        pass

    def notify_health(self, event):  # pragma: no cover
        pass

    def notify_digest(self, event):  # pragma: no cover
        pass

    def notify_spac_status(self, event):
        self.spac_status.append(event)


def _seed(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, sector, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                SID,
                DEFAULT_TENANT_ID,
                "PAII",
                CIK,
                "Pyrophyte Acquisition Corp. II",
                "Blank Checks",
                date(2026, 1, 1),
            ),
        )
    for tid, name in ((AI, "AI Memory & Storage"), (DEF, "Modern Defense"), (RR, "Rainbow Rush")):
        thesis_repo.upsert(
            db, Thesis(id=tid, name=name, narrative="n", tenant_id=DEFAULT_TENANT_ID)
        )
    db.commit()


def test_a_real_three_thesis_announcement_is_ONE_page(db):
    _seed(db)
    # non-vacuous: these two filings, alone, ARE a transition searching -> announced
    assert (
        deal_state(
            [
                StateEvent(
                    filed=e.filed,
                    form=e.form,
                    items=tuple(e.items or ()) or None,
                    accession=e.accession,
                )
                for e in EVENTS
            ]
        )
        == "announced"
    )
    # the pre-Q13 page count for this announcement: one per (CIK, thesis) — three
    assert len(announcements({CIK}, MATCHES)[CIK]) == 3

    cap = _Capture()
    result = RadarRunResult()
    _notify_announced(db, cap, {}, EVENTS, MATCHES, {CIK: SID}, result, tenant_id=DEFAULT_TENANT_ID)

    (evt,) = cap.spac_status  # ONE page for the announcement
    assert result.status_notifications == 1
    assert evt.cik == CIK and evt.ticker == "PAII"
    assert evt.thesis_names == ("AI Memory & Storage", "Modern Defense", "Rainbow Rush")
    assert [(m.signal_terms, m.broad_terms) for m in evt.matches] == [
        (("HBM",), ()),
        ((), ("aerospace", "defense")),
        (("TMS",), ("2CB", "DMT", "LSD")),
    ]
    assert evt.url in {e.source_ref for e in EVENTS}  # the lead DA filing's EDGAR link (#6)
    lines = SlackNotifier._format_spac(evt).splitlines()
    assert lines[:4] == [
        "🟢 PAII (Pyrophyte Acquisition Corp. II) — SPAC deal ANNOUNCED · matches "
        "AI Memory & Storage, Modern Defense, Rainbow Rush",
        "AI Memory & Storage matched: HBM",
        "Modern Defense matched: aerospace, defense",
        "Rainbow Rush matched: TMS, 2CB, DMT, LSD",
    ]


def test_a_match_on_an_ARCHIVED_thesis_is_left_off_the_page(db):
    """A match on a thesis the live list no longer holds is never a ghost line; the page still goes out for
    the theses that remain (and none at all when none remain)."""
    _seed(db)
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET archived_at = now() WHERE id = %s", (DEF,))
    db.commit()
    cap = _Capture()
    _notify_announced(
        db, cap, {}, EVENTS, MATCHES, {CIK: SID}, RadarRunResult(), tenant_id=DEFAULT_TENANT_ID
    )
    (evt,) = cap.spac_status
    assert evt.thesis_names == ("AI Memory & Storage", "Rainbow Rush")

"""V2 W6c: R9 / N8 (a), where a catalyst trigger came from, on the WIRE only.

The recorded card carries the origin as the trigger's provenance source (``thesis_catalyst`` for an operator
entry); the API response derives ``TriggerRefOut.origin`` from it: ``operator`` for an operator entry and for the
theme conviction (a whole-card entry), ``platform`` for any other catalyst, ``None`` for every other kind. A
``thesis_catalyst`` citation links: an http(s) URL as is, an SEC accession to its EDGAR filing index.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.schemas_api import _trigger_out
from domain.call import TriggerRef
from domain.enums import Grade, Kind
from domain.signal import Provenance
from pipeline.seed import NUCLEAR_THESIS_ID, SMR_ID, seed_nuclear, seed_nuclear_catalyst
from repositories.thesis_catalyst_repo import entry_night
from tests.thesis_catalyst_fixtures import insert_version

ET = ZoneInfo("America/New_York")
SID = uuid.UUID(int=0xA1)


def _t(kind: Kind, *sources: Provenance) -> TriggerRef:
    return TriggerRef(label="x", kind=kind, grade=Grade.FLIP, security_id=SID, sources=list(sources))


def _out(t: TriggerRef, cik: str | None = "1234567"):
    return _trigger_out(t, {SID: cik}, {SID: "SMR"})


def test_origin_is_derived_from_the_first_source():
    entry = Provenance(source="thesis_catalyst", ref="https://example.com/a")
    doe = Provenance(source="doe_usaspending", ref="DENE0009589")
    assert _out(_t(Kind.CATALYST, entry)).origin == "operator"
    assert _out(_t(Kind.CATALYST, entry, doe)).origin == "operator"
    assert _out(_t(Kind.CATALYST, doe)).origin == "platform"
    assert _out(_t(Kind.CATALYST, doe, entry)).origin == "platform"  # the entry rides muted
    assert _out(_t(Kind.CATALYST, Provenance(source="ratified", ref="r"))).origin == "platform"
    assert _out(_t(Kind.THEME_CONVICTION, Provenance(source="ratified", ref="r"))).origin == "operator"
    assert _out(_t(Kind.TECHNICAL_BREAKOUT, Provenance(source="price", ref="p"))).origin is None
    assert _out(_t(Kind.INSIDER, Provenance(source="form4", ref="0001-26-000001"))).origin is None


def test_a_thesis_catalyst_citation_links():
    url = _out(_t(Kind.CATALYST, Provenance(source="thesis_catalyst", ref="https://example.com/a")))
    assert url.sources[0].url == "https://example.com/a"
    acc = _out(_t(Kind.CATALYST, Provenance(source="thesis_catalyst", ref="0001193125-26-234847")))
    assert acc.sources[0].url is not None and "000119312526234847" in acc.sources[0].url
    unsafe = _out(_t(Kind.CATALYST, Provenance(source="thesis_catalyst", ref="javascript:alert(1)")))
    assert unsafe.sources[0].url is None
    # a platform source keeps its old (absent) link
    assert _out(_t(Kind.CATALYST, Provenance(source="ratified", ref="https://x.example/"))).sources[
        0
    ].url is None


def test_the_call_route_carries_the_origin(client, db):
    seed_nuclear(db)
    seed_nuclear_catalyst(db)
    saved = datetime(2026, 6, 1, 10, tzinfo=ET)
    insert_version(
        db,
        NUCLEAR_THESIS_ID,
        uuid.uuid4(),
        at=saved,
        valid_from=entry_night(saved),
        security_ids=[SMR_ID],
        grade="core",
        citation="https://example.com/smr",
    )
    db.commit()
    body = client.get(f"/theses/{NUCLEAR_THESIS_ID}/call?asof={date(2026, 6, 2).isoformat()}").json()
    origins = {}
    for m in body["armed_members"]:
        for t in m["triggers"]:
            origins[(m["ticker"], t["kind"])] = t["origin"]
    assert origins[("SMR", "catalyst")] == "operator"
    assert origins[("OKLO", "catalyst")] == "platform"
    assert origins[("SMR", "technical_breakout")] is None

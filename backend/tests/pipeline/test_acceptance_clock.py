"""THE ACCEPTANCE CLOCK on the legs that store it, and the ``backfill_accepted --drift`` repair — DB-backed.

- The Form 4 leg and the 4/A leg store the listed SEC time only when it agrees with the filing date; a time
  SEC's list pushed past midnight is replaced by the filing's own header, else bounded to the end of the
  filing date (never NULL, never early). One quiet nightly line counts them.
- ``--drift`` re-dates the stored times on the wrong night across ``fact_insider_txn``, ``insider_amendment``
  and ``insider_amendment_link``: a dry run writes nothing, ``--execute`` appends exactly the planned
  versions (COUNT THE TABLES), a re-run appends zero, and the NULL backfill behaves as it always did.
- Point in time, on the PUBLIC clock (the history views' read): a filing stored a night late appears on its
  filing date's night after the repair — and not before its header's instant (no lookahead).

Every SEC document is served from a test cache or a fake client — no network.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from ingest import CacheMiss
from ingest.edgar.acceptance import EDGAR_TZ, AcceptanceTally, filing_date_bound, header_cache_key
from ingest.edgar.client import EdgarClient
from ingest.edgar.form4 import ingest_form4
from ingest.edgar.form4a import ingest_amendments, ingest_form4a
from pipeline import ingest_thesis as IT
from pipeline.backfill_accepted import (
    DRIFT_TABLES,
    drift_payload,
    run_backfill,
    run_drift,
    write_drift_payload,
)
from tests.form4a_fixtures import (
    CIKS,
    FILINGS,
    accepted_by_accession,
    filed_by_accession,
    ingest_originals,
    make_security,
    xml,
)

UTC = timezone.utc
T = DEFAULT_TENANT_ID
_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)  # issuer CIK 0001234567 (the security_id fixture's), two dated txns: 2026-06-01 + 2026-05-15

# One Form 4 filed 2026-06-02 whose SEC header says 19:00:20 EDT (23:00:20Z); SEC's list says
# 2026-06-03T04:00:20Z = 00:00:20 EDT the NEXT day — the measured +5 h drift, a night late.
ACC = "0001234567-26-000123"
FILED = date(2026, 6, 2)
HEADER = datetime(2026, 6, 2, 23, 0, 20, tzinfo=UTC)
LATE = "2026-06-03T04:00:20.000Z"
LATE_DT = datetime(2026, 6, 3, 4, 0, 20, tzinfo=UTC)
BOUND = filing_date_bound(FILED)  # 23:59:59 EDT = 2026-06-03T03:59:59Z
FAR = datetime(2100, 1, 1, tzinfo=UTC)


def _hdr(at: datetime) -> str:
    stamp = at.astimezone(EDGAR_TZ).strftime("%Y%m%d%H%M%S")
    return f"<!--\n<SEC-HEADER>{ACC}.hdr.sgml\n<ACCEPTANCE-DATETIME>{stamp}\n-->"


def _subs(rows: list[tuple[str, str, str, str]]) -> dict:
    """A submissions JSON: ``(form, accession, filingDate, acceptanceDateTime)`` rows."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "primaryDocument": [f"xslF345X05/{r[1]}.xml" for r in rows],
                "filingDate": [r[2] for r in rows],
                "acceptanceDateTime": [r[3] for r in rows],
                "items": [""] * len(rows),
            }
        }
    }


def _cache(tmp_path: Path, listed: str, *, header: bool) -> EdgarClient:
    """A cache-only client over a tmp cache holding the company's submissions, the Form 4 and (optionally)
    the filing's header page."""
    (tmp_path / "submissions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "submissions" / "CIK0001234567.json").write_text(
        json.dumps(_subs([("4", ACC, FILED.isoformat(), listed)])), encoding="utf-8"
    )
    doc = tmp_path / "forms" / ACC / f"{ACC}.xml"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(_XML, encoding="utf-8")
    if header:
        (tmp_path / header_cache_key(ACC)).write_text(_hdr(HEADER), encoding="utf-8")
    return EdgarClient(cache_dir=tmp_path, allow_live=False)


class _NoBars:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return []


def _member_thesis(db, security_id) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, T, "Acceptance", "n"),
        )
        cur.execute(
            "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), T, tid, 0, "DEVCO", "—", security_id),
        )
    db.commit()
    return tid


def _accepted(db, accession: str) -> set[datetime | None]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (security_id, accession, insider_name, valid_from, txn_seq) accepted "
            "FROM fact_insider_txn WHERE accession = %s "
            "ORDER BY security_id, accession, insider_name, valid_from, txn_seq, recorded_at DESC, id DESC",
            (accession,),
        )
        return {r["accepted"] for r in cur.fetchall()}


def _counts(db) -> dict[str, int]:
    out = {}
    with db.cursor() as cur:
        for t in DRIFT_TABLES:
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — test-local table names
            out[t] = cur.fetchone()["n"]
    db.commit()
    return out


def _ingest(db, security_id, client, monkeypatch) -> IT.NameResult:
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _NoBars())
    tid = _member_thesis(db, security_id)
    (r,) = IT.ingest_thesis(db, tid, allow_live=False, edgar_client=client)
    assert r.error is None, r.error
    return r


# --- the Form 4 leg -------------------------------------------------------------------------------------


def test_the_form4_leg_stores_the_header_time_for_a_listed_time_a_night_late(
    db, security_id, tmp_path, monkeypatch, capsys
):
    r = _ingest(db, security_id, _cache(tmp_path, LATE, header=True), monkeypatch)
    assert r.form4_appended == 2
    assert _accepted(db, ACC) == {HEADER}
    assert (r.acceptance_header, r.acceptance_bounded) == (1, 0)
    IT.print_acceptance_line([r])
    assert capsys.readouterr().out.strip() == (
        "acceptance · 1 listed time(s) disagreed with the filing date · 1 read from the header, "
        "0 bounded to the filing date"
    )


def test_the_form4_leg_bounds_to_the_filing_date_when_the_header_is_unreadable(
    db, security_id, tmp_path, monkeypatch, capsys
):
    """No header cached and no live pulls: the end of the filing date in ET — never NULL, never early."""
    r = _ingest(db, security_id, _cache(tmp_path, LATE, header=False), monkeypatch)
    assert _accepted(db, ACC) == {BOUND}
    assert BOUND >= HEADER and BOUND.astimezone(EDGAR_TZ).date() == FILED
    assert (r.acceptance_header, r.acceptance_bounded) == (0, 1)
    IT.print_acceptance_line([r])
    assert "1 bounded to the filing date" in capsys.readouterr().out


def test_a_consistent_listed_time_is_stored_as_listed_and_the_line_stays_silent(
    db, security_id, tmp_path, monkeypatch, capsys
):
    """A listed time on the filing date within EDGAR's hours is stored as listed; no header is read (the
    cache holds none, and a read would have bounded it)."""
    r = _ingest(
        db, security_id, _cache(tmp_path, "2026-06-02T23:00:20.000Z", header=False), monkeypatch
    )
    assert _accepted(db, ACC) == {HEADER}
    assert (r.acceptance_header, r.acceptance_bounded) == (0, 0)
    IT.print_acceptance_line([r])
    assert capsys.readouterr().out == ""


# --- the 4/A leg ----------------------------------------------------------------------------------------

_ATCH_4A = (
    "0001493152-26-044627"  # filed 2026-09-28, SEC accepted 21:01:26 EDT (2026-09-29T01:01:26Z)
)


def _atch_subs(listed_4a: str) -> dict:
    accs = sorted(a for a in FILINGS if FILINGS[a].key == "ATCH")
    listed = [
        listed_4a if a == _ATCH_4A else FILINGS[a].accepted.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for a in accs
    ]
    return {
        "filings": {
            "recent": {
                "form": [FILINGS[a].form for a in accs],
                "accessionNumber": accs,
                "primaryDocument": [f"xslF345X06/{a}.xml" for a in accs],
                "filingDate": [FILINGS[a].filed.isoformat() for a in accs],
                "acceptanceDateTime": listed,
            }
        }
    }


class _AtchClient:
    """The real ATCH documents by their forms/ key; a header only for ``headers`` (else uncached)."""

    def __init__(self, subs: dict, headers: dict[str, datetime]) -> None:
        self.subs, self.headers, self.header_reads = subs, headers, []

    def get_json(self, url, key):
        return self.subs

    def get_text(self, url, key):
        acc = key.split("/")[1]
        if key.endswith("-index-headers.html"):
            self.header_reads.append(acc)
            if acc not in self.headers:
                raise CacheMiss(key)
            stamp = self.headers[acc].astimezone(EDGAR_TZ).strftime("%Y%m%d%H%M%S")
            return f"<ACCEPTANCE-DATETIME>{stamp}"
        return xml(acc)


def test_the_4a_leg_stores_the_header_time_on_the_record_and_its_link(db):
    true_at = FILINGS[_ATCH_4A].accepted
    sid = make_security(db, "ATCH")
    ingest_originals(db, sid, "ATCH")
    client = _AtchClient(_atch_subs("2026-09-29T06:01:26.000Z"), {_ATCH_4A: true_at})  # +5 h
    tally = AcceptanceTally()
    out, _ = ingest_amendments(db, client, sid, CIKS["ATCH"], "ATCH", client.subs, acceptance=tally)
    db.commit()
    assert len(out.stored) == 5
    assert client.header_reads == [_ATCH_4A]  # the four consistent 4/As read no header
    assert (tally.header, tally.bound) == (1, 0)
    with db.cursor() as cur:
        cur.execute(
            "SELECT a.accepted AS rec, l.accepted AS link FROM insider_amendment a "
            "JOIN insider_amendment_link l ON l.security_id = a.security_id "
            "AND l.amendment_accession = a.accession WHERE a.accession = %s",
            (_ATCH_4A,),
        )
        row = cur.fetchone()
    assert row["rec"] == row["link"] == true_at


# --- the --drift repair ---------------------------------------------------------------------------------


class _DriftClient:
    """``fetch_submissions`` -> canned submissions per CIK; the header -> ``headers`` (else uncached)."""

    def __init__(self, by_cik: dict[str, dict], headers: dict[str, datetime]) -> None:
        self.by_cik, self.headers, self.header_reads = by_cik, headers, []

    def get_json(self, url, key):
        m = re.search(r"CIK(\d+)\.json", key)
        cik = str(int(m.group(1))) if m else ""
        if cik not in self.by_cik:
            raise CacheMiss(key)
        return self.by_cik[cik]

    def get_text(self, url, key):
        acc = key.split("/")[1]
        self.header_reads.append(acc)
        if acc not in self.headers:
            raise CacheMiss(key)
        stamp = self.headers[acc].astimezone(EDGAR_TZ).strftime("%Y%m%d%H%M%S")
        return f"<ACCEPTANCE-DATETIME>{stamp}"


def _plant(db, security_id, accession: str = ACC, accepted: datetime | None = LATE_DT) -> None:
    """A Form 4 stored as a capture after 2026-10-23 stored it: at SEC's listed (late) time."""
    ingest_form4(db, security_id, _XML, accession, accepted=accepted, member_cik="0001234567")
    db.commit()


def _late_client(*, header: bool = True, listed: str = LATE) -> _DriftClient:
    return _DriftClient(
        {"1234567": _subs([("4", ACC, FILED.isoformat(), listed)])},
        {ACC: HEADER} if header else {},
    )


def test_drift_dry_run_writes_nothing_and_reports_each_accession(db, security_id):
    _plant(db, security_id)
    before = _counts(db)
    res = run_drift(db, client=_late_client(), execute=False, log=lambda *_: None)
    assert _counts(db) == before == res.before == res.after
    (item,) = res.items
    assert (item.table, item.accession, item.filed, item.source) == (
        "fact_insider_txn",
        ACC,
        FILED,
        "header",
    )
    assert (item.stored, item.new, len(item.ids)) == (LATE_DT, HEADER, 2)
    assert _accepted(db, ACC) == {LATE_DT}


def test_drift_execute_appends_exactly_the_planned_versions_and_a_rerun_appends_zero(
    db, security_id, tmp_path
):
    _plant(db, security_id)
    before = _counts(db)
    res = run_drift(db, client=_late_client(), execute=True, log=lambda *_: None)
    after = _counts(db)
    assert (
        after["fact_insider_txn"] == before["fact_insider_txn"] + 2 == res.after["fact_insider_txn"]
    )
    assert after["insider_amendment"] == before["insider_amendment"]
    assert _accepted(db, ACC) == {HEADER}
    with db.cursor() as cur:  # append-only: the corrections name the versions they supersede
        cur.execute(
            "SELECT count(*) AS n, count(supersedes) AS sup FROM fact_insider_txn WHERE accession = %s",
            (ACC,),
        )
        assert dict(cur.fetchone()) == {"n": 4, "sup": 2}
    db.commit()
    again = run_drift(db, client=_late_client(), execute=True, log=lambda *_: None)
    assert again.items == [] and _counts(db) == after  # idempotent: COUNT THE TABLE
    # the provenance names every re-dated accession and its source
    path = write_drift_payload(drift_payload(res), base_dir=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["by_source"] == {"header": 1}
    assert payload["items"][0]["accession"] == ACC and payload["items"][0]["rows"] == 2


def test_drift_bounds_to_the_filing_date_when_the_header_is_unreadable(db, security_id):
    _plant(db, security_id)
    res = run_drift(db, client=_late_client(header=False), execute=True, log=lambda *_: None)
    assert [i.source for i in res.items] == ["bound"]
    assert _accepted(db, ACC) == {BOUND}
    # a re-run (still no header) resolves to the same bound: nothing appended
    assert (
        run_drift(db, client=_late_client(header=False), execute=True, log=lambda *_: None).items
        == []
    )


def test_drift_uses_todays_listed_time_when_it_agrees_with_the_filing_date(db, security_id):
    """An EARLY stored time across midnight (an old writer's -8 h) whose listed time today is right: the
    listed time, no header read."""
    _plant(
        db, security_id, accepted=datetime(2026, 6, 2, 3, 0, 20, tzinfo=UTC)
    )  # 23:00:20 EDT 06-01
    client = _late_client(header=False, listed="2026-06-02T23:00:20.000Z")
    res = run_drift(db, client=client, execute=True, log=lambda *_: None)
    assert [i.source for i in res.items] == ["listed"] and client.header_reads == []
    assert _accepted(db, ACC) == {HEADER}


def test_drift_leaves_a_right_night_time_alone_unless_off_hours_is_asked(db, security_id):
    """The 0000002488-26-000145 shape: stored 4 h EARLY on the right date within EDGAR's hours -> no target,
    even with --off-hours. A right-date time outside EDGAR's hours (02:00 EDT) is a target only with it.
    """
    _plant(db, security_id, accepted=datetime(2026, 6, 2, 19, 0, 20, tzinfo=UTC))  # 15:00:20 EDT
    _plant(
        db,
        security_id,
        accession="0001234567-26-000124",
        accepted=datetime(2026, 6, 2, 6, 0, tzinfo=UTC),
    )
    client = _DriftClient(
        {
            "1234567": _subs(
                [
                    ("4", ACC, FILED.isoformat(), LATE),
                    ("4", "0001234567-26-000124", FILED.isoformat(), "2026-06-02T14:00:00.000Z"),
                ]
            )
        },
        {},
    )
    assert run_drift(db, client=client, execute=False, log=lambda *_: None).items == []
    off = run_drift(db, client=client, execute=False, off_hours=True, log=lambda *_: None)
    assert [(i.accession, i.source) for i in off.items] == [("0001234567-26-000124", "listed")]
    assert off.items[0].new == datetime(2026, 6, 2, 14, 0, tzinfo=UTC)


def test_drift_leaves_an_accession_absent_from_the_index_and_an_uncached_security_alone(
    db, security_id
):
    _plant(db, security_id)
    empty = _DriftClient({"1234567": _subs([])}, {})
    res = run_drift(db, client=empty, execute=True, log=lambda *_: None)
    assert res.items == [] and res.not_in_index == 1
    cold = run_drift(db, client=_DriftClient({}, {}), execute=True, log=lambda *_: None)
    assert cold.items == [] and cold.scopes_no_submissions == 1
    assert _accepted(db, ACC) == {LATE_DT}


def test_drift_re_dates_a_4a_record_and_its_link(db):
    """A 4/A captured at SEC's late listed time: the repair appends ONE record version and ONE link version
    (verbatim but for ``accepted``), and no decision."""
    true_at = FILINGS[_ATCH_4A].accepted
    sid = make_security(db, "ATCH")
    ingest_originals(db, sid, "ATCH")
    late = datetime(2026, 9, 29, 6, 1, 26, tzinfo=UTC)  # 02:01:26 EDT the next day
    f = FILINGS[_ATCH_4A]
    ingest_form4a(
        db,
        sid,
        xml(_ATCH_4A),
        _ATCH_4A,
        member_cik=f.cik,
        filed_by_accession=filed_by_accession("ATCH"),
        accepted_by_accession=accepted_by_accession("ATCH"),
        filed=f.filed,
        accepted=late,
    )
    db.commit()
    before = _counts(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        decisions = cur.fetchone()["n"]
    client = _DriftClient(
        {str(int(CIKS["ATCH"])): _atch_subs("2026-09-29T06:01:26.000Z")}, {_ATCH_4A: true_at}
    )
    res = run_drift(db, client=client, execute=True, log=lambda *_: None)
    assert sorted((i.table, i.accession) for i in res.items) == [
        ("insider_amendment", _ATCH_4A),
        ("insider_amendment_link", _ATCH_4A),
    ]
    after = _counts(db)
    assert after["insider_amendment"] == before["insider_amendment"] + 1
    assert after["insider_amendment_link"] == before["insider_amendment_link"] + 1
    assert after["fact_insider_txn"] == before["fact_insider_txn"]
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        assert cur.fetchone()["n"] == decisions
        for table, col in (
            ("insider_amendment", "accession"),
            ("insider_amendment_link", "amendment_accession"),
        ):
            cur.execute(
                f"SELECT * FROM {table} WHERE {col} = %s ORDER BY recorded_at DESC, id DESC",  # noqa: S608
                (_ATCH_4A,),
            )
            new, old = cur.fetchall()
            assert (new["accepted"], old["accepted"]) == (true_at, late)
            same = {k for k in new if k not in ("id", "recorded_at", "accepted")}
            assert {k: new[k] for k in same} == {k: old[k] for k in same}  # verbatim copy
    db.commit()
    assert run_drift(db, client=client, execute=True, log=lambda *_: None).items == []


def test_the_null_backfill_still_writes_the_listed_time_and_never_touches_a_stored_one(
    db, security_id
):
    """The NULL mode is unchanged: it fills a NULL from the listed time (as it always did) and leaves a
    stored (even wrong-night) time to --drift."""
    _plant(db, security_id)  # stored late
    _plant(db, security_id, accession="0001234567-26-000125", accepted=None)
    client = _DriftClient(
        {
            "1234567": _subs(
                [
                    ("4", ACC, FILED.isoformat(), LATE),
                    ("4", "0001234567-26-000125", FILED.isoformat(), "2026-06-02T15:00:00.000Z"),
                ]
            )
        },
        {},
    )
    res = run_backfill(db, client=client, execute=True, log=lambda *_: None)
    assert res.rows_corrected == 2
    assert _accepted(db, "0001234567-26-000125") == {datetime(2026, 6, 2, 15, 0, tzinfo=UTC)}
    assert _accepted(db, ACC) == {LATE_DT}


# --- point in time, on the PUBLIC clock -----------------------------------------------------------------


def _visible(db, security_id, asof: date, known_at: datetime) -> set[str]:
    rows = as_of(
        db,
        "fact_insider_txn",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=T,
        clock="public",
    )
    return {r["accession"] for r in rows}


def test_a_filing_stored_a_night_late_appears_on_its_filing_night_after_the_repair_and_not_before(
    db, security_id
):
    _plant(db, security_id)
    night = known_at_for_asof(FILED, now=FAR)
    assert _visible(db, security_id, FILED, night) == set()  # THE DEFECT: a night late
    run_drift(db, client=_late_client(), execute=True, log=lambda *_: None)
    assert _visible(db, security_id, FILED, night) == {ACC}  # its filing date's night
    # ...and never before SEC's own header instant: not the night before, not 18:59 EDT that day
    prior = date(2026, 6, 1)
    assert _visible(db, security_id, prior, known_at_for_asof(prior, now=FAR)) == set()
    just_before = datetime.combine(FILED, time(18, 59), tzinfo=EDGAR_TZ)
    assert just_before < HEADER
    assert _visible(db, security_id, FILED, just_before) == set()
    assert _visible(db, security_id, FILED, HEADER) == {ACC}
    # the night after, it is still there (every version visible)
    nxt = date(2026, 6, 3)
    assert _visible(db, security_id, nxt, known_at_for_asof(nxt, now=FAR)) == {ACC}


@pytest.mark.parametrize("header", [True, False])
def test_the_repaired_time_is_never_earlier_than_the_header(db, security_id, header):
    _plant(db, security_id)
    run_drift(db, client=_late_client(header=header), execute=True, log=lambda *_: None)
    (stored,) = _accepted(db, ACC)
    assert stored >= HEADER and stored.astimezone(EDGAR_TZ).date() == FILED

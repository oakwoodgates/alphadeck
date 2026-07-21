from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from uuid import UUID

from calls.assembler import assemble_call
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import TriggerRef
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind
from domain.signal import Provenance
from repositories import calls_repo
from scoreboard.provenance import (
    FREEZE_WINDOW,
    THAW_LAG_DAYS,
    derive_episode_provenance,
    form4_accessions,
    thaw_lags,
)
from tests.scoreboard.helpers import keys_fired, persist_thesis

# The 2d provenance derivation: the thaw-lag query over fact_insider_txn's bitemporal axes (DB),
# the winning-row health read (DB), and the pure flag composition (no DB). Every ``None`` must
# degrade to UN-flagged — unknown is not a judgement (migration 0023's rule) — and the flags are
# composed after scoring, so nothing here touches the call path.

JUNE = date(2026, 6, 15)  # comfortably outside FREEZE_WINDOW


def _trig(*sources: Provenance) -> TriggerRef:
    return TriggerRef(
        label="test trigger", kind=Kind.INSIDER, security_id=uuid.uuid4(), sources=list(sources)
    )


def _form4(ref: str) -> Provenance:
    return Provenance(source="form4", ref=ref)


def _fact(db, security_id: UUID, accession: str, valid_from: date, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "accession": accession,
            "insider_name": "A Buyer",
            "txn_code": "P",
            "valid_from": valid_from,
            "recorded_at": recorded_at,
        },
    )
    db.commit()


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 12, 0, tzinfo=timezone.utc)


# --- the thaw-lag query (B2) ---


def test_thaw_lags_prompt_vs_thawed(db, security_id):
    """Calendar-day lag = first recorded_at vs the event date: a promptly-ingested filing reads a
    small lag; a thawed one (recorded 10d after its event) reads 10."""
    _fact(db, security_id, "acc-prompt", date(2026, 6, 1), _utc(2026, 6, 2))
    _fact(db, security_id, "acc-thawed", date(2026, 6, 1), _utc(2026, 6, 11))

    lags = thaw_lags(db, ["acc-prompt", "acc-thawed"], tenant_id=DEFAULT_TENANT_ID)
    assert lags == {"acc-prompt": 1, "acc-thawed": 10}


def test_thaw_lag_first_learn_wins_and_a_correction_never_shrinks_it(db, security_id):
    """MIN(recorded_at) = when the platform FIRST learned the filing; MAX(valid_from) = its latest
    event date (the conservative base). A correction appended later must not shrink the lag."""
    _fact(db, security_id, "acc-m", date(2026, 6, 1), _utc(2026, 6, 12))
    _fact(db, security_id, "acc-m", date(2026, 6, 3), _utc(2026, 6, 12))  # same filing, later txn
    assert thaw_lags(db, ["acc-m"], tenant_id=DEFAULT_TENANT_ID) == {"acc-m": 9}

    _fact(
        db, security_id, "acc-m", date(2026, 6, 3), _utc(2026, 7, 1)
    )  # a correction, learned late
    assert thaw_lags(db, ["acc-m"], tenant_id=DEFAULT_TENANT_ID) == {"acc-m": 9}


def test_thaw_lags_known_at_pin_and_missing_rows(db, security_id):
    """The known_at pin keeps the read consistent (a row recorded after it is not yet known); an
    accession with no fact rows is simply absent — unknown, never a fabricated lag."""
    _fact(db, security_id, "acc-late", date(2026, 6, 1), _utc(2026, 6, 11))

    assert thaw_lags(db, ["acc-late"], tenant_id=DEFAULT_TENANT_ID, known_at=_utc(2026, 6, 5)) == {}
    assert thaw_lags(db, ["acc-none"], tenant_id=DEFAULT_TENANT_ID) == {}
    assert thaw_lags(db, [], tenant_id=DEFAULT_TENANT_ID) == {}


# --- the winning-row health read (A) ---


def test_ingest_health_stamp_belongs_to_the_winning_row_per_asof(db, security_id):
    """The IDENTICAL dedup as latest_for_thesis: a same-asof supersede means the stamp read is the
    WINNING (latest-seq) row's — the same row whose card the Scoreboard scores. A legacy append
    reads (None, None), raw."""
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=30, conf_liveness=10)
    card = assemble_call(thesis, [conv, conf], date(2026, 6, 1), DEFAULT_CONFIG)
    calls_repo.append(db, card, ingest_fresh=False, ingest_errors=3)
    calls_repo.append(db, card, ingest_fresh=True, ingest_errors=0)  # the superseding re-run
    legacy = assemble_call(thesis, [conv], date(2026, 6, 2), DEFAULT_CONFIG)
    calls_repo.append(db, legacy)  # no stamp: a legacy/manual append
    db.commit()

    health = calls_repo.ingest_health_for_thesis(db, thesis.id)
    assert health[date(2026, 6, 1)] == (True, 0)
    assert health[date(2026, 6, 2)] == (None, None)


# --- the flag composition (pure) ---


def test_freeze_window_edges():
    """B1 is inclusive on both ends: 07-09 no, 07-10 yes, 07-17 yes, 07-18 no."""
    assert FREEZE_WINDOW == (date(2026, 7, 10), date(2026, 7, 17))
    for d, expect in [
        (date(2026, 7, 9), False),
        (date(2026, 7, 10), True),
        (date(2026, 7, 17), True),
        (date(2026, 7, 18), False),
    ]:
        p = derive_episode_provenance(d, [], health={}, lags={})
        assert p.freeze_era is expect
        assert p.ingest_flagged is expect


def test_legacy_null_stamp_never_flags():
    """None = pre-0023 / manual append — raw, never coerced to a judgement."""
    p = derive_episode_provenance(JUNE, [], health={}, lags={})
    assert p.arm_ingest_fresh is None
    assert p.ingest_flagged is False and p.ingest_note is None


def test_partial_stamp_flags_with_the_note():
    p = derive_episode_provenance(JUNE, [], health={JUNE: (False, 2)}, lags={})
    assert p.arm_ingest_fresh is False and p.ingest_flagged is True
    assert p.ingest_note == "partial ingest on the arm-date run (2 names errored)"
    one = derive_episode_provenance(JUNE, [], health={JUNE: (False, 1)}, lags={})
    assert one.ingest_note == "partial ingest on the arm-date run (1 name errored)"


def test_clean_stamp_stays_clean():
    p = derive_episode_provenance(JUNE, [], health={JUNE: (True, 0)}, lags={})
    assert p.arm_ingest_fresh is True
    assert p.ingest_flagged is False and p.ingest_note is None


def test_no_form4_sources_degrades_to_unknown_not_flagged():
    """A non-insider arm (price-only provenance) has no thaw leg: unknown, never flagged by B2."""
    trig = _trig(Provenance(source="price", ref="price:DEVCO:2026-06-02"))
    p = derive_episode_provenance(JUNE, [trig], health={}, lags={"acc-1": 30})
    assert p.thaw_lag_days is None and p.ingest_flagged is False


def test_form4_source_without_fact_rows_degrades_to_unknown_not_flagged():
    p = derive_episode_provenance(JUNE, [_trig(_form4("acc-missing"))], health={}, lags={})
    assert p.thaw_lag_days is None and p.ingest_flagged is False


def test_thaw_boundary_at_the_documented_constant():
    """<= THAW_LAG_DAYS carries the lag quietly; beyond it flags with the note."""
    assert THAW_LAG_DAYS == 7
    trig = _trig(_form4("acc-1"))
    ok = derive_episode_provenance(JUNE, [trig], health={}, lags={"acc-1": 7})
    assert ok.thaw_lag_days == 7 and ok.ingest_flagged is False and ok.ingest_note is None
    late = derive_episode_provenance(JUNE, [trig], health={}, lags={"acc-1": 8})
    assert late.thaw_lag_days == 8 and late.ingest_flagged is True
    assert late.ingest_note == "insider source ingested 8d after its event date"


def test_thaw_lag_is_the_max_across_cited_accessions():
    trig = _trig(_form4("acc-1"), _form4("acc-2"))
    p = derive_episode_provenance(JUNE, [trig], health={}, lags={"acc-1": 2, "acc-2": 9})
    assert p.thaw_lag_days == 9 and p.ingest_flagged is True


def test_all_three_mechanisms_compose_one_note():
    trig = _trig(_form4("acc-1"))
    arm = date(2026, 7, 10)
    p = derive_episode_provenance(arm, [trig], health={arm: (False, 1)}, lags={"acc-1": 9})
    assert p.ingest_flagged is True
    assert p.ingest_note == (
        "partial ingest on the arm-date run (1 name errored)"
        " · armed inside the 2026-07 EDGAR freeze window"
        " · insider source ingested 9d after its event date"
    )


def test_form4_accessions_dedup_sort_and_skip_other_sources():
    t1 = _trig(_form4("acc-b"), Provenance(source="price", ref="price:X:2026-06-02"))
    t2 = _trig(_form4("acc-a"), _form4("acc-b"))
    assert form4_accessions([t1, t2]) == ["acc-a", "acc-b"]
    assert form4_accessions([]) == []

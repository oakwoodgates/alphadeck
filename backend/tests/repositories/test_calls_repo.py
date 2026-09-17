from __future__ import annotations

from datetime import date, datetime, timezone

import psycopg
import pytest
from psycopg.types.json import Json

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import State, Verdict
from repositories import calls_repo, thesis_repo
from repositories.mappers import call_to_row
from tests.calls.factories import make_thesis


def _persist_minimal_thesis(db):
    # basket=[] avoids the security_master FK; the calls log only needs the thesis to exist
    thesis = make_thesis(basket=[])
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis


def test_append_and_read_back_roundtrips(db):
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)  # incubating (no events)
    calls_repo.append(db, card)
    db.commit()

    logged = calls_repo.list_for_thesis(db, thesis.id)
    assert len(logged) == 1
    assert logged[0].thesis_id == thesis.id
    assert logged[0].state is State.INCUBATING
    assert logged[0].model_dump_json() == card.model_dump_json()  # full round-trip through jsonb


def test_calls_log_is_immutable(db):
    """The accountability log is write-only: a real DB trigger blocks UPDATE (not a convention)."""
    thesis = _persist_minimal_thesis(db)
    calls_repo.append(db, assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG))
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE calls SET verdict = 'tampered' WHERE thesis_id = %s", (thesis.id,))
    db.rollback()


def test_latest_for_thesis_dedups_to_the_call_of_record(db):
    """``latest_for_thesis`` returns one row per as-of (the latest append wins); ``list_for_thesis``
    keeps the full append-only history. This is what stops a future scoreboard reading duplicates.
    """
    thesis = _persist_minimal_thesis(db)
    asof = date(2026, 6, 1)
    base = assemble_call(thesis, [], asof, DEFAULT_CONFIG)  # incubating -> verdict WATCHING
    # two re-runs at the SAME asof (e.g. after a fact correction) — the later seq supersedes
    calls_repo.append(db, base.model_copy(update={"verdict": Verdict.NOT_YET}))
    calls_repo.append(db, base.model_copy(update={"verdict": Verdict.WATCHING}))
    calls_repo.append(db, base.model_copy(update={"asof": date(2026, 6, 2)}))  # a different asof
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 3  # history keeps every row

    by_asof = {c.asof: c for c in calls_repo.latest_for_thesis(db, thesis.id)}
    assert set(by_asof) == {date(2026, 6, 1), date(2026, 6, 2)}  # one row per as-of
    assert by_asof[date(2026, 6, 1)].verdict is Verdict.WATCHING  # the latest append, not NOT_YET


def test_record_if_changed_appends_first_then_skips_identical(db):
    """A re-run on UNCHANGED data appends NOTHING — asserted by COUNTING the table, not the read (the read
    dedups, so a duplicate append would hide behind a correct read while the log silently grows)."""
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)

    assert calls_repo.record_if_changed(db, card) is True  # first time -> append
    db.commit()
    assert calls_repo.record_if_changed(db, card) is False  # identical -> NO append
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 1  # the TABLE has one row, not two


def test_record_if_changed_appends_exactly_one_on_a_real_change(db):
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)
    calls_repo.record_if_changed(db, card)
    db.commit()

    changed = card.model_copy(update={"verdict": Verdict.NOT_YET})
    assert calls_repo.record_if_changed(db, changed) is True  # differs -> one new versioned row
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 2  # exactly one new row
    assert calls_repo.latest_for_thesis(db, thesis.id)[0].verdict is Verdict.NOT_YET  # latest wins


def test_record_if_changed_ignores_a_pure_list_reorder(db):
    """The compare is canonical (order-independent), so reordering an unordered card list is NOT a change —
    the determinism guard: a flapping serialize would re-append every run."""
    thesis = _persist_minimal_thesis(db)
    base = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)

    assert calls_repo.record_if_changed(db, base.model_copy(update={"missing": ["x", "y"]})) is True
    db.commit()
    # same set, reversed order -> must NOT append
    assert (
        calls_repo.record_if_changed(db, base.model_copy(update={"missing": ["y", "x"]})) is False
    )
    db.commit()

    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 1


def test_include_reconstructed_filters_BEFORE_the_dedup_and_the_marker_rides_the_write(db):
    """0042: the default read sees every row (latest per as-of, marker-blind); the honest read drops
    reconstructed rows BEFORE ``DISTINCT ON``, so a night with a nightly row AND a later reconstruction
    yields the NIGHTLY row, and a reconstruction-only night yields nothing. The marker rides the write,
    never the compare: an identical card is refused whichever marker the prior carries."""
    thesis = _persist_minimal_thesis(db)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    nightly = assemble_call(thesis, [], d1, DEFAULT_CONFIG)
    recon_d1 = nightly.model_copy(update={"verdict": Verdict.NOT_YET})  # a later re-run, marked
    recon_d2 = assemble_call(thesis, [], d2, DEFAULT_CONFIG)
    calls_repo.append(db, nightly, ingest_fresh=True, ingest_errors=0)  # the cron's shape
    assert calls_repo.record_if_changed(db, recon_d1, reconstructed=True) is True
    assert calls_repo.record_if_changed(db, recon_d2, reconstructed=True) is True
    db.commit()
    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 3  # COUNT THE TABLE

    every = {c.asof: c for c in calls_repo.latest_for_thesis(db, thesis.id)}
    assert (
        set(every) == {d1, d2} and every[d1].verdict is Verdict.NOT_YET
    )  # latest wins, marker-blind
    honest = {
        c.asof: c for c in calls_repo.latest_for_thesis(db, thesis.id, include_reconstructed=False)
    }
    assert set(honest) == {d1} and honest[d1].verdict is nightly.verdict  # the nightly row survives
    assert calls_repo.ingest_health_for_thesis(db, thesis.id) == {
        d1: (None, None),
        d2: (None, None),
    }
    assert calls_repo.ingest_health_for_thesis(db, thesis.id, include_reconstructed=False) == {
        d1: (True, 0)
    }
    # the banner's list names only nights with NOTHING honest: d1 (a nightly row beside the later
    # reconstruction — scored from the nightly row) is NOT listed; d2 (reconstruction only) is
    assert calls_repo.reconstructed_asofs(db, upto=date(2026, 5, 31)) == []
    assert calls_repo.reconstructed_asofs(db, upto=d1) == []
    assert calls_repo.reconstructed_asofs(db, upto=d2) == [d2]

    # idempotency is marker-blind: the identical card appends nothing, with or without the flag
    assert calls_repo.record_if_changed(db, recon_d2, reconstructed=True) is False
    assert calls_repo.record_if_changed(db, recon_d2) is False
    db.commit()
    assert len(calls_repo.list_for_thesis(db, thesis.id)) == 3


# --- G2c: recorded_asof_stamps — the hole check's input, value-free ------------------------------------


def _insert_call_at(db, thesis, *, asof: date, recorded_at: datetime):
    """Append a call-of-record for ``asof`` with an EXPLICIT ``recorded_at``.

    Why an explicit INSERT rather than ``calls_repo.append`` followed by an UPDATE: the ``calls`` log is
    immutable — a real ``no_update`` trigger (migration 0003) blocks rewriting a recorded call — so the
    transaction stamp has to be supplied at insert time. It is otherwise exactly the row ``append`` writes:
    a genuinely assembled card through the repo's own ``call_to_row`` mapper, only with the stamp pinned
    instead of defaulted to ``now()``."""
    card = assemble_call(thesis, [], asof, DEFAULT_CONFIG)
    row = call_to_row(card, DEFAULT_TENANT_ID)
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, recorded_at)
               VALUES (%(tenant_id)s, %(thesis_id)s, %(asof)s, %(state)s, %(verdict)s, %(card)s,
                       %(recorded_at)s)""",
            {**row, "card": Json(row["card"]), "recorded_at": recorded_at},
        )
    db.commit()


def test_recorded_asof_stamps_returns_the_LATEST_recorded_at_per_asof(db):
    """MAX is the load-bearing aggregate: the caller asks "was SOME row for this night recorded at/after the
    cutoff", so a night carrying BOTH a daytime row and a proper post-close row must report the later stamp —
    otherwise a night that WAS properly recorded would read as daytime-only."""
    thesis = _persist_minimal_thesis(db)
    asof = date(2026, 9, 8)
    morning = datetime(2026, 9, 8, 13, 15, tzinfo=timezone.utc)  # 09:15 ET
    night = datetime(2026, 9, 9, 2, 35, tzinfo=timezone.utc)  # 22:35 ET that evening
    _insert_call_at(db, thesis, asof=asof, recorded_at=morning)
    _insert_call_at(db, thesis, asof=asof, recorded_at=night)

    stamps = calls_repo.recorded_asof_stamps(db, since=date(2026, 9, 1))

    assert set(stamps) == {asof}
    assert stamps[asof] == night  # the LATER of the two, not the first


def test_recorded_asof_stamps_is_bounded_by_since(db):
    """Bounded like its predecessor: the hole check scans a window, so the read must not drag in the whole
    history."""
    thesis = _persist_minimal_thesis(db)
    old, recent = date(2026, 8, 3), date(2026, 9, 8)
    _insert_call_at(
        db, thesis, asof=old, recorded_at=datetime(2026, 8, 4, 2, 35, tzinfo=timezone.utc)
    )
    _insert_call_at(
        db, thesis, asof=recent, recorded_at=datetime(2026, 9, 9, 2, 35, tzinfo=timezone.utc)
    )

    assert set(calls_repo.recorded_asof_stamps(db, since=date(2026, 9, 1))) == {recent}
    assert set(calls_repo.recorded_asof_stamps(db, since=date(2026, 8, 1))) == {old, recent}


def test_recorded_asof_stamps_is_EMPTY_on_a_fresh_log(db):
    """The quiet fresh-install shape — the caller reads "no nights recorded", never a crash."""
    assert calls_repo.recorded_asof_stamps(db, since=date(2026, 9, 1)) == {}


# --- run identity: config_hash / code_sha / run_kind (F1, migration 0044) ------------------------------


def _row_identity(db, thesis_id) -> list[tuple]:
    """Every row's (seq, config_hash, code_sha, run_kind, ingest_fresh) — the RAW table, oldest first.
    The reads under test dedup, so a test that only looked through them could not see the table grow.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT seq, config_hash, code_sha, run_kind, ingest_fresh FROM calls "
            "WHERE thesis_id = %s ORDER BY seq",
            (thesis_id,),
        )
        return [
            (r["seq"], r["config_hash"], r["code_sha"], r["run_kind"], r["ingest_fresh"])
            for r in cur.fetchall()
        ]


def _stamps(db, thesis_id) -> list[tuple]:
    """The same rows without ``seq`` — ``calls.seq`` is a TABLE-WIDE sequence, so its absolute value
    depends on every other test that ran first; only its ordering is ever meaningful."""
    return [r[1:] for r in _row_identity(db, thesis_id)]


def _count_calls(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        return cur.fetchone()["n"]


def test_a_changed_config_hash_with_an_identical_card_appends_NOTHING(db):
    """THE F1 guarantee, and the reason the three columns live off the card: a dial edit (or a deploy, or a
    Sunday manual run) must not re-record a thesis whose call did not change.

    COUNT THE TABLE, not the read: the as-of read dedups by construction, so a spurious duplicate would sit
    behind a perfectly correct-looking `latest_for_thesis` while the log silently grew (the idempotency
    convention that the M2 ingest and the daily cron both live by)."""
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)

    assert calls_repo.record_if_changed(db, card, config_hash="aaaa", run_kind="cron") is True
    db.commit()
    before = _count_calls(db)

    # same card, DIFFERENT policy + a different run kind + a code sha that was unknown before
    assert (
        calls_repo.record_if_changed(
            db, card, config_hash="bbbb", code_sha="deadbee", run_kind="manual"
        )
        is False
    )
    db.commit()

    assert _count_calls(db) == before  # the table did NOT grow
    # `seq` is a table-wide sequence, so only its ORDER is meaningful — compare the stamps themselves
    assert _stamps(db, thesis.id) == [("aaaa", None, "cron", None)]  # the first stamp survives


def test_a_changed_card_with_the_SAME_hash_appends_exactly_one(db):
    """The mirror: an unchanged config must never suppress a genuinely changed card."""
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)
    calls_repo.record_if_changed(db, card, config_hash="aaaa", run_kind="cron")
    db.commit()
    before = _count_calls(db)

    changed = card.model_copy(update={"verdict": Verdict.NOT_YET})
    assert calls_repo.record_if_changed(db, changed, config_hash="aaaa", run_kind="cron") is True
    db.commit()

    assert _count_calls(db) == before + 1


def test_canonical_is_BLIND_to_the_identity_columns(db):
    """The structural reason the test above holds, asserted directly so it cannot rot: the compare reads
    the CARD, and the identity never enters it. If someone later "helpfully" moves config_hash onto the
    CallCard, this fails before the churn reaches a prod night."""
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)
    calls_repo.append(db, card, config_hash="aaaa", code_sha="1111111", run_kind="cron")
    calls_repo.append(db, card, config_hash="bbbb", code_sha="2222222", run_kind="manual")
    db.commit()

    logged = calls_repo.list_for_thesis(db, thesis.id)
    assert len(logged) == 2
    assert calls_repo._canonical(logged[0]) == calls_repo._canonical(logged[1])


def test_append_without_the_stamps_leaves_all_three_NULL(db):
    """A legacy/manual append stamps nothing, and NULL is the honest value — never a coerced default."""
    thesis = _persist_minimal_thesis(db)
    calls_repo.append(db, assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG))
    db.commit()
    assert _stamps(db, thesis.id) == [(None, None, None, None)]


def test_run_kind_is_constrained_by_the_DATABASE(db):
    """The value set is pinned by a CHECK, not by convention — a typo'd kind cannot reach the record."""
    thesis = _persist_minimal_thesis(db)
    card = assemble_call(thesis, [], date(2026, 6, 1), DEFAULT_CONFIG)
    with pytest.raises(psycopg.errors.CheckViolation):
        calls_repo.append(db, card, run_kind="weekend")
    db.rollback()


def test_run_identity_and_ingest_health_pick_the_SAME_winning_row(db):
    """The two peer reads must agree on WHICH row they describe, or an episode gets captioned with one
    run's fingerprint and another run's ingest health. Asserted on the chosen SEQ, not merely on plausible
    values: both reads must resolve to the row the raw table says is latest for that as-of."""
    thesis = _persist_minimal_thesis(db)
    asof = date(2026, 6, 1)
    base = assemble_call(thesis, [], asof, DEFAULT_CONFIG)
    calls_repo.append(
        db,
        base.model_copy(update={"verdict": Verdict.NOT_YET}),
        ingest_fresh=False,
        ingest_errors=3,
        config_hash="old-policy",
        run_kind="manual",
    )
    calls_repo.append(
        db,
        base.model_copy(update={"verdict": Verdict.WATCHING}),
        ingest_fresh=True,
        ingest_errors=0,
        config_hash="new-policy",
        code_sha="abc1234",
        run_kind="cron",
    )
    db.commit()

    rows = _row_identity(db, thesis.id)
    assert len(rows) == 2
    winner = max(rows)  # ordered by seq — the latest append is the call of record for this as-of
    winner_seq, winner_hash, winner_sha, winner_kind, winner_fresh = winner

    identity = calls_repo.run_identity_for_thesis(db, thesis.id)
    health = calls_repo.ingest_health_for_thesis(db, thesis.id)

    assert identity[asof] == (winner_hash, winner_sha, winner_kind)
    assert health[asof][0] == winner_fresh
    # and the winner really is the later row, not "the one that happened to sort first"
    assert winner_seq == max(r[0] for r in rows)
    assert (winner_hash, winner_kind) == ("new-policy", "cron")


def test_run_identity_honors_include_reconstructed_like_its_peer(db):
    """A reconstructed row must be dropped BEFORE the per-as-of dedup here exactly as it is for the ingest
    stamps — otherwise the Scoreboard would score a nightly row while captioning it with a backfill's.
    """
    thesis = _persist_minimal_thesis(db)
    asof = date(2026, 6, 1)
    base = assemble_call(thesis, [], asof, DEFAULT_CONFIG)
    calls_repo.append(
        db, base, ingest_fresh=True, config_hash="nightly", run_kind="cron"
    )  # the honest row
    calls_repo.append(
        db,
        base.model_copy(update={"verdict": Verdict.NOT_YET}),
        reconstructed=True,
        config_hash="reconstruction",
        run_kind="backfill",
    )  # a LATER reconstruction of the same night
    db.commit()

    assert calls_repo.run_identity_for_thesis(db, thesis.id)[asof].run_kind == "backfill"
    assert (
        calls_repo.run_identity_for_thesis(db, thesis.id, include_reconstructed=False)[
            asof
        ].run_kind
        == "cron"
    )
    # the peer read agrees on the same row under the same flag
    assert (
        calls_repo.ingest_health_for_thesis(db, thesis.id, include_reconstructed=False)[asof][0]
        is True
    )

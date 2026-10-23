"""S5 — the INK (migration 0064): an edition episode's score is frozen once its signal window ENDS.

An episode matures at its ``exit_by`` (the window end — never the de-arm) and is inked the first night the
market tape has printed a session past it; from then on the Scoreboard's edition read uses the inked score,
so a later price correction never re-scores it, while a scrubbed-back view before the window ended still
scores on read (no lookahead). Inking is idempotent (count the table), and ``ink_running_at_close`` inks the
rest when an edition closes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from calls.assembler import assemble_call
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from editions.nightly import ink_running_at_close, plan_ink, run_nightly
from replay.scoring import score_episode
from repositories import editions_repo
from scoreboard.prices import PgRealizedPrices
from scoreboard.record import EditionSource, derive_thesis_record
from tests.calls.factories import insider_event
from tests.editions.fixtures import weekdays
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis

ARM = date(2026, 6, 1)  # Mon
EXIT_BY = date(2026, 6, 10)  # Wed: the conviction key's liveness (9 days) — the signal window's end
LAST = date(2026, 6, 12)  # Fri: the edition's last page
ENGINE = {"fingerprint": "fp-test", "config_hash": "cfg-test", "code_sha": None}


def _counts(db) -> dict[str, int]:
    c = editions_repo.table_counts(db)
    db.commit()
    return c


def _edition(db, security_id, *, long_sid=None):
    """An edition whose pages arm the company on 06-01 (window end 06-10) — and, with ``long_sid``, a second
    thesis armed the same night with a window far past the edition's last page."""
    thesis = persist_thesis(db, security_id)
    for i, d in enumerate(weekdays(date(2026, 5, 25), date(2026, 6, 30))):
        bar(db, security_id, d, 100.0 + i)
    conv, conf = keys_fired(security_id, ARM, conv_liveness=9, conf_liveness=4)
    warm = insider_event(security_id=security_id, liveness=60).model_copy(
        update={"asof": date(2026, 5, 29)}
    )
    pages = [assemble_call(thesis, [warm], date(2026, 5, 29), DEFAULT_CONFIG)]
    pages += [assemble_call(thesis, [conv, conf], d, DEFAULT_CONFIG) for d in weekdays(ARM, LAST)]
    long_thesis = None
    if long_sid is not None:
        long_thesis = persist_thesis(db, long_sid)
        for i, d in enumerate(weekdays(date(2026, 5, 25), date(2026, 6, 30))):
            bar(db, long_sid, d, 50.0 + i)
        lc, lf = keys_fired(long_sid, ARM, conv_liveness=60, conf_liveness=40)
        pages += [
            assemble_call(long_thesis, [lc, lf], d, DEFAULT_CONFIG) for d in weekdays(ARM, LAST)
        ]
    now = datetime.now(timezone.utc)
    editions_repo.publish_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        number=1,
        label="first re-run",
        causes=["initial"],
        engine=ENGINE,
        data_known_by=now,
        pages=pages,
        entries=[],
        computed_at=now,
    )
    db.commit()
    ed = editions_repo.get_by_number(db, tenant_id=DEFAULT_TENANT_ID, number=1)
    db.commit()
    return thesis, long_thesis, ed


def _episode(db, thesis, ed, asof):
    record, _ = derive_thesis_record(db, thesis, asof, source=EditionSource(ed))
    db.commit()
    (e,) = record.episodes
    return e


def test_the_episode_is_inked_the_night_after_its_window_ends_and_only_once(db, security_id):
    thesis, _, ed = _edition(db, security_id)
    on_read = _episode(db, thesis, ed, LAST)
    assert on_read.episode.exit_by == EXIT_BY and on_read.matured and on_read.inked is None

    # ON the window-end night the market has not printed a session past it yet: not inked, said why
    at_end = plan_ink(db, ed, EXIT_BY)
    db.commit()
    assert at_end.rows == [] and "not printed a session past" in at_end.waiting[0]["why"]

    nxt = plan_ink(db, ed, EXIT_BY + timedelta(days=1))
    db.commit()
    (row,) = nxt.rows
    assert (row["security_id"], row["arm_date"], row["kind"]) == (security_id, ARM, "matured")
    before = _counts(db)
    n = editions_repo.ink_outcomes(db, tenant_id=DEFAULT_TENANT_ID, edition_id=ed.id, rows=nxt.rows)
    db.commit()
    assert n == 1 and _counts(db)["edition_outcome"] == before["edition_outcome"] + 1

    # COUNT THE TABLE: inking again appends nothing, and a later night finds nothing left to ink
    again = editions_repo.ink_outcomes(
        db, tenant_id=DEFAULT_TENANT_ID, edition_id=ed.id, rows=nxt.rows
    )
    db.commit()
    assert again == 0 and plan_ink(db, ed, LAST).rows == []
    db.commit()
    assert _counts(db)["edition_outcome"] == before["edition_outcome"] + 1


def test_the_edition_read_uses_the_ink_and_a_later_price_correction_never_rescores_it(
    db, security_id
):
    thesis, _, ed = _edition(db, security_id)
    rows = plan_ink(db, ed, LAST).rows
    editions_repo.ink_outcomes(db, tenant_id=DEFAULT_TENANT_ID, edition_id=ed.id, rows=rows)
    db.commit()
    inked = _episode(db, thesis, ed, LAST)
    assert inked.inked == "matured"
    final = inked.outcome.forward_return
    assert final is not None

    # a vendor correction of the exit bar, appended later (a new version, stamped now)
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "d": EXIT_BY,
            "close": 999.0,
            "valid_from": EXIT_BY,
        },
    )
    db.commit()
    after = _episode(db, thesis, ed, LAST)
    assert after.inked == "matured" and after.outcome.forward_return == final  # frozen
    rescored = score_episode(
        after.episode, PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=LAST)
    )
    db.commit()
    assert rescored.forward_return != final  # what scoring on read would now say
    # the episode's own fields still come from the pages
    assert after.outcome.close_reason == after.episode.close_reason

    # a view before the window ended is RUNNING: scored on read, never the final number (no lookahead)
    early = _episode(db, thesis, ed, EXIT_BY - timedelta(days=1))
    assert early.inked is None and early.matured is False


def test_the_nightly_job_inks_and_a_second_run_appends_nothing(db, security_id):
    thesis, _, ed = _edition(db, security_id)
    first = run_nightly(db, apply=True, end=LAST)
    assert first.ink["inked"] == 1 and "1 final score inked" in first.admin_line()
    once = _counts(db)
    second = run_nightly(db, apply=True, end=LAST)
    assert second.ink["inked"] == 0 and _counts(db) == once


def test_ink_running_at_close_inks_the_rest_as_of_the_last_night(db, security_id):
    from tests.editions.test_nightly import _security

    long_sid = _security(db, "LONGCO", "0007654399")
    thesis, long_thesis, ed = _edition(db, security_id, long_sid=long_sid)
    editions_repo.ink_outcomes(
        db, tenant_id=DEFAULT_TENANT_ID, edition_id=ed.id, rows=plan_ink(db, ed, LAST).rows
    )
    db.commit()
    before = _counts(db)
    n = ink_running_at_close(db, ed)
    db.commit()
    assert n == 1 and _counts(db)["edition_outcome"] == before["edition_outcome"] + 1
    assert ink_running_at_close(db, ed) == 0  # idempotent
    db.commit()
    running = _episode(db, long_thesis, ed, LAST)
    assert running.inked == "running_at_close" and running.matured is False
    # inside the closed edition, before its last night, it still scores on read (the G13 limitation)
    assert _episode(db, long_thesis, ed, LAST - timedelta(days=1)).inked is None
    # the matured one kept its own ink
    assert _episode(db, thesis, ed, LAST).inked == "matured"

"""Q18's skipped-filing BASELINE across a skipped-thesis catch-up — the per-thesis carry-forward.

The first Q18 build read the skip baseline from the newest FULL-roster pass only, the rule the stale-tape and
name-error baselines had before ``carried_inventory``. That made a ``--catch-up`` invisible to the next pass:
a filing FIRST skipped during the catch-up printed its line there and AGAIN at the next full pass. Now each
thesis's artifact entry records the filings ITS legs skipped (like ``name_errors``), and
``previous_ingest_skips`` is the union over ``carried_inventory(..., evaluated_key="ingest_skips_evaluated")``:
each thesis's list from the newest finished, live pass that COVERED it.

Every sequence runs the REAL pass (``tests/pipeline/catchup_pass_stubs.py``: the real artifact writer, the
real baseline reader, the real diff) over the roster ``run_daily`` returns. What the legs would print is
decided by the ``known_skips`` the pass hands ``run_daily`` (a filing in it is counted, not re-printed —
proved at the leg in ``test_ingest_thesis.py`` / ``test_schedule13.py``), and what the pass reports as new is
``outcome.skips.new``; both are asserted, pass by pass.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import date

import pytest

from ingest.skips import FORM4_UNPARSEABLE, IngestSkip, skip_key
from pipeline import cron_run_log, daily
from tests.pipeline.catchup_pass_stubs import Name, covered, next_second, prime_pass, skipped

_D1, _D2, _D3 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)


@pytest.fixture
def pager(monkeypatch, tmp_path):
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path / "cron_runs")
    return prime_pass(monkeypatch)


def _names():
    return Name("AOK", uuid.uuid4()), Name("BOK", uuid.uuid4())


def _with_skips(result: daily.ThesisRunResult, *accessions: str) -> daily.ThesisRunResult:
    """``result`` with its healthy member's legs having skipped ``accessions`` (pre-XML Form 4s, the
    audited real class) — the shape ``ingest_thesis`` returns."""
    first, *rest = result.ingested
    skips = tuple(
        IngestSkip(FORM4_UNPARSEABLE, a, first.security_id, first.ticker, "syntax error")
        for a in accessions
    )
    return dataclasses.replace(
        result, ingested=[dataclasses.replace(first, ingest_skips=skips), *rest]
    )


def _run(monkeypatch, pager, roster, *, asof, catch_up=False):
    """One REAL pass -> (the known_skips the loop was handed, the outcome)."""
    next_second()
    seen: dict = {}

    def _run_daily(conn, **k):
        seen["known"] = k.get("known_skips")
        return list(roster)

    monkeypatch.setattr(daily, "run_daily", _run_daily)
    out = daily.run_daily_pass(
        asof=asof, allow_live=True, notifier=pager, catch_up=catch_up, run_kind="cron"
    )
    return seen["known"], out


def _new(out) -> set[str]:
    return {s.accession for s in out.skips.new}


def _old_full_roster_rule(run_dir) -> set[str]:
    """What the FIRST Q18 build read as known: the run-level accessions of the newest finished, live,
    evaluated, FULL-ROSTER artifact. Kept here only to show the sequence it got wrong."""
    for doc in cron_run_log.list_run_logs(base_dir=run_dir):
        if (
            doc.get("mode") == "live"
            and doc.get("ingest_skips_evaluated")
            and cron_run_log.is_finished(doc)
            and cron_run_log.is_full_roster(doc)
        ):
            return set(doc["ingest_skips"][FORM4_UNPARSEABLE]["accessions"])
    return set()


def test_a_filing_FIRST_skipped_in_a_catch_up_is_not_re_printed_at_the_next_full_pass(
    pager, monkeypatch, tmp_path
):
    """Full pass (no skips) -> a catch-up that skips thesis A and walks B, where a filing F2 first fails
    -> the next full pass (F2 still fails). F2 prints exactly once, at the catch-up."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok = _names()
    f2 = "0000950144-97-007016"

    known1, out1 = _run(
        monkeypatch,
        pager,
        [covered(ta, asof=_D1, healthy=a_ok), covered(tb, asof=_D1, healthy=b_ok)],
        asof=_D1,
    )
    assert known1 is None and _new(out1) == set()

    known2, out2 = _run(
        monkeypatch,
        pager,
        [skipped(ta), _with_skips(covered(tb, asof=_D2, healthy=b_ok), f2)],
        asof=_D2,
        catch_up=True,
    )
    assert skip_key(FORM4_UNPARSEABLE, f2) not in known2 and _new(out2) == {f2}  # printed here

    # BEFORE (the full-roster rule): the catch-up is read past, so F2 would print again tonight
    assert f2 not in _old_full_roster_rule(tmp_path / "cron_runs")

    known3, out3 = _run(
        monkeypatch,
        pager,
        [
            covered(ta, asof=_D3, healthy=a_ok),
            _with_skips(covered(tb, asof=_D3, healthy=b_ok), f2),
        ],
        asof=_D3,
    )
    # AFTER (the carry-forward): B's entry comes from the catch-up that covered it — F2 is known
    assert skip_key(FORM4_UNPARSEABLE, f2) in known3
    assert _new(out3) == set()
    f4 = next(k for k in out3.skips.kinds if k.kind == FORM4_UNPARSEABLE)
    assert (f4.total, f4.known, f4.previous_total, f4.delta) == (1, 1, 1, 0)


def test_a_thesis_the_catch_up_SKIPPED_keeps_its_filings_and_does_not_re_print(pager, monkeypatch):
    """A's filing F1 fails on the full pass; the catch-up skips A (nothing walked); the next full pass walks
    A again. F1 is known on BOTH later passes — carried from the last pass that covered A."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok = _names()
    f1 = "0001094891-03-000080"

    _, out1 = _run(
        monkeypatch,
        pager,
        [_with_skips(covered(ta, asof=_D1, healthy=a_ok), f1), covered(tb, asof=_D1, healthy=b_ok)],
        asof=_D1,
    )
    assert _new(out1) == {f1}

    known2, out2 = _run(
        monkeypatch,
        pager,
        [skipped(ta), covered(tb, asof=_D2, healthy=b_ok)],
        asof=_D2,
        catch_up=True,
    )
    assert skip_key(FORM4_UNPARSEABLE, f1) in known2 and _new(out2) == set()

    known3, out3 = _run(
        monkeypatch,
        pager,
        [_with_skips(covered(ta, asof=_D3, healthy=a_ok), f1), covered(tb, asof=_D3, healthy=b_ok)],
        asof=_D3,
    )
    assert skip_key(FORM4_UNPARSEABLE, f1) in known3 and _new(out3) == set()


def _strip_attribution(run_dir) -> None:
    """Rewrite every artifact the way the FIRST Q18 build wrote it: the run-level block only, no per-thesis
    ``ingest_skips``."""
    for p in run_dir.glob("*.json"):
        doc = json.loads(p.read_text(encoding="utf-8"))
        for t in doc.get("theses") or []:
            t.pop("ingest_skips", None)
        p.write_text(json.dumps(doc), encoding="utf-8")


def test_an_artifact_WITHOUT_per_thesis_attribution_falls_back_to_EVERYTHING_NEW_once(
    pager, monkeypatch, tmp_path
):
    """No silent mute from an old artifact: when no carried entry carries the per-thesis list, the baseline
    is None ("nothing known") and every skip prints once more; the pass after that has it again."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok = _names()
    f1 = "0000950144-97-007016"
    _run(
        monkeypatch,
        pager,
        [_with_skips(covered(ta, asof=_D1, healthy=a_ok), f1), covered(tb, asof=_D1, healthy=b_ok)],
        asof=_D1,
    )
    _strip_attribution(tmp_path / "cron_runs")

    known2, out2 = _run(
        monkeypatch,
        pager,
        [_with_skips(covered(ta, asof=_D2, healthy=a_ok), f1), covered(tb, asof=_D2, healthy=b_ok)],
        asof=_D2,
    )
    assert known2 is None and _new(out2) == {f1}  # printed once more, never muted

    known3, out3 = _run(
        monkeypatch,
        pager,
        [_with_skips(covered(ta, asof=_D3, healthy=a_ok), f1), covered(tb, asof=_D3, healthy=b_ok)],
        asof=_D3,
    )
    assert skip_key(FORM4_UNPARSEABLE, f1) in known3 and _new(out3) == set()


def test_one_thesis_carried_from_an_old_artifact_prints_ITS_filings_once(
    pager, monkeypatch, tmp_path
):
    """Mixed: the full pass is old-format; the catch-up (new format) covers B and skips A; at the next full
    pass B's filing is known (from the catch-up) while A's — carried from the old artifact, which never
    attributed it — reads as new and prints once."""
    ta, tb = uuid.uuid4(), uuid.uuid4()
    a_ok, b_ok = _names()
    fa, fb = "0000950144-97-007016", "0000950103-26-005722"
    _run(
        monkeypatch,
        pager,
        [
            _with_skips(covered(ta, asof=_D1, healthy=a_ok), fa),
            _with_skips(covered(tb, asof=_D1, healthy=b_ok), fb),
        ],
        asof=_D1,
    )
    _strip_attribution(tmp_path / "cron_runs")
    _run(
        monkeypatch,
        pager,
        [skipped(ta), _with_skips(covered(tb, asof=_D2, healthy=b_ok), fb)],
        asof=_D2,
        catch_up=True,
    )

    known3, out3 = _run(
        monkeypatch,
        pager,
        [
            _with_skips(covered(ta, asof=_D3, healthy=a_ok), fa),
            _with_skips(covered(tb, asof=_D3, healthy=b_ok), fb),
        ],
        asof=_D3,
    )
    assert known3 == {skip_key(FORM4_UNPARSEABLE, fb)}
    assert _new(out3) == {fa}

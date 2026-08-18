"""Band 03 S5 — the SC 13D activist-stake CONVICTION trigger (pure score + the master switch + the
full-pipeline standing guard). No DB: the pure ``score`` takes fact rows directly; ``detect`` is
exercised through a stub pit; the two-key gate runs the REAL pipeline (``assemble_from_pit`` ->
every registered detector) over a COMPLETE in-memory point-in-time fake.

The REAL instances (test-honesty — cited filings, fixed timestamps, no fitting):

- the GOLDEN: atai's ORIGINAL SC 13D about COMPASS Pathways (CMPS, an answer-key basket member) —
  accession 0001193125-21-171001, filed 2021-05-24, ATAI Life Sciences B.V. (CIK 0001840904);
- the fork-4 proof: the real SCHEDULE 13D/A accession 0001140361-26-005810 (filed 2026-02-17,
  AtaiBeckley Inc., 4.96% — a sell-down BELOW 5%) must NOT fire a fresh CORE at a 2026 asof;
- the negative: MindMed's real tape holds ONLY 13G-family rows — the detector fires nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State
from ingest.edgar.submissions import SCHEDULE13_D_FORMS
from pipeline.core import assemble_from_pit
from signals import activist_stake
from signals.base import window_prices
from tests.calls.factories import ASOF, SID, make_thesis

_ON = DEFAULT_CONFIG.model_copy(update={"activist_stake_enabled": True})
_LIVENESS = DEFAULT_CONFIG.activist_13d_liveness_days
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)

# The real CMPS × atai episode (cited; see the module docstring).
_CMPS_13D_ACC = "0001193125-21-171001"
_CMPS_13DA_ACC = "0001140361-26-005810"


def _fact(
    accession="ACC-13D",
    form="SC 13D",
    filed=ASOF,
    filer_cik="0001111111",
    filer_name="Activist Fund LP",
    pct=None,
):
    return {
        "accession": accession,
        "form": form,
        "filer_cik": filer_cik,
        "filer_name": filer_name,
        "pct_owned": pct,
        "filed": filed,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": filed,
    }


def _real_cmps_original(filed=date(2021, 5, 24)):
    return _fact(
        accession=_CMPS_13D_ACC,
        form="SC 13D",
        filed=filed,
        filer_cik="0001840904",
        filer_name="ATAI Life Sciences B.V.",
    )


def _real_cmps_exit_amendment():
    return _fact(
        accession=_CMPS_13DA_ACC,
        form="SCHEDULE 13D/A",
        filed=date(2026, 2, 17),
        filer_cik="0002081043",
        filer_name="AtaiBeckley Inc.",
        pct=4.96,
    )


# --- the pure score ---------------------------------------------------------------------------------


def test_13d_original_fires_a_core_conviction_from_the_dials():
    e = activist_stake.score([_fact(pct=5.6)], SID, ASOF)
    assert e is not None and e.fired
    assert e.role is Role.ENTRY_TRIGGER and e.kind is Kind.ACTIVIST_STAKE
    assert e.grade is Grade.CORE  # fixed in the detector — structural, capital-committed intent
    assert e.score == DEFAULT_CONFIG.activist_13d_score  # the [PROPOSED] dial, never hardcoded
    assert e.alpha_liveness_days == _LIVENESS
    assert e.asof == ASOF  # fire date = the ORIGINAL's filing date
    assert "SC 13D" in e.label and "Activist Fund LP" in e.label and "5.6% of class" in e.label
    (p,) = e.provenance
    assert p.source == "schedule13" and p.ref == "ACC-13D"
    assert p.detail["form"] == "SC 13D" and p.detail["filer_name"] == "Activist Fund LP"
    assert p.detail["index_url"].endswith("-index.htm")  # checkable source (#6)


def test_renamed_era_schedule_13d_fires_identically():
    """The rename trap's detector half: a post-2024-12 "SCHEDULE 13D" original is the same event."""
    e = activist_stake.score([_fact(form="SCHEDULE 13D")], SID, ASOF)
    assert e is not None and e.grade is Grade.CORE and "SCHEDULE 13D" in e.label


def test_13g_family_never_fires():
    """FORK 3 (operator-confirmed): 13G = passive — mostly index-fund plumbing (measured ~2
    originals/yr/name). It stays ON the tape (#9) but fires NOTHING, either naming era."""
    for form in ("SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"):
        assert activist_stake.score([_fact(form=form)], SID, ASOF) is None, form


def test_amendment_never_fires_or_reanchors():
    """FORK 4 (operator-confirmed): a 13D/A is direction-blind (increase, sell-down, and exit file
    identically) — an amendment alone fires nothing, and a fresh /A must NOT re-anchor a stale
    original back to life."""
    for form in ("SC 13D/A", "SCHEDULE 13D/A"):
        assert activist_stake.score([_fact(form=form)], SID, ASOF) is None, form
    stale_original = _fact(filed=ASOF - timedelta(days=_LIVENESS + 40))
    fresh_amendment = _fact(accession="ACC-A", form="SC 13D/A", filed=ASOF)
    assert activist_stake.score([stale_original, fresh_amendment], SID, ASOF) is None


def test_real_cmps_atai_golden_fires_on_the_original():
    """THE GOLDEN: the real atai SC 13D about CMPS, scored inside its window (asof 2021-06-15) —
    a CORE Key-1 conviction with the real accession + filer in the provenance."""
    e = activist_stake.score([_real_cmps_original()], SID, date(2021, 6, 15))
    assert e is not None and e.fired and e.grade is Grade.CORE
    assert e.asof == date(2021, 5, 24)
    assert "ATAI Life Sciences B.V." in e.label
    assert [p.ref for p in e.provenance] == [_CMPS_13D_ACC]


def test_real_cmps_exit_amendment_must_not_fire_today():
    """FORK 4 ON THE REAL DATA: CMPS's tape today = the 2021 original (long stale) + the real
    2026-02-17 SCHEDULE 13D/A reporting 4.96% — a sell-down BELOW 5%. Latest-wins-including-/A
    would fire a fresh CORE on an exit filing; the original-anchor rule fires NOTHING."""
    tape = [_real_cmps_original(), _real_cmps_exit_amendment()]
    assert activist_stake.score(tape, SID, date(2026, 8, 18)) is None


def test_real_mindmed_13g_tape_fires_nothing():
    """The negative instance: MindMed (CIK 1813814) has NO 13D in its entire submissions history —
    only 13G-family rows (verified live 2026-08-18; the famous FCM MM Holdings campaign was DFAN14A
    proxy material, never a 13D). Two real rows from that tape fire nothing."""
    # real accessions + forms + dates from the live tape; the filers are irrelevant to the fire
    # decision (form type is the whole rule) and unasserted here, so they stay None — no fabricated
    # identity on a real accession.
    tape = [
        _fact(
            accession="0000938206-26-000012",
            form="SCHEDULE 13G",
            filed=date(2026, 2, 17),
            filer_cik=None,
            filer_name=None,
        ),
        _fact(
            accession="0002012383-24-003562",
            form="SC 13G",
            filed=date(2024, 11, 8),
            filer_cik=None,
            filer_name=None,
        ),
    ]
    assert activist_stake.score(tape, SID, date(2026, 8, 18)) is None


def test_liveness_is_inclusive_at_the_horizon_and_expires():
    at_horizon = _fact(filed=ASOF - timedelta(days=_LIVENESS))
    assert activist_stake.score([at_horizon], SID, ASOF) is not None
    stale = _fact(filed=ASOF - timedelta(days=_LIVENESS + 1))
    assert activist_stake.score([stale], SID, ASOF) is None


def test_future_filed_fact_is_invisible_no_lookahead():
    """A pure-score honesty re-check (the as-of read already guarantees it live): a filing dated
    after asof contributes nothing."""
    assert activist_stake.score([_fact(filed=ASOF + timedelta(days=1))], SID, ASOF) is None


def test_most_recent_live_original_wins_and_the_episode_rides_provenance():
    """Two live originals: the most recent anchors the fire (accession tiebreak deterministic);
    the fired EPISODE — the anchor + the amendments from it forward — rides the provenance (#6),
    while the older separate episode does not."""
    old = _fact(accession="ACC-OLD", filed=ASOF - timedelta(days=100))
    new = _fact(accession="ACC-NEW", filed=ASOF - timedelta(days=10))
    amendment = _fact(accession="ACC-NEW-A", form="SC 13D/A", filed=ASOF - timedelta(days=5))
    e = activist_stake.score([amendment, old, new], SID, ASOF)
    assert e is not None and e.asof == ASOF - timedelta(days=10)
    assert [p.ref for p in e.provenance] == ["ACC-NEW", "ACC-NEW-A"]  # sorted, episode-scoped


def test_master_switch_off_detect_emits_nothing_score_stays_ungated():
    """INERT-FIRST: with the live DEFAULT_CONFIG (switch OFF) ``detect`` no-ops — it never even
    reads the pit — so no activist-stake event can reach a live card; the pure ``score`` above
    stays fully testable. Flipping the switch (model_copy — the replay force-on path) fires."""
    pit = SimpleNamespace(
        activist_stake_facts=lambda sid: (_ for _ in ()).throw(AssertionError("pit read while OFF"))
    )
    assert activist_stake.detect(pit, SID, ASOF, DEFAULT_CONFIG) is None

    pit_on = SimpleNamespace(activist_stake_facts=lambda sid: [_fact()])
    e = activist_stake.detect(pit_on, SID, ASOF, _ON)
    assert e is not None and e.kind is Kind.ACTIVIST_STAKE


def test_activist_kind_inherits_conviction_membership():
    """The wiring claim: Kind.ACTIVIST_STAKE ∈ conviction_kinds (co-location arming) AND ∈
    own_conviction_kinds (the is_own ranking) — the derived property inherits it automatically,
    with zero assembler edits."""
    assert Kind.ACTIVIST_STAKE in DEFAULT_CONFIG.conviction_kinds
    assert Kind.ACTIVIST_STAKE in DEFAULT_CONFIG.own_conviction_kinds


def test_detector_13d_forms_match_the_ingest_constant():
    """The drift pin: the detector keeps the 13D-family strings as a LOCAL literal (the share_creep
    no-ingest-import convention) — this asserts it can never drift from the ingest's set."""
    assert activist_stake._13D_FORMS == SCHEDULE13_D_FORMS


# --- the two-key gate + the standing guard (the REAL pipeline over a COMPLETE fake) -----------------

# A flat 50.0 base long enough for the 52-week detectors, then one 65.0 close on 3x volume — the
# breakout confirmation bar (the dearm-replay arc's shape). Without the spike the tape is flat and
# NO confirmation exists.
_FLAT = [50.0] * 260
_VOLS = [1000.0] * 260


def _bars(spike: bool):
    closes = _FLAT + ([65.0] if spike else [])
    vols = _VOLS + ([3000.0] if spike else [])
    n = len(closes)
    return [
        {"d": ASOF - timedelta(days=(n - 1 - i)), "close": c, "high": c, "low": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


class _PIT:
    """A COMPLETE ``SignalPointInTimeData`` fake — every protocol accessor implemented. THE
    STANDING GUARD (the corporate_risk-flip lesson): ``assemble_from_pit`` with the switch ON runs
    EVERY registered detector against this fake, so a protocol accessor missing from a test double
    AttributeErrors HERE in CI today, not at the future flip."""

    def __init__(self, asof: date, *, stakes=(), bars=()) -> None:
        self.asof = asof
        self.known_at = _KNOWN
        self.tenant_id = DEFAULT_TENANT_ID
        self._stakes = list(stakes)
        self._bars = list(bars)

    def insider_txns(self, security_id: UUID) -> list[dict]:
        return []

    def price_history(self, security_id: UUID, lookback_days: int | None = None) -> list[dict]:
        rows = [dict(b) for b in self._bars if b["d"] <= self.asof]
        return window_prices(rows, self.asof, lookback_days)

    def dilution_facts(self, security_id: UUID) -> list[dict]:
        return []

    def catalyst_facts(self, security_id: UUID) -> list[dict]:
        return []

    def fundamentals_facts(self, security_id: UUID) -> list[dict]:
        return []

    def corporate_event_facts(self, security_id: UUID) -> list[dict]:
        return []

    def activist_stake_facts(self, security_id: UUID) -> list[dict]:
        return [dict(f) for f in self._stakes if f["valid_from"] <= self.asof]

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict]:
        return []

    def security_name(self, security_id: UUID) -> str | None:
        return None


def test_two_key_gate_a_13d_alone_warms_and_arms_only_with_a_colocated_breakout():
    """The two-key gate through the REAL pipeline: a live 13D conviction alone WARMS (never arms);
    with a co-located volume-backed breakout it ARMS. And the INERT proof: the same stake tape
    under the live DEFAULT_CONFIG (switch OFF) contributes nothing — flat tape reads INCUBATING,
    breakout tape reads exactly what a no-stake basket reads."""
    thesis = make_thesis()
    stakes = [_fact(filed=ASOF - timedelta(days=10))]

    # Key 1 alone — a live 13D on a flat tape: WARMING, never armed.
    warm = assemble_from_pit(_PIT(ASOF, stakes=stakes, bars=_bars(spike=False)), thesis, ASOF, _ON)
    assert warm.state is State.WARMING
    assert Kind.ACTIVIST_STAKE in {t.kind for t in warm.triggers_fired}

    # Key 1 + a co-located Key 2 (the breakout bar): ARMED.
    armed = assemble_from_pit(_PIT(ASOF, stakes=stakes, bars=_bars(spike=True)), thesis, ASOF, _ON)
    assert armed.state is State.ARMED
    assert Kind.ACTIVIST_STAKE in {t.kind for t in armed.triggers_fired}

    # INERT under the live DEFAULT_CONFIG: the stake tape changes NOTHING while the switch is off —
    # the flat card reads INCUBATING (no trigger at all), and the breakout card equals the card a
    # stake-free basket produces (state AND fired-trigger kinds identical; goldens byte-safe).
    off_flat = assemble_from_pit(
        _PIT(ASOF, stakes=stakes, bars=_bars(spike=False)), thesis, ASOF, DEFAULT_CONFIG
    )
    assert off_flat.state is State.INCUBATING
    off_spike = assemble_from_pit(
        _PIT(ASOF, stakes=stakes, bars=_bars(spike=True)), thesis, ASOF, DEFAULT_CONFIG
    )
    no_stakes = assemble_from_pit(
        _PIT(ASOF, stakes=(), bars=_bars(spike=True)), thesis, ASOF, DEFAULT_CONFIG
    )
    assert off_spike.state is no_stakes.state
    assert {t.kind for t in off_spike.triggers_fired} == {t.kind for t in no_stakes.triggers_fired}
    assert Kind.ACTIVIST_STAKE not in {t.kind for t in off_spike.triggers_fired}

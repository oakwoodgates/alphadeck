"""Q13 push policy (operator 2026-10-17: O3 + O4) on REAL recorded calls, through the real cron wiring.

Every card here is a recorded prod call-of-record (``tests/fixtures/push_policy/real_cards.json`` — extracted
READ-ONLY, values verbatim, the projection named in its ``provenance``). Each test seeds the REAL prior row
into ``calls``, stubs only the assembler to return the REAL next row's card, and runs ``daily.run_daily`` — so
the baseline read, the event build, the notify, the record and the digest are the production path. The
outcomes below are what those real nights produce under the new policy; none was chosen to make a test pass.

The cases (MEASURED in the 2026-10-16 audit, A2): the two downgrades that used to read as new arms (DAT
2026-09-23, Nuclear Buildout 2026-10-07), a real upgrade (Nuclear Buildout 2026-10-06), a real opposite-
direction move (DAT 2026-09-24), the 2026-10-16 nightly pass whose already-armed theses gained names, and
the 2026-10-12 boot catch-up recorded late the next afternoon. For the digest's first-time / re-arm split the
REAL earlier row each re-arm was armed on is seeded too (the fixture's ``rearm_history`` names them, and
names which arms are first-time on the full record), so the split each test asserts is the record's own.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.settings import get_settings
from notify import DigestEvent, SlackNotifier
from pipeline import daily, push_policy
from repositories import calls_repo, thesis_repo

_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "push_policy" / "real_cards.json"
    ).read_text(encoding="utf-8")
)
_NY = ZoneInfo("America/New_York")


def _row(seq: int) -> dict:
    return _FIXTURE["rows"][str(seq)]


def _card(seq: int) -> CallCard:
    return CallCard.model_validate(_row(seq)["card"])


def _sid(ticker: str) -> uuid.UUID:
    (sid,) = [s for s, t in _FIXTURE["tickers"].items() if t == ticker]
    return uuid.UUID(sid)


class _Capture:
    """A complete Notifier double: every surface recorded separately."""

    def __init__(self):
        self.events: list = []
        self.digests: list[DigestEvent] = []

    def notify(self, event):
        self.events.append(event)

    def notify_digest(self, event):
        self.digests.append(event)

    def notify_health(self, event):  # pragma: no cover — run_daily never pages health
        pass

    def notify_spac_status(self, event):  # pragma: no cover — the radar leg is not run here
        pass


def _seed(db, *pairs: tuple[int, int], history: tuple[int, ...] = ()) -> dict[uuid.UUID, CallCard]:
    """Seed each pair's thesis (its REAL id + name), the REAL prior call-of-record, and the master tickers;
    return thesis_id -> the REAL next card the stubbed assembler will hand the cron. ``history`` = the REAL
    earlier rows a re-arm was armed on (the fixture's ``rearm_history`` names them), written first, in their
    recorded order, so the first-time / re-arm read sees the record as it was."""
    with db.cursor() as cur:
        for sid, ticker in _FIXTURE["tickers"].items():
            cur.execute(
                "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
                (sid, DEFAULT_TENANT_ID, ticker, "2026-01-01"),
            )
        for prior_seq, _ in pairs:
            card = _card(prior_seq)
            cur.execute(
                "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
                (card.thesis_id, DEFAULT_TENANT_ID, _row(prior_seq)["thesis"], "n"),
            )
    db.commit()
    for seq in sorted([*history, *(p for p, _ in pairs)]):  # the real write order
        calls_repo.append(db, _card(seq), DEFAULT_TENANT_ID)
    db.commit()
    return {_card(n).thesis_id: _card(n) for _, n in pairs}


def _run(db, monkeypatch, nxt: dict[uuid.UUID, CallCard], *, asof: date, now: datetime, cap):
    """One real `run_daily` pass: ingest stubbed (no network), the assembler returns the recorded card, the
    market clock pinned to the instant the real pass ran (so N1's timing label is the real one)."""
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])
    monkeypatch.setattr(daily, "call_for_thesis", lambda conn, thid, a, **k: nxt[thid])
    monkeypatch.setattr(daily, "market_now", lambda: now)
    monkeypatch.setattr(daily, "market_tz", lambda: _NY)
    return {r.thesis_id: r for r in daily.run_daily(db, asof=asof, allow_live=True, notifier=cap)}


# --- O3: the two downgrades that used to read as new arms are QUIET now -----------------------------------


@pytest.mark.parametrize(
    ("prior_seq", "next_seq", "asof", "ran_at", "text"),
    [
        (
            1168,
            1180,
            date(2026, 9, 23),
            datetime(2026, 9, 23, 22, 36, tzinfo=_NY),
            "Digital Asset Treasury Complex — still armed, weaker: core → flip; 4 → 2 names "
            "(core_entry → starter_entry)\n"
            "no longer armed: NAKA (structural break), ASST (fast breakdown); "
            "Momentum-only — not yet volume-confirmed.",
        ),
        (
            1293,
            1305,
            date(2026, 10, 7),
            datetime(2026, 10, 7, 22, 37, tzinfo=_NY),
            "Nuclear Buildout — still armed, weaker: core → flip; 4 → 3 names "
            "(core_entry → starter_entry)\n"
            "no longer armed: GHM (structural break, fast breakdown); "
            "Momentum-only — not yet volume-confirmed.",
        ),
    ],
)
def test_a_real_downgrade_while_armed_is_a_QUIET_push(
    db, monkeypatch, prior_seq, next_seq, asof, ran_at, text
):
    """DAT 2026-09-23 and Nuclear Buildout 2026-10-07 pushed the loud red "ARMED" header although the setup
    got WEAKER (core -> flip, fewer names). Now: a quiet, honest push naming what moved and the reason the
    card carries (which names left, on which de-arm signal). No new name armed, so no digest either.
    """
    nxt = _seed(db, (prior_seq, next_seq))
    cap = _Capture()
    by = _run(db, monkeypatch, nxt, asof=asof, now=ran_at, cap=cap)

    (evt,) = cap.events
    assert (evt.from_state, evt.to_state) == ("armed", "armed")
    assert evt.push_kind == "downgrade" and not evt.loud
    assert SlackNotifier._format(evt) == text  # no 🔴, no "ARMED", no capitals
    assert cap.digests == []  # nothing newly armed: no digest, never a "nothing new" ping
    tid = _card(next_seq).thesis_id
    assert by[tid].recorded is True and len(calls_repo.list_for_thesis(db, tid)) == 2


def test_a_real_upgrade_while_armed_stays_LOUD_and_its_names_are_not_repeated_in_the_digest(
    db, monkeypatch
):
    """Nuclear Buildout 2026-10-06: starter_entry -> core_entry, 2 -> 4 armed names — a genuine strengthening.
    LOUD, the ARMED style with an explicit up marker and what improved; the push lists every armed name, so
    the two that newly armed are NOT repeated in the digest (a suppressed duplicate)."""
    nxt = _seed(db, (1281, 1293))
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 6),
        now=datetime(2026, 10, 6, 22, 38, tzinfo=_NY),
        cap=cap,
    )

    (evt,) = cap.events
    assert evt.push_kind == "upgrade" and evt.loud
    assert SlackNotifier._format(evt) == (
        "🔴 Nuclear Buildout — ARMED ↑ now core, +2 names (starter_entry → core_entry)\n"
        "VST, GHM, CEG, GEV armed\n"
        "VST insider buy, breakout · GHM catalyst, breakout · CEG catalyst, breakout · "
        "GEV catalyst, breakout"
    )
    assert len(push_policy.newly_armed(_card(1281), _card(1293))) == 2  # two names DID newly arm …
    assert cap.digests == []  # … and the loud push already listed them


def test_a_real_opposite_direction_move_is_QUIET_with_both_sides_stated(db, monkeypatch):
    """DAT 2026-09-24: the grade rose (flip -> core) but armed names fell (2 -> 1). A weaker side never rides a
    loud header: QUIET, both changes stated. The name that newly armed (NAKA re-arming core — it was armed on
    the 09-22 row, seq 1168, and de-armed 09-23) was not on any thesis-level push, so it reaches the operator
    through the digest, marked a re-arm."""
    nxt = _seed(db, (1180, 1192), history=(1168,))
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 9, 24),
        now=datetime(2026, 9, 24, 22, 36, tzinfo=_NY),
        cap=cap,
    )

    (evt,) = cap.events
    assert evt.push_kind == "mixed" and not evt.loud
    assert SlackNotifier._format(evt) == (
        "Digital Asset Treasury Complex — still armed, mixed: flip → core; 2 → 1 names "
        "(starter_entry → core_entry)\n"
        "no longer armed: RIOT (fast breakdown), MARA (fast breakdown); "
        "The market is confirming (a volume-backed breakout)."
    )
    (digest,) = cap.digests
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\n"
        "Digital Asset Treasury Complex: NAKA (re-arm) insider buy, breakout"
    )


# --- O4: the nightly digest on real nights ----------------------------------------------------------------


def test_the_real_2026_10_16_pass_sends_ONE_digest_grouped_by_thesis(db, monkeypatch):
    """The 2026-10-16 nightly pass: Modern Defense (armed, verdict held) gained LMT + HONA, The Everything
    Exchange (armed, verdict held) gained FIS — no thesis-level push for either, so before Q13 these three
    arms pushed nothing. Navy Shipbuilding Rebuild ENTERED Armed the same pass: its loud push (unchanged)
    lists its own names, and a thesis that was not already Armed is never digest news. ONE digest message for
    the pass, grouped by thesis, in the compact #309 shape. LMT and FIS are FIRST-TIME arms; HONA is a RE-ARM
    (armed on Modern Defense's 09-22 row, seq 1170), so it is marked and follows LMT."""
    nxt = _seed(db, (1404, 1416), (1410, 1422), (1406, 1418), history=(1170,))
    cap = _Capture()
    ran = datetime(2026, 10, 16, 22, 36, tzinfo=_NY)
    _run(db, monkeypatch, nxt, asof=date(2026, 10, 16), now=ran, cap=cap)

    (evt,) = cap.events  # only Navy moved state/verdict
    assert evt.thesis_name == "Navy Shipbuilding Rebuild" and evt.push_kind == "armed" and evt.loud
    assert SlackNotifier._format(evt).startswith(
        "🔴 Navy Shipbuilding Rebuild — ARMED (warming → armed)"
    )
    (digest,) = cap.digests
    assert digest.timing == "current" and digest.name_count == 3
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\n"
        "Modern Defense: LMT catalyst, breakout · HONA (re-arm) insider buy, breakout\n"
        "The Everything Exchange: FIS insider buy, breakout"
    )

    # a same-day SECOND pass (an Admin "Run daily now" after the nightly): the baseline is the night's own
    # row (F2), the cards are unchanged — no row, no push, no second digest
    cap2 = _Capture()
    _run(db, monkeypatch, nxt, asof=date(2026, 10, 16), now=ran, cap=cap2)
    assert cap2.events == [] and cap2.digests == []


def test_the_real_late_catch_up_of_2026_10_12_labels_its_digest(db, monkeypatch):
    """The 2026-10-12 pass died mid-run; the boot catch-up recorded the remaining theses at 17:04-17:13 ET the
    NEXT day — after the night's on-time window closed. Three already-armed theses gained a name in it
    (Nuclear Buildout PCG, Rainbow Rush CYCN, The Everything Exchange MELI). The digest carries N1's label so
    a catch-up's arms are never read as tonight's. CYCN is a re-arm (armed on Rainbow Rush's 10-02 row, seq
    1270); PCG and MELI are first-time arms."""
    nxt = _seed(db, (1329, 1371), (1330, 1372), (1332, 1374), history=(1270,))
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 12),
        now=datetime(2026, 10, 13, 17, 8, tzinfo=_NY),
        cap=cap,
    )

    assert cap.events == []  # no thesis moved state or verdict
    (digest,) = cap.digests
    assert digest.timing == "late"
    text = SlackNotifier._format_digest(digest)
    assert text.splitlines()[0] == (
        "Newly armed in already-armed theses (asof 2026-10-12, recorded late)"
    )
    assert [t.thesis_name for t in digest.theses] == [
        "Nuclear Buildout",
        "Rainbow Rush",
        "The Everything Exchange",
    ]
    assert [[n.ticker for n in t.names] for t in digest.theses] == [["PCG"], [], ["MELI"]]
    assert [[n.ticker for n in t.rearms] for t in digest.theses] == [[], ["CYCN"], []]
    assert "Rainbow Rush: CYCN (re-arm) activist, breakout" in text.splitlines()


def test_a_real_flapping_nuclear_name_is_a_RE_ARM_and_the_first_time_arm_comes_FIRST(
    db, monkeypatch
):
    """Nuclear Buildout 2026-08-19 (seq 720 -> 729, verdict held): UUUU, J and URG newly armed. UUUU and J had
    armed in this thesis before and de-armed (UUUU on the 08-17 row, seq 711; J on the 08-14 row, seq 693) —
    flapping names; URG had never been an armed member of Nuclear Buildout on any earlier honest row. The card
    RANKS the re-arms first (UUUU, J, URG); the digest lists the first-time arm first, then the marked
    re-arms, each group in the card's order."""
    nxt = _seed(db, (720, 729), history=(693, 711))
    assert [m.security_id for m in push_policy.newly_armed(_card(720), _card(729))] == [
        _sid("UUUU"),
        _sid("J"),
        _sid("URG"),
    ]  # the card's own rank order: re-arms ahead of the first-time arm
    cap = _Capture()
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 8, 19),
        now=datetime(2026, 8, 19, 23, 1, tzinfo=_NY),
        cap=cap,
    )

    assert cap.events == []  # core_entry held: no thesis-level push
    (digest,) = cap.digests
    (line,) = digest.theses
    assert [n.ticker for n in line.names] == ["URG"] and [n.ticker for n in line.rearms] == [
        "UUUU",
        "J",
    ]
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\n"
        "Nuclear Buildout: URG catalyst, breakout · UUUU (re-arm) insider buy, catalyst, breakout · "
        "J (re-arm) insider buy, breakout"
    )


def test_a_failed_history_read_labels_every_name_FIRST_TIME_and_drops_none(db, monkeypatch, caplog):
    """The re-arm read is fail-open: if it raises, every name reads first-time (unmarked), none is dropped,
    and the failure is logged."""
    import logging

    nxt = _seed(db, (1404, 1416), history=(1170,))

    def _boom(*a, **k):
        raise RuntimeError("history read down")

    monkeypatch.setattr(push_policy, "ever_armed", _boom)
    cap = _Capture()
    with caplog.at_level(logging.WARNING):
        _run(
            db,
            monkeypatch,
            nxt,
            asof=date(2026, 10, 16),
            now=datetime(2026, 10, 16, 22, 36, tzinfo=_NY),
            cap=cap,
        )

    (digest,) = cap.digests
    (line,) = digest.theses
    assert [n.ticker for n in line.names] == ["LMT", "HONA"] and line.rearms == ()
    assert any("re-arm history read failed" in r.getMessage() for r in caplog.records)


def test_the_re_arm_read_IS_one_query_for_the_pass(db, monkeypatch):
    nxt = _seed(db, (1404, 1416), (1410, 1422), history=(1170,))
    calls: list[set] = []
    real = push_policy.ever_armed

    def _spy(conn, thesis_ids, *, asof):
        calls.append(set(thesis_ids))
        return real(conn, thesis_ids, asof=asof)

    monkeypatch.setattr(push_policy, "ever_armed", _spy)
    _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 16),
        now=datetime(2026, 10, 16, 22, 36, tzinfo=_NY),
        cap=_Capture(),
    )
    assert calls == [{_card(1416).thesis_id, _card(1422).thesis_id}]


# --- fail-open: a digest fault never costs a record or another push -----------------------------------------


def test_a_raising_digest_delivery_costs_nothing(db, monkeypatch):
    nxt = _seed(db, (1404, 1416), (1410, 1422))

    class _Boom(_Capture):
        def notify_digest(self, event):
            raise RuntimeError("slack is down")

    cap = _Boom()
    by = _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 16),
        now=datetime(2026, 10, 16, 22, 36, tzinfo=_NY),
        cap=cap,
    )

    assert all(r.recorded is True and r.error is None for r in by.values())  # both records landed


def test_a_failed_identity_read_still_LISTS_the_name_by_its_security_id(db, monkeypatch):
    """The digest's master read failing degrades the line, never drops or counts the name (#9): with no
    ticker and no company name to show, the name is listed by the one identity left, its security id.
    """
    nxt = _seed(db, (1410, 1422))

    def _boom(*a, **k):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(push_policy.master, "tickers_for", _boom)
    cap = _Capture()
    by = _run(
        db,
        monkeypatch,
        nxt,
        asof=date(2026, 10, 16),
        now=datetime(2026, 10, 16, 22, 36, tzinfo=_NY),
        cap=cap,
    )

    (digest,) = cap.digests
    assert SlackNotifier._format_digest(digest) == (
        "Newly armed in already-armed theses\n"
        f"The Everything Exchange: {_sid('FIS')} (no ticker) insider buy, breakout"
    )
    assert next(iter(by.values())).recorded is True


# --- NAMES, NEVER COUNTS: a name with no ticker is listed by its company name ------------------------------
# A CONTRACT test with direct inputs, not a real instance: prod holds NO ticker-less name to test on (MEASURED
# 2026-10-17: 0 of the 174 security ids ever armed and 0 of the 10,630 security_master rows lack a ticker).
# The company "Acme Uranium Corp" and its ids below are synthetic placeholders and claim nothing about the
# record; the real-name tests above cover every format with real data.


def test_a_ticker_less_name_is_LISTED_by_its_company_name_in_the_digest_and_the_reason_line(db):
    acme, other = uuid.uuid4(), uuid.uuid4()
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) VALUES "
            "(%s, %s, NULL, 'Acme Uranium Corp', '2026-01-01'), (%s, %s, 'OTHR', 'Other Inc', "
            "'2026-01-01')",
            (acme, DEFAULT_TENANT_ID, other, DEFAULT_TENANT_ID),
        )
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'Contract', 'n')",
            (tid, DEFAULT_TENANT_ID),
        )
    db.commit()
    thesis = thesis_repo.get(db, tid)
    template = _card(
        1416
    )  # a real armed card's shape; its members replaced by the two direct inputs

    def armed_with(*sids):
        members = [
            m.model_copy(update={"security_id": s}) for m, s in zip(template.armed_members, sids)
        ]
        return template.model_copy(update={"thesis_id": tid, "armed_members": members})

    assert push_policy.identities(db, [acme, other], tenant_id=DEFAULT_TENANT_ID) == {
        acme: (None, "Acme Uranium Corp"),
        other: ("OTHR", None),  # the company name is read ONLY for a name with no ticker
    }
    entry = push_policy.digest_entry(
        db, thesis, armed_with(other), armed_with(other, acme), covered=False
    )
    (name,) = entry.names
    assert name.display == "Acme Uranium Corp (no ticker)"
    digest = DigestEvent(asof=date(2026, 10, 16), theses=(entry,))
    assert (
        SlackNotifier._format_digest(digest)
        .splitlines()[1]
        .startswith("Contract: Acme Uranium Corp (no ticker) ")
    )
    reasons = push_policy._reasons(
        armed_with(other, acme),
        armed_with(other),
        [acme],
        push_policy.identities(db, [acme], tenant_id=DEFAULT_TENANT_ID),
    )
    assert reasons[0] == "no longer armed: Acme Uranium Corp (no ticker)"


# --- the Slack adapter delivers the classes as built (a real event, the webhook faked) ---------------------


def test_slack_pushes_the_real_downgrade_ONCE_and_QUIET(db, monkeypatch):
    nxt = _seed(db, (1168, 1180))
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/T/B/x")
    get_settings.cache_clear()
    posted: list[str] = []

    def fake_post(url, **kw):
        posted.append(kw["json"]["text"])
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        _run(
            db,
            monkeypatch,
            nxt,
            asof=date(2026, 9, 23),
            now=datetime(2026, 9, 23, 22, 36, tzinfo=_NY),
            cap=SlackNotifier(),
        )
    finally:
        monkeypatch.delenv("SLACK_WEBHOOK_URL")
        get_settings.cache_clear()

    (text,) = posted  # the quiet downgrade, once — no digest (no new name)
    assert (
        text.startswith("Digital Asset Treasury Complex — still armed, weaker") and "🔴" not in text
    )

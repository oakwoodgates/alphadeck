"""SlackNotifier — the loud DELIVERY channel (inverse loudness #7) + its fail-open contract.

No DB: these build a TransitionEvent, patch the outbound ``httpx.post``, and assert on the captured call. The
env override follows the repo recipe — ``setenv`` + ``get_settings.cache_clear()`` under an autouse fixture that
scrubs the var and clears the cache on both sides.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import httpx
import pytest

from domain.settings import get_settings
from notify import (
    ArmedName,
    DigestEvent,
    DigestThesis,
    HealthEvent,
    LogNotifier,
    SlackNotifier,
    SpacStatusEvent,
    SpacThesisMatch,
    TransitionEvent,
    get_notifier,
)

_WEBHOOK = "https://hooks.slack.example/T000/B000/xxxx: the webhook"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Isolate SLACK_WEBHOOK_URL from the real env and re-read the cached singleton both sides."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _event(*, to_state: str, from_state: str = "warming") -> TransitionEvent:
    return TransitionEvent(
        thesis_id=uuid4(),
        thesis_name="AI Memory & Storage Supercycle",
        asof=date(2026, 6, 5),
        from_state=from_state,
        to_state=to_state,
        from_verdict="watching",
        to_verdict="starter_entry",
    )


def _capture_post(monkeypatch, *, raises: BaseException | None = None):
    """Patch httpx.post to record (url, json) — or raise, to exercise the fail-open path."""
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append({"url": url, "json": kw.get("json"), "timeout": kw.get("timeout")})
        if raises is not None:
            raise raises
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _enable_slack(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _WEBHOOK)
    get_settings.cache_clear()


# --- (1) posts on a → armed move ---------------------------------------------------------------------------


def test_posts_on_armed_transition(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(_event(to_state="armed"))

    assert len(calls) == 1
    assert calls[0]["url"] == _WEBHOOK
    body = calls[0]["json"]
    assert set(body) == {"text"}
    text = body["text"]
    assert (
        "AI Memory & Storage Supercycle" in text
    )  # thesis-name identity (operator-chosen; no ticker)
    assert "ARMED" in text
    assert "warming → armed" in text  # the state move is glanceable


# --- (1b) COMPACT company-level detail on the armed push ---------------------------------------------------
# The push gains WHICH names armed + the events that armed them, in a 3-line form: header (state move kept),
# the armed tickers, then a per-company trigger tail. Adds the triggering evidence to the push (#6), still
# only on a → armed move (#7); the friendly kind map is a static presentation layer (no LLM, #3).


def _armed_event(*, thesis_name="AI Memory & Storage", armed=()):
    return TransitionEvent(
        thesis_id=uuid4(),
        thesis_name=thesis_name,
        asof=date(2026, 6, 5),
        from_state="warming",
        to_state="armed",
        from_verdict="watching",
        to_verdict="core_entry",
        armed=armed,
    )


def test_armed_push_renders_compact_company_detail(monkeypatch):
    """The 3-line compact push: header (the state move is KEPT), the armed tickers, then per-company its
    ticker + friendly trigger kinds. The kind map applies (insider→'insider buy',
    technical_breakout→'breakout'); a member's kinds join by ', ', companies by ' · '."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(
        _armed_event(
            armed=(
                ArmedName("CRDO", ("insider", "technical_breakout")),
                ArmedName("MRVL", ("technical_breakout",)),
            )
        )
    )

    assert len(calls) == 1
    text = calls[0]["json"]["text"]
    assert text == (
        "🔴 AI Memory & Storage — ARMED (warming → armed)\n"
        "CRDO, MRVL armed\n"
        "CRDO insider buy, breakout · MRVL breakout"
    )


def test_kind_label_maps_friendly_and_falls_back_to_underscores_to_spaces():
    """The friendly map is a static presentation layer; an UNMAPPED kind degrades to underscores→spaces
    (never a KeyError), so a new Kind added to the enum still renders a readable label unchanged here.
    """
    from notify import _kind_label

    assert _kind_label("insider") == "insider buy"
    assert _kind_label("technical_breakout") == "breakout"
    assert _kind_label("activist_stake") == "activist"
    assert _kind_label("some_new_kind") == "some new kind"  # unmapped → underscores to spaces


def test_armed_push_falls_back_to_the_thesis_only_line_only_when_the_enrichment_produced_nothing(
    monkeypatch,
):
    """Graceful degrade: armed=() (the enrichment failed open) is EXACTLY the thesis-only header line. But a
    name with no ticker is NEVER skipped (Q13 review, #9): it is listed by its company name, or — with no
    company name either — by its security id."""
    _enable_slack(monkeypatch)

    # (a) armed=() — the default / degraded-enrichment case
    calls = _capture_post(monkeypatch)
    SlackNotifier().notify(_event(to_state="armed"))  # _event leaves armed=()
    assert calls[0]["json"]["text"] == (
        "🔴 AI Memory & Storage Supercycle — ARMED (warming → armed)"
    )

    # (b) names that never resolved to a ticker are LISTED, by company name, else by security id
    calls2 = _capture_post(monkeypatch)
    sid = uuid4()
    SlackNotifier().notify(
        _armed_event(
            thesis_name="Solo",
            armed=(
                ArmedName(None, ("insider",), name="Acme Uranium Corp"),
                ArmedName(None, (), security_id=sid),
            ),
        )
    )
    assert calls2[0]["json"]["text"] == (
        "🔴 Solo — ARMED (warming → armed)\n"
        f"Acme Uranium Corp (no ticker), {sid} (no ticker) armed\n"
        "Acme Uranium Corp (no ticker) insider buy"
    )


def test_armed_push_omits_the_tail_line_when_no_member_has_triggers(monkeypatch):
    """A resolved name with NO trigger kinds still lists in line 2 (it armed) but contributes no tail; if
    NO member has a tail, the push is just the 2 lines (header + tickers) — the tail line is loud only
    when there's evidence to show."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(_armed_event(armed=(ArmedName("CRDO", ()), ArmedName("MRVL", ()))))

    assert calls[0]["json"]["text"] == (
        "🔴 AI Memory & Storage — ARMED (warming → armed)\nCRDO, MRVL armed"
    )


# --- (1c) N1 — a push about a PAST or LATE night says so ----------------------------------------------------
# The measured case: the 2026-10-12 pass died mid-run and the next afternoon's boot catch-up recorded the
# night ~18 h late; an arm pushed from that catch-up must never read as tonight's.


def _timed_event(timing: str, asof: date = date(2026, 10, 12)) -> TransitionEvent:
    return TransitionEvent(
        thesis_id=uuid4(),
        thesis_name="Nuclear Buildout",
        asof=asof,
        from_state="warming",
        to_state="armed",
        from_verdict="watching",
        to_verdict="core_entry",
        armed=(ArmedName("IMSR", ("insider",)),),
        timing=timing,
    )


def test_a_late_push_names_its_night_and_says_recorded_late(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(_timed_event("late"))

    assert calls[0]["json"]["text"] == (
        "🔴 Nuclear Buildout — ARMED (warming → armed) (asof 2026-10-12, recorded late)\n"
        "IMSR armed\n"
        "IMSR insider buy"
    )


def test_a_past_push_names_its_night_and_tonights_push_is_unchanged(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(_timed_event("past"))
    SlackNotifier().notify(_timed_event("current"))
    SlackNotifier().notify(_armed_event(thesis_name="Solo"))  # the default: tonight's night

    head = [c["json"]["text"].split("\n")[0] for c in calls]
    assert head == [
        "🔴 Nuclear Buildout — ARMED (warming → armed) (asof 2026-10-12)",
        "🔴 Nuclear Buildout — ARMED (warming → armed)",  # no label: the push reads as before N1
        "🔴 Solo — ARMED (warming → armed)",
    ]


def test_the_record_sink_says_recorded_late_too(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        LogNotifier().notify(_timed_event("late"))
        LogNotifier().notify(_timed_event("current"))
    msgs = [r.getMessage() for r in caplog.records if "TRANSITION" in r.getMessage()]
    assert msgs[0].endswith("(asof 2026-10-12, recorded late)")
    assert msgs[1].endswith("(asof 2026-10-12)")  # the log has always named the as-of


# --- (2) no-op on a quiet (non-armed) move -----------------------------------------------------------------


def test_does_not_post_on_non_armed_transition(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    # a move INTO warming (not armed) — logged by the record sink, but never pushed
    SlackNotifier().notify(_event(from_state="incubating", to_state="warming"))

    assert calls == []


def test_the_record_sink_logs_every_transition_even_when_not_pushed(monkeypatch, caplog):
    """Inverse loudness (#7) lives in the PUSH decision, not the record: SlackNotifier delegates to a
    LogNotifier so EVERY transition is recorded (a log is a record, not a nag) whether or not it's a loud
    → armed push. A quiet → warming move posts nothing (above) but is still logged here."""
    import logging

    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)
    with caplog.at_level(logging.WARNING):
        SlackNotifier().notify(_event(from_state="incubating", to_state="warming"))
    assert calls == []  # not pushed (quiet move)...
    assert any("TRANSITION" in r.message for r in caplog.records)  # ...but still recorded


# --- (3) FAIL-OPEN: a raising POST must NOT propagate out of notify() ---------------------------------------
# This is the load-bearing test: pipeline.daily calls notify() BEFORE record_if_changed + commit inside a
# try/except that rolls back on any exception. If a Slack outage could raise out of notify(), it would DROP the
# call-of-record. So a POST that throws must be swallowed — notify() returns normally.


def test_post_exception_does_not_propagate(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch, raises=httpx.ConnectError("slack is down"))

    # must NOT raise — a Slack outage can never corrupt the record
    SlackNotifier().notify(_event(to_state="armed"))

    assert len(calls) == 1  # the POST was attempted...
    # ...and its exception was swallowed (control reached here without propagating)


def test_raise_for_status_error_does_not_propagate(monkeypatch):
    """A 4xx/5xx from Slack (raise_for_status) is also swallowed."""
    _enable_slack(monkeypatch)
    calls: list[str] = []

    def fake_post(url, **kw):
        calls.append(url)
        return httpx.Response(
            500, request=httpx.Request("POST", url)
        )  # .raise_for_status() -> HTTPStatusError

    monkeypatch.setattr(httpx, "post", fake_post)

    SlackNotifier().notify(_event(to_state="armed"))  # must not raise
    assert calls == [_WEBHOOK]


# --- R4: notify_health — the durable page on a bad cron night (freeze / withheld / errored) ---


def _freeze() -> HealthEvent:
    return HealthEvent(
        asof=date(2026, 7, 17),
        theses=6,
        withheld_no_live=0,
        withheld_failure=0,
        errored=0,
        edgar_fetches=0,
        frozen=True,
    )


def test_health_label_names_the_actual_reason_not_a_generic_gloss():
    """A manual --no-live run is BENIGN and must say so; a total ingest failure is the alarm. The label must
    not read 'failure' when nobody failed (the operator's 'that sounds bad' on the no-live test page).
    """
    no_live = HealthEvent(
        asof=date(2026, 7, 17),
        theses=6,
        withheld_no_live=6,
        withheld_failure=0,
        errored=0,
        edgar_fetches=0,
        frozen=False,
    )
    assert "not an error" in no_live.label and "FAILURE" not in no_live.label
    fail = HealthEvent(
        asof=date(2026, 7, 17),
        theses=2,
        withheld_no_live=0,
        withheld_failure=2,
        errored=0,
        edgar_fetches=50,
        frozen=False,
    )
    assert "TOTAL INGEST FAILURE" in fail.label


def test_health_ALWAYS_pushes_it_only_fires_when_wrong(monkeypatch):
    """Unlike notify (armed-only), EVERY health event pushes — it exists only for the exception (freeze /
    withheld / errored), so it IS the rare loud one. A freeze is exactly the page R1 lacked."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify_health(_freeze())

    assert len(calls) == 1 and calls[0]["url"] == _WEBHOOK
    text = calls[0]["json"]["text"]
    assert "FROZEN" in text and "0 EDGAR fetches" in text  # the freeze, glanceable


def test_health_push_is_FAIL_OPEN(monkeypatch):
    """Same load-bearing contract as notify: a Slack outage must NEVER raise out of the cron."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch, raises=httpx.ConnectError("slack is down"))
    SlackNotifier().notify_health(_freeze())  # must not raise
    assert len(calls) == 1  # attempted + swallowed


def test_health_records_loud_even_with_no_webhook(monkeypatch, caplog):
    """No Slack configured → the LogNotifier fallback still RECORDS the bad night loudly (a log is a record)."""
    import logging

    calls = _capture_post(monkeypatch)  # webhook unset (autouse fixture) → no push
    with caplog.at_level(logging.ERROR):
        LogNotifier().notify_health(_freeze())
    assert calls == []  # nothing pushed (no webhook)
    assert any("CRON HEALTH" in r.message and "FROZEN" in r.message for r in caplog.records)


# --- (4) get_notifier() env selection ----------------------------------------------------------------------


def test_get_notifier_returns_slack_when_webhook_set(monkeypatch):
    _enable_slack(monkeypatch)
    assert isinstance(get_notifier(), SlackNotifier)


def test_get_notifier_falls_back_to_log_when_webhook_unset(monkeypatch):
    # autouse fixture already delenv'd + cache-cleared — the var is unset
    assert isinstance(get_notifier(), LogNotifier)


# --- SPAC ANNOUNCED page: notify_spac_status (announced-only loudness #7, deterministic #3) ---------------
# The radar leg calls this ONLY on a → announced transition for a thesis-matched shell, so — like
# notify_health — every call is by construction the rare exception a push is for (no armed-style gate needed).


def _spac_event(*, ticker="NEWS", signal=("psilocybin",), broad=(), url="https://sec.example/da"):
    return SpacStatusEvent(
        cik="0000002222",
        company_name="New Shell Acquisition Corp",
        ticker=ticker,
        accession="0002222222-26-000002",
        filed=date(2026, 8, 3),
        matches=(
            SpacThesisMatch(
                thesis_id=uuid4(),
                thesis_name="Rainbow",
                accession="0002222222-26-000002",
                signal_terms=signal,
                broad_terms=broad,
                url=url,
            ),
        ),
        url=url,
    )


def test_spac_status_posts_the_compact_announced_page(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify_spac_status(_spac_event())

    assert len(calls) == 1 and calls[0]["url"] == _WEBHOOK
    assert set(calls[0]["json"]) == {"text"}
    assert calls[0]["json"]["text"] == (
        "🟢 NEWS (New Shell Acquisition Corp) — SPAC deal ANNOUNCED · matches Rainbow\n"
        "matched: psilocybin\n"
        "https://sec.example/da"
    )


def test_spac_status_falls_back_to_company_name_without_a_ticker(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify_spac_status(_spac_event(ticker=None, url=None))

    assert calls[0]["json"]["text"] == (
        "🟢 New Shell Acquisition Corp — SPAC deal ANNOUNCED · matches Rainbow\nmatched: psilocybin"
    )


def test_spac_status_lists_both_term_tiers(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify_spac_status(
        _spac_event(signal=("psilocybin",), broad=("uranium enrichment",))
    )

    assert "matched: psilocybin, uranium enrichment" in calls[0]["json"]["text"]


def test_spac_status_push_is_FAIL_OPEN(monkeypatch):
    """The load-bearing contract, same as notify/notify_health: a Slack outage must NEVER raise. The radar
    leg calls this AFTER its own commits, so a raise would falsely fail the leg (and print a leg-failed line).
    """
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch, raises=httpx.ConnectError("slack is down"))
    SlackNotifier().notify_spac_status(_spac_event())  # must not raise
    assert len(calls) == 1  # attempted + swallowed


def test_spac_status_records_loud_even_with_no_webhook(monkeypatch, caplog):
    """No Slack configured → the LogNotifier fallback still RECORDS the announced page loudly (a log is a
    record, not a nag) — the record survives regardless of delivery."""
    import logging

    calls = _capture_post(monkeypatch)  # webhook unset (autouse fixture) → no push
    with caplog.at_level(logging.WARNING):
        LogNotifier().notify_spac_status(_spac_event())
    assert calls == []  # nothing pushed (no webhook)
    assert any("SPAC ANNOUNCED" in r.message and "Rainbow" in r.message for r in caplog.records)


def test_spac_status_with_SEVERAL_matched_theses_is_ONE_page_naming_each(monkeypatch):
    """Q13 — one page per announcement: line 1 names every matched thesis, then one "<thesis> matched:"
    line each (the provenance per thesis, #6), then every distinct DA link the matches cite."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)
    evt = SpacStatusEvent(
        cik="0000002222",
        company_name="New Shell Acquisition Corp",
        ticker="NEWS",
        accession="0002222222-26-000002",
        filed=date(2026, 8, 3),
        matches=(
            SpacThesisMatch(uuid4(), "AI Memory", "0002222222-26-000002", ("HBM",), (), "u1"),
            SpacThesisMatch(uuid4(), "Rainbow", "0002222222-26-000003", ("TMS",), ("DMT",), "u2"),
        ),
        url="u1",
    )

    SlackNotifier().notify_spac_status(evt)

    assert len(calls) == 1
    assert calls[0]["json"]["text"] == (
        "🟢 NEWS (New Shell Acquisition Corp) — SPAC deal ANNOUNCED · matches AI Memory, Rainbow\n"
        "AI Memory matched: HBM\n"
        "Rainbow matched: TMS, DMT\n"
        "u1\n"
        "u2"
    )
    assert evt.label == "NEWS → announced (matches AI Memory, Rainbow)"


# --- Q13 / O3: armed -> armed moves are classified before they are formatted -------------------------------


def _a2a(**kw) -> TransitionEvent:
    base = dict(
        thesis_id=uuid4(),
        thesis_name="Nuclear Buildout",
        asof=date(2026, 10, 7),
        from_state="armed",
        to_state="armed",
        from_verdict="core_entry",
        to_verdict="starter_entry",
    )
    return TransitionEvent(**{**base, **kw})


def test_a_DOWNGRADE_pushes_QUIET_text_with_its_reasons(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(
        _a2a(
            from_grade="core",
            to_grade="flip",
            from_armed=5,
            to_armed=3,
            reasons=("no longer armed: AAA (fast breakdown), BBB",),
        )
    )

    (call,) = calls
    assert call["json"]["text"] == (
        "Nuclear Buildout — still armed, weaker: core → flip; 5 → 3 names "
        "(core_entry → starter_entry)\n"
        "no longer armed: AAA (fast breakdown), BBB"
    )


def test_a_MIXED_move_states_both_sides_and_a_CHANGED_note_is_quiet_too(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(
        _a2a(
            from_verdict="starter_entry",
            to_verdict="core_entry",
            from_grade="flip",
            to_grade="core",
            from_armed=2,
            to_armed=1,
        )
    )
    SlackNotifier().notify(
        _a2a(
            from_verdict="flip_only",
            to_verdict="starter_entry",
            from_grade="flip",
            to_grade="flip",
            from_armed=2,
            to_armed=2,
            timing="late",
        )
    )

    assert [c["json"]["text"] for c in calls] == [
        "Nuclear Buildout — still armed, mixed: flip → core; 2 → 1 names (starter_entry → core_entry)",
        "Nuclear Buildout — still armed, changed (flip_only → starter_entry) "
        "(asof 2026-10-07, recorded late)",
    ]


def test_an_UPGRADE_is_LOUD_with_an_up_marker_and_what_improved(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(
        _a2a(
            from_verdict="flip_only",
            to_verdict="starter_entry",
            from_grade="flip",
            to_grade="flip",
            from_armed=1,
            to_armed=3,
            armed=(ArmedName("CEG", ("catalyst",)),),
        )
    )

    assert calls[0]["json"]["text"] == (
        "🔴 Nuclear Buildout — ARMED ↑ +2 names (flip_only → starter_entry)\nCEG armed\nCEG catalyst"
    )


def test_a_DE_ARM_is_recorded_never_pushed(monkeypatch, caplog):
    """De-arm pushes are unchanged by Q13: there were none (a de-arm is a record, not a nag)."""
    import logging

    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)
    with caplog.at_level(logging.WARNING):
        SlackNotifier().notify(_a2a(to_state="warming", to_verdict="not_yet"))
    assert calls == []
    assert any("TRANSITION" in r.message for r in caplog.records)


def test_the_record_sink_tags_an_armed_to_armed_move_with_its_class(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        LogNotifier().notify(_a2a(from_grade="core", to_grade="flip", from_armed=4, to_armed=3))
    (msg,) = [r.getMessage() for r in caplog.records if "TRANSITION" in r.getMessage()]
    assert "[downgrade]" in msg and msg.endswith("(asof 2026-10-07)")


# --- Q13 / O4: the digest ----------------------------------------------------------------------------------


def _digest(*theses, timing="current") -> DigestEvent:
    return DigestEvent(asof=date(2026, 10, 12), theses=tuple(theses), timing=timing)


def test_the_digest_pushes_one_QUIET_message_grouped_by_thesis(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify_digest(
        _digest(
            DigestThesis(uuid4(), "Nuclear Buildout", (ArmedName("PCG", ("insider", "catalyst")),)),
            DigestThesis(
                uuid4(),
                "Rainbow Rush",
                names=(ArmedName(None, ("insider",), name="Acme Uranium Corp"),),
                rearms=(ArmedName("CYCN", ()),),
            ),
            timing="late",
        )
    )

    (call,) = calls
    assert call["json"]["text"] == (
        "Newly armed in already-armed theses (asof 2026-10-12, recorded late)\n"
        "Nuclear Buildout: PCG insider buy, catalyst\n"
        "Rainbow Rush: Acme Uranium Corp (no ticker) insider buy · CYCN (re-arm)"
    )  # first-time arms first, re-arms after and marked; a ticker-less name by its company name
    assert "🔴" not in call["json"]["text"]


def test_an_EMPTY_digest_never_pushes(monkeypatch, caplog):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)
    SlackNotifier().notify_digest(_digest())
    assert calls == []


def test_the_digest_push_is_FAIL_OPEN(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch, raises=httpx.ConnectError("slack is down"))
    SlackNotifier().notify_digest(
        _digest(DigestThesis(uuid4(), "Rainbow Rush", (ArmedName("CYCN", ()),)))
    )  # must not raise
    assert len(calls) == 1

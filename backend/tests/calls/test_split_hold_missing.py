"""The assembler half of the split-night hold (S5, CALL_LOGIC §2b): the hold's lines join ``missing[]`` after its
own entries, change nothing else, and no hold leaves the card exactly as before. (Which triggers are held, and
the lines' words, are ``pipeline.core`` + ``domain.split_hold`` — golden on real nights in
``tests/signals/test_split_hold.py``.)"""

from __future__ import annotations

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from tests.calls.factories import ASOF, insider_event, make_thesis

LINE = (
    "Breakout on DEVCO held: possible unconfirmed split (the price stepped x16 on 06-02 against the "
    "volume; no split filing found yet)"
)


def test_the_held_line_joins_missing_after_its_own_entries():
    events = [
        insider_event()
    ]  # a conviction with no confirmation: the breakout was the held trigger
    plain = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    held = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG, held=[LINE])
    assert held.missing == [*plain.missing, LINE]
    assert LINE in held.counter_case  # the deterministic counter-case lists what is missing
    # nothing else on the card moves: state, verdict, grades, clocks, triggers
    strip = {"missing", "counter_case"}
    assert held.model_dump(exclude=strip) == plain.model_dump(exclude=strip)


def test_no_hold_is_byte_identical():
    events = [insider_event()]
    assert assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG, held=()) == assemble_call(
        make_thesis(), events, ASOF, DEFAULT_CONFIG
    )

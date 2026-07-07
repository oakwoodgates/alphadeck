"""The canonical-primary rank (the multi-sibling CIK rule) — the EMPIRICAL GATE made durable.

"SEC file order is primary-first" was validated against every multi-row CIK in the live file (2026-07-07)
and found USUALLY-RIGHT, NOT the rule: 51 violations. Each violation class found live is pinned here as a
unit fixture, so the composite rank (instrument class > exchange > F-ordinary demotion > SEC file order)
can never regress to the failure it was built to prevent — a stably-wrong instrument the operator's real
trade rides on. The DB half pins the resolvers: ``ids_for_ciks`` picks the flagged primary (stable even
unflagged), and ``canonicalize_ids`` re-points a sibling to it (the promote guard's second half).
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from securities import master
from securities.sec_tickers import flag_primaries


def _primary(rows: list[tuple[str, str, str | None, str | None]]) -> str:
    """The ticker flag_primaries marks primary (rows share one CIK)."""
    flagged = flag_primaries(rows)
    (winner,) = [t for _c, t, _n, _e, p in flagged if p]
    return winner


# --- the violation classes found live (each one a real case from the 2026-07-07 file) ---


def test_exchange_splits_the_adr_foreign_ordinary_pair():
    """ASML(Nasdaq) over ASMLF(OTC) — file order agreed here, but DEVSF/ORISF proved order alone inverts."""
    assert (
        _primary([("0000937966", "ASML", "ASML", "Nasdaq"), ("0000937966", "ASMLF", "ASML", "OTC")])
        == "ASML"
    )


def test_exchange_corrects_the_inverted_file_order():
    """THE dangerous live class: the OTC foreign ordinary listed FIRST (DEVSF before DEVS/Nasdaq). File
    order would stamp the illiquid line primary — stably and invisibly wrong; the exchange rank corrects.
    """
    assert (
        _primary(
            [
                ("0001854480", "DEVSF", "DevvStream", "OTC"),
                ("0001854480", "DEVS", "DevvStream", "Nasdaq"),
            ]
        )
        == "DEVS"
    )


def test_exchange_demotes_the_bankruptcy_line():
    """SNBRQ(OTC) was listed before SNBR(Nasdaq) live — the Q-tagged line loses on venue."""
    assert (
        _primary(
            [
                ("0000827187", "SNBRQ", "Sleep Number", "OTC"),
                ("0000827187", "SNBR", "Sleep Number", "Nasdaq"),
            ]
        )
        == "SNBR"
    )


def test_derivative_demotion_beats_file_order_units_first():
    """GPATU (unit) and OABIW (warrant) were listed FIRST live, same venue as the common — the
    sibling-relative suffix demotion picks the common regardless of order or venue tie."""
    assert (
        _primary(
            [
                ("0001834526", "GPATU", "GP-Act III", "Nasdaq"),
                ("0001834526", "GPAT", "GP-Act III", "Nasdaq"),
                ("0001834526", "GPATW", "GP-Act III", "Nasdaq"),
            ]
        )
        == "GPAT"
    )
    assert (
        _primary(
            [
                ("0001846253", "OABIW", "OmniAb", "Nasdaq"),
                ("0001846253", "OABI", "OmniAb", "Nasdaq"),
            ]
        )
        == "OABI"
    )


def test_derivative_demotion_splits_the_warrant_pair_kttaw():
    """The original display-quirk case: KTTA and KTTAW share Nasdaq (exchange can't split); the warrant
    suffix — relative to its sibling base, never a bare endswith — demotes KTTAW."""
    assert (
        _primary(
            [
                ("0001841330", "KTTA", "Pasithea", "Nasdaq"),
                ("0001841330", "KTTAW", "Pasithea", "Nasdaq"),
            ]
        )
        == "KTTA"
    )


def test_preferred_demotion_beats_the_nyse_preferred():
    """ICR-PA(NYSE) was listed before the OTC common lines live — instrument class outranks venue: an
    equity thesis anchors to the COMMON, not an income line on a bigger exchange."""
    assert (
        _primary(
            [
                ("0001690012", "ICR-PA", "InPoint", "NYSE"),
                ("0001690012", "ICRL", "InPoint", "OTC"),
                ("0001690012", "ICRP", "InPoint", "OTC"),
            ]
        )
        == "ICRL"
    )


def test_dual_class_falls_to_sec_file_order_googl():
    """Same class, same venue -> the SEC's own order decides. NAMED ASSUMPTION (operator-ratified):
    SEC-first-row is a proxy for primary US LISTING; for dual-class it tracks governance-primary (GOOGL
    class A), not necessarily trading-primary (GOOG class C is often more liquid). Both liquid Nasdaq so
    it's moot here; if a dual-class liquidity case ever bites, the tiebreaker becomes volume."""
    assert (
        _primary(
            [
                ("0001652044", "GOOGL", "Alphabet", "Nasdaq"),
                ("0001652044", "GOOG", "Alphabet", "Nasdaq"),
            ]
        )
        == "GOOGL"
    )


def test_all_otc_foreign_pair_prefers_the_adr_over_the_f_ordinary():
    """44 all-OTC pairs listed the F-ordinary first live — the F-suffix demotion (OTC + …F + a non-F
    sibling) picks the ADR line (the US-tradeable one) regardless of order."""
    assert (
        _primary(
            [("0000000001", "TCTZF", "Tencent", "OTC"), ("0000000001", "TCEHY", "Tencent", "OTC")]
        )
        == "TCEHY"
    )


def test_spac_preseparation_common_over_its_trading_unit():
    """The one class where instrument-class outranks a LIVE venue: a pre-separation SPAC's common
    (exchange None — not yet trading separately) beats its trading unit. Identity-correct by design (the
    unit splits INTO the common) — the named edge from the gate, accepted."""
    assert (
        _primary(
            [("0002128115", "AAC", "Ares Acq", None), ("0002128115", "AAC-UN", "Ares Acq", "NYSE")]
        )
        == "AAC"
    )


def test_plain_ticker_ending_in_suffix_letter_is_not_misread():
    """The suffix check is SIBLING-RELATIVE: a standalone ticker ending in W/U/R (no sibling base) is a
    common share, never misread as a derivative."""
    assert (
        _primary(
            [("0000000002", "GLW", "Corning", "NYSE"), ("0000000002", "GLWF", "Corning", "OTC")]
        )
        == "GLW"
    )


def test_flag_primaries_exactly_one_per_cik_and_order_preserved():
    rows = [
        ("0000000010", "AAA", "A", "Nasdaq"),
        ("0000000011", "BBBU", "B", "Nasdaq"),
        ("0000000011", "BBB", "B", "Nasdaq"),
        ("0000000010", "AAAW", "A", "Nasdaq"),
    ]
    flagged = flag_primaries(rows)
    assert [(t, p) for _c, t, _n, _e, p in flagged] == [
        ("AAA", True),
        ("BBBU", False),
        ("BBB", True),
        ("AAAW", False),
    ]  # input (file) order preserved; one primary per CIK


# --- the resolvers over the flag (DB) ---


def _insert(db, *, ticker, cik, is_primary=None, name="X"):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, is_primary, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, is_primary, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def test_ids_for_ciks_resolves_to_the_primary_sibling(db):
    """The root-cause fix: a multi-sibling CIK resolves to the flagged primary — not whichever row a tied
    recorded_at happened to surface — so a re-draft returns the SAME id the operator confirmed."""
    _insert(db, ticker="ASMLF", cik="0000937966", is_primary=False)
    asml = _insert(db, ticker="ASML", cik="0000937966", is_primary=True)
    assert master.ids_for_ciks(db, ["0000937966"]) == {"0000937966": asml}


def test_ids_for_ciks_is_stable_even_unflagged(db):
    """The migration window (is_primary all NULL, byte-identical recorded_at): the trailing ``id`` tail
    keeps the pick DETERMINISTIC across calls — stability is the floor, the flag adds correctness.
    """
    a = _insert(db, ticker="AAA", cik="0000000001")
    b = _insert(db, ticker="AAB", cik="0000000001")
    picks = {tuple(master.ids_for_ciks(db, ["0000000001"]).items()) for _ in range(5)}
    assert len(picks) == 1  # five calls, one answer
    assert next(iter(picks))[0][1] in {a, b}


def test_canonicalize_ids_repoints_the_sibling_only(db):
    """The promote guard's second half: a non-primary sibling id maps to (primary_id, primary_ticker); the
    primary itself, a CIK-less row, and an unknown id map to nothing (stored as-is)."""
    asmlf = _insert(db, ticker="ASMLF", cik="0000937966", is_primary=False)
    asml = _insert(db, ticker="ASML", cik="0000937966", is_primary=True)
    lone = _insert(db, ticker="LONE", cik=None)
    out = master.canonicalize_ids(db, [asmlf, asml, lone, uuid.uuid4()])
    assert out == {asmlf: (asml, "ASML")}

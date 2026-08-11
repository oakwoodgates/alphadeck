from __future__ import annotations

from signals.display import theme_breadth

# ---------------------------------------------------------------------------------------------------
# Hand-constructed member close-series. A flat baseline of 100 makes the SMA50 exactly 100 at every
# point, so a single downward dip at a chosen index deterministically drives that point BELOW its
# trailing 50-bar mean while leaving the other point above it — a clean, hand-verifiable way to place
# a member into one of the four (above-now × above-20d-ago) cells.
# ---------------------------------------------------------------------------------------------------
_N = 80  # >= MIN_BARS (70), with margin
_BASE = 100.0
_DIP = 80.0  # clearly below the ~99.x trailing mean a single dip produces


def _series(dips: tuple[int, ...] = ()) -> list[float]:
    """A flat-100 series of _N bars with a downward dip at each NEGATIVE index in ``dips``."""
    closes = [_BASE] * _N
    for idx in dips:
        closes[idx] = _DIP
    return closes


# above BOTH now and 20d-ago (a steady participant): no dips
def _up_up() -> list[float]:
    return _series()


# below 20d-ago, above now (a member that TURNED UP): dip only at index -21
def _turned_up() -> list[float]:
    return _series((-1 - theme_breadth.DELTA_LOOKBACK_BARS,))


# above 20d-ago, below now (a member that TURNED DOWN): dip only at the last bar
def _turned_down() -> list[float]:
    return _series((-1,))


# below BOTH (never participating): dip at both the 20d-ago bar and the last bar
def _down_down() -> list[float]:
    return _series((-1 - theme_breadth.DELTA_LOOKBACK_BARS, -1))


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_cell_construction_is_what_we_think():
    """Anchor the fixtures: each helper lands in exactly the (above-now, above-ago) cell claimed, so
    the breadth arithmetic below rests on verified inputs, not hope."""
    # a 1-member basket reads breadth = 100% or 0% depending on the single member's now-cell
    assert _by_key(theme_breadth.compute([_up_up()]))["breadth"].value == 100.0
    assert _by_key(theme_breadth.compute([_turned_up()]))["breadth"].value == 100.0  # above NOW
    assert _by_key(theme_breadth.compute([_turned_down()]))["breadth"].value == 0.0  # below NOW
    assert _by_key(theme_breadth.compute([_down_down()]))["breadth"].value == 0.0
    # the 20d-ago reading distinguishes turned_up (below then) from up_up (above then)
    assert _by_key(theme_breadth.compute([_turned_up()]))["breadth_prior"].value == 0.0
    assert _by_key(theme_breadth.compute([_up_up()]))["breadth_prior"].value == 100.0


def test_thrust_fires_at_majority_and_delta():
    """70% above now, 40% above 20d-ago -> +30 pts: >= 50% AND >= +25 pts -> THRUST (loud, #7)."""
    basket = [_up_up()] * 4 + [_turned_up()] * 3 + [_down_down()] * 3  # counted = 10
    sig = theme_breadth.compute(basket)
    m = _by_key(sig)
    assert m["breadth"].value == 70.0  # up_up (4) + turned_up (3) above now
    assert m["breadth_prior"].value == 40.0  # only the 4 up_up were above 20d ago
    assert m["breadth_delta"].value == 30.0
    assert m["breadth_delta"].tone == "pos"
    assert m["members_counted"].value == 10.0
    assert sig.headline.key == "thrust"  # the stable categorical the FE chip reads
    assert sig.headline.glyph == "up"


def test_no_thrust_when_delta_below_threshold():
    """60% now, 40% 20d-ago -> +20 pts: majority holds but the surge is < +25 -> NO thrust (quiet)."""
    basket = [_up_up()] * 4 + [_turned_up()] * 2 + [_down_down()] * 4  # counted = 10
    sig = theme_breadth.compute(basket)
    m = _by_key(sig)
    assert m["breadth"].value == 60.0 and m["breadth_prior"].value == 40.0
    assert m["breadth_delta"].value == 20.0  # +20 < +25 -> the delta gate fails
    assert sig.headline.key == "quiet"
    assert sig.headline.glyph is None  # quiet: no loudness


def test_no_thrust_when_below_majority_even_with_a_big_surge():
    """40% now (below the majority) with a +40 pt surge -> NO thrust: the majority gate fails alone."""
    basket = [_turned_up()] * 4 + [_down_down()] * 6  # now 40%, ago 0% -> +40 pts
    sig = theme_breadth.compute(basket)
    m = _by_key(sig)
    assert m["breadth"].value == 40.0 and m["breadth_delta"].value == 40.0
    assert sig.headline.key == "quiet"  # a surge cannot rescue a minority-participation basket


def test_thin_member_is_shown_not_counted():
    """A member with < 70 bars can't compute BOTH readings -> excluded from the denominator, surfaced
    honestly in the counts, never fabricated as a 'below' (#9)."""
    thin = [_BASE] * (theme_breadth.MIN_BARS - 1)  # 69 bars: SMA50-at-asof real, 20d-ago unknowable
    basket = [_up_up()] * 3 + [thin]  # 4 resolved, 3 counted
    sig = theme_breadth.compute(basket)
    m = _by_key(sig)
    assert m["breadth"].value == 100.0  # 3/3 counted, the thin one does not dilute it
    assert m["members_counted"].value == 3.0
    assert m["members_thin"].value == 1.0
    assert "shown-not-counted" in m["members_thin"].note
    assert sig.basis.params["members_resolved"] == 4


def test_all_thin_reads_unknown_not_a_fake_zero():
    """Every member too thin -> breadth is n/a (unknown), not a false 0% -> the quiet 'unknown' state."""
    sig = theme_breadth.compute([[_BASE] * 10, [_BASE] * 5])
    m = _by_key(sig)
    assert m["breadth"].value is None and m["breadth"].note is not None
    assert m["members_counted"].value == 0.0 and m["members_thin"].value == 2.0
    assert sig.headline.key == "unknown"


def test_empty_basket_returns_none():
    assert theme_breadth.compute([]) is None


def test_dials_ride_the_basis_for_show_the_work():
    sig = theme_breadth.compute([_up_up()])
    assert sig.kind == "theme_breadth" and sig.basis.source == "fact_price_eod"
    assert sig.basis.params["sma_window"] == 50
    assert sig.basis.params["delta_lookback_bars"] == 20
    assert sig.basis.params["thrust_min_breadth_pct"] == 50.0
    assert sig.basis.params["thrust_min_delta_pts"] == 25.0

"""F3 — the etf_flow golden pure-math tests. THE THREE TRAPS lead: flow is Δshares_out × close, so
(a) price appreciation alone, (b) volume churn alone, and (c) an AUM rise without Δshares must each
read ZERO flow — and a true zero (equal real samples) must be DISTINCT from "no data" (n/a)."""

from __future__ import annotations

from datetime import date, timedelta

from signals.display import etf_flow

_ASOF = date(2026, 7, 24)


def _samples(rows: list[tuple[int, float]], *, source="globalx") -> list[dict]:
    """rows: (days_before_asof, shares_out)."""
    return [
        {
            "d": _ASOF - timedelta(days=back),
            "shares_out": shares,
            "source": source,
            "source_ref": "https://www.globalxetfs.com/funds/ura",
        }
        for back, shares in rows
    ]


def _bars(closes: list[tuple[int, float]], volume: float = 1e6) -> list[dict]:
    """closes: (days_before_asof, close). Volume rides along (etf_flow must never read it)."""
    return [{"d": _ASOF - timedelta(days=back), "close": c, "volume": volume} for back, c in closes]


def _flat_series(shares: float, days: int = 40) -> list[dict]:
    return _samples([(back, shares) for back in range(days, -1, -1)])


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


# --- THE THREE TRAPS: shares flat => ZERO flow, whatever else moved ----------------------------------


def test_trap_a_price_appreciation_alone_is_zero_flow():
    """Close doubles over the month; shares outstanding never move. AUM doubled — flow is ZERO."""
    bars = _bars([(back, 20.0 + (40 - back) * 0.5) for back in range(40, -1, -1)])
    m = _by_key(etf_flow.compute(_flat_series(138_771_666.0), bars, _ASOF))
    assert m["flow_1w_usd"].value == 0.0
    assert m["flow_1m_usd"].value == 0.0
    assert m["flow_1m_pct_of_shares"].value == 0.0


def test_trap_b_volume_churn_alone_is_zero_flow():
    """Huge secondary-market churn (volume 100×), flat shares — churn is not creation. ZERO flow."""
    bars = _bars([(back, 20.0) for back in range(40, -1, -1)], volume=1e8)
    m = _by_key(etf_flow.compute(_flat_series(1_000_000.0), bars, _ASOF))
    assert m["flow_1w_usd"].value == 0.0
    assert m["flow_1m_usd"].value == 0.0


def test_trap_c_aum_rise_without_delta_shares_is_zero_flow():
    """AUM (shares × close) rises 50% purely on price: same trap as (a), framed on the AUM figure the
    sleeve dossier shows beside this chip — the chip must NOT read the AUM rise as an inflow."""
    shares = 21_864_628.0
    start_close, end_close = 40.0, 60.0
    bars = _bars([(back, start_close + (40 - back) * 0.5) for back in range(40, -1, -1)])
    sig = etf_flow.compute(_flat_series(shares), bars, _ASOF)
    aum_start, aum_end = shares * start_close, shares * end_close
    assert aum_end > aum_start * 1.4  # the AUM really did rise…
    m = _by_key(sig)
    assert m["flow_1m_usd"].value == 0.0  # …and the flow read is still exactly zero
    assert sig.headline.key == "net_flat"


# --- zero flow is DISTINCT from no data ---------------------------------------------------------------


def test_zero_flow_is_a_real_zero_not_na():
    sig = etf_flow.compute(
        _flat_series(1000.0), _bars([(back, 5.0) for back in range(40, -1, -1)]), _ASOF
    )
    m = _by_key(sig)
    assert m["flow_1m_usd"].value == 0.0 and m["flow_1m_usd"].note is None
    assert sig.headline is not None and sig.headline.glyph == "flat"


def test_no_samples_at_all_returns_none():
    """Every non-ETF member — and an ETF before its first sample — honestly renders nothing."""
    assert etf_flow.compute([], _bars([(1, 5.0)]), _ASOF) is None


def test_series_younger_than_the_window_is_na_with_the_count():
    """Forward-only accrual (#7 quiet while accruing): 3 samples, no baseline on/before either window
    start — both windows n/a, saying how much of the window is sampled."""
    sig = etf_flow.compute(
        _samples([(2, 100.0), (1, 110.0), (0, 120.0)]),
        _bars([(back, 5.0) for back in range(5, -1, -1)]),
        _ASOF,
    )
    m = _by_key(sig)
    assert m["flow_1w_usd"].value is None and m["flow_1w_usd"].note == "n/a: 3/7 sampled days"
    assert m["flow_1m_usd"].value is None and m["flow_1m_usd"].note == "n/a: 3/30 sampled days"
    assert sig.headline is None  # nothing statable yet — the chip stays quiet


def test_stale_series_is_na_not_zero():
    """A baseline exists but the sampler stopped 10 days ago: the 1w window has no fresh sample —
    that is UNKNOWN flow (n/a + the latest-sample date), never a fabricated zero."""
    sig = etf_flow.compute(
        _samples([(40, 100.0), (10, 100.0)]),
        _bars([(back, 5.0) for back in range(40, -1, -1)]),
        _ASOF,
    )
    m = _by_key(sig)
    assert m["flow_1w_usd"].value is None
    assert "no sample in the last 7d" in m["flow_1w_usd"].note
    assert (_ASOF - timedelta(days=10)).isoformat() in m["flow_1w_usd"].note


# --- the signed rollup itself --------------------------------------------------------------------------


def test_inflow_math_prices_each_delta_at_its_days_close():
    """Two creations inside the month: +1000 shares on d-20 (close 10) and +500 on d-5 (close 20)
    ⇒ 1m flow = 1000×10 + 500×20 = $20,000; 1w sees only the +500 ⇒ $10,000. Baselines: 1m starts
    from 10,000 shares (Δ +1500 = +15%), 1w from 11,000 (Δ +500 ≈ +4.55%)."""
    samples = _samples([(35, 10_000.0), (20, 11_000.0), (5, 11_500.0)])
    bars = _bars([(20, 10.0), (5, 20.0), (0, 30.0)])
    sig = etf_flow.compute(samples, bars, _ASOF)
    m = _by_key(sig)
    assert m["flow_1m_usd"].value == 20_000.0
    assert m["flow_1m_pct_of_shares"].value == 15.0
    assert m["flow_1w_usd"].value == 10_000.0
    assert m["flow_1w_pct_of_shares"].value == 4.55
    assert sig.headline.key == "net_inflow" and sig.headline.glyph == "up"
    assert "1m net INFLOW" in sig.headline.label and "+15.0% of shares" in sig.headline.label


def test_outflow_reads_negative_and_down():
    samples = _samples([(35, 10_000.0), (5, 9_000.0)])
    bars = _bars([(5, 10.0), (0, 10.0)])
    sig = etf_flow.compute(samples, bars, _ASOF)
    m = _by_key(sig)
    assert m["flow_1m_usd"].value == -10_000.0  # −1000 shares × $10
    assert m["flow_1m_pct_of_shares"].value == -10.0
    assert sig.headline.key == "net_outflow" and sig.headline.glyph == "down"
    assert "OUTFLOW" in sig.headline.label


def test_weekend_dated_sample_prices_at_the_prior_close():
    """A Saturday-dated sample has no same-day bar: the delta prices at the latest close on/before it
    (Friday's) — the standard as-of convention, never a dropped delta."""
    saturday = _ASOF - timedelta(days=1)
    samples = [
        *_samples([(35, 1000.0)]),
        {"d": saturday, "shares_out": 1100.0, "source": "globalx", "source_ref": "u"},
    ]
    bars = _bars([(2, 7.0)])  # the latest bar is two days before asof (Friday)
    m = _by_key(etf_flow.compute(samples, bars, _ASOF))
    assert m["flow_1w_usd"].value == 700.0  # +100 shares × Friday's $7


def test_delta_with_no_close_on_file_is_excluded_and_said():
    samples = _samples([(35, 1000.0), (5, 1100.0)])
    m = _by_key(etf_flow.compute(samples, [], _ASOF))  # no bars at all
    assert m["flow_1m_usd"].value == 0.0  # the one delta is unpriced -> excluded from the $ sum
    assert m["flow_1m_usd"].note == "1 deltas unpriced (no close on file)"
    assert m["flow_1m_pct_of_shares"].value == 10.0  # the share-count read needs no close


def test_headline_falls_back_to_1w_while_1m_accrues():
    """A 10-day-old series: the 1m window has no baseline (n/a) but the 1w window is fully knowable —
    the chip states the 1w read rather than sitting mute."""
    samples = _samples([(10, 1000.0), (3, 1050.0)])
    bars = _bars([(back, 4.0) for back in range(12, -1, -1)])
    sig = etf_flow.compute(samples, bars, _ASOF)
    m = _by_key(sig)
    assert m["flow_1m_usd"].value is None
    assert m["flow_1w_usd"].value == 200.0  # +50 × $4
    assert sig.headline is not None and sig.headline.label.startswith("1w net INFLOW")


def test_basis_carries_the_provenance():
    """#6: the reading shows its work — the fact table, both windows, the sample count, the latest
    sample's exact URL, and the adapter(s) (with the aggregator's resolution caveat when it sampled).
    """
    samples = [
        *_samples([(35, 1000.0), (20, 1010.0)], source="stockanalysis"),
        *_samples([(5, 1020.0)]),
    ]
    sig = etf_flow.compute(samples, _bars([(back, 4.0) for back in range(40, -1, -1)]), _ASOF)
    b = sig.basis
    assert b.source == "fact_fund_shares"
    assert b.params["window_1w_days"] == 7 and b.params["window_1m_days"] == 30
    assert b.params["sample_count"] == 3 and b.bars_used == 3
    assert (
        b.params["source_ref"] == "https://www.globalxetfs.com/funds/ura"
    )  # the LATEST sample's page
    assert b.window_end == _ASOF - timedelta(days=5)  # the staleness tell
    assert "globalx" in b.note and "stockanalysis" in b.note and "rounded" in b.note


def test_post_asof_samples_are_ignored_no_lookahead():
    """A pure-fn belt over #1 (the PIT read already caps at asof): a future-dated sample changes
    nothing about a read pinned earlier."""
    future = [
        {"d": _ASOF + timedelta(days=3), "shares_out": 9e9, "source": "globalx", "source_ref": "u"}
    ]
    with_future = etf_flow.compute(
        _flat_series(1000.0) + future, _bars([(back, 5.0) for back in range(40, -1, -1)]), _ASOF
    )
    without = etf_flow.compute(
        _flat_series(1000.0), _bars([(back, 5.0) for back in range(40, -1, -1)]), _ASOF
    )
    assert with_future == without

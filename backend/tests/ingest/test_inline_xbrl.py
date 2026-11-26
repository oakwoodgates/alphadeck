"""The raw inline-XBRL reader (``ingest.edgar.inline_xbrl``) on cut-down real 10-Q documents.

Fixtures: ``tests/fixtures/sec_extractor/inline/`` (README there). Pure: no database, no network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from domain.config import DEFAULT_EXTRACTOR_CONFIG as CFG
from ingest.edgar import inline_xbrl as ix

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "inline"
_Q3 = date(2026, 9, 30)


def _raw(ticker: str) -> str:
    return (_FX / f"{ticker}-10q.htm").read_text(encoding="utf-8")


def _cover(ticker: str) -> ix.CoverReading:
    cover = ix.read_cover(_raw(ticker))
    assert cover is not None, ticker
    return cover


def _eps(ticker: str, period: date = _Q3) -> ix.EpsBase:
    eps = ix.eps_share_base(
        _raw(ticker), period, CFG.quarterly_span_min_days, CFG.pace_ytd_max_days
    )
    assert eps is not None, ticker
    return eps


def _counts(cover: ix.CoverReading) -> dict[str | None, float]:
    return {c.member: c.value for c in cover.counted}


def test_googl_scale_is_applied_per_class_at_the_cover_instant():
    cover = _cover("GOOGL")
    assert cover.asof == date(2026, 10, 21)
    assert _counts(cover) == {
        "us-gaap:CommonClassAMember": 5_888_000_000,
        "us-gaap:CommonClassBMember": 827_000_000,
        "goog:CapitalClassCMember": 5_579_000_000,
    }
    assert [c.shown for c in cover.counted] == ["5,888", "827", "5,579"]
    assert cover.figure == 12_294_000_000 and cover.multi_class and cover.total is None


def test_googl_eps_base_is_the_quarter_per_class_and_matches_class_c_by_its_letter():
    eps = _eps("GOOGL")
    assert (eps.start, eps.end) == (date(2026, 7, 1), _Q3)  # the quarter, not the nine months
    assert eps.total == 12_256_000_000
    assert dict(eps.by_class) == {
        "us-gaap:CommonClassAMember": 5_878_000_000,
        "us-gaap:CommonClassBMember": 830_000_000,
        "us-gaap:CommonClassCMember": 5_548_000_000,
    }
    assert ix.class_key("goog:CapitalClassCMember") == ix.class_key("us-gaap:CommonClassCMember")
    assert ix.classes_outside_eps(_cover("GOOGL"), eps) is None


def test_hood_gemi_meta_read_the_classes_whatever_the_cover_wording():
    """HOOD prints 'were N and M', GEMI 'N and M shares of Class A and Class B ..., respectively' (the text parser
    paired M with Class A), META a table: the tags carry the class either way."""
    assert _counts(_cover("HOOD")) == {
        "us-gaap:CommonClassAMember": 793_425_151,
        "us-gaap:CommonClassBMember": 107_066_126,
    }
    assert _counts(_cover("GEMI")) == {
        "us-gaap:CommonClassAMember": 55_898_868,
        "us-gaap:CommonClassBMember": 75_126_784,
    }
    assert _counts(_cover("META")) == {
        "us-gaap:CommonClassAMember": 2_214_472_367,
        "us-gaap:CommonClassBMember": 341_768_886,
    }
    for t in ("HOOD", "GEMI", "META"):
        assert ix.classes_outside_eps(_cover(t), _eps(t)) is None, t


def test_dell_is_dated_by_its_cover_and_its_quarter_ends_on_its_own_period():
    cover = _cover("DELL")
    assert cover.asof == date(2026, 9, 1)  # not the 2026-07-31 period end
    assert cover.figure == 635_812_750
    eps = _eps("DELL", date(2026, 7, 31))
    assert (eps.start, eps.end, eps.total) == (date(2026, 5, 2), date(2026, 7, 31), 645_000_000)


def test_ceg_one_undimensioned_count():
    cover = _cover("CEG")
    assert cover.asof == date(2026, 10, 31)
    assert _counts(cover) == {None: 351_608_504}
    assert not cover.multi_class and cover.set_aside == ()
    assert _eps("CEG").total == 354_000_000


def test_exc_combined_filing_reads_the_parent_never_a_legal_entity_member():
    cover = _cover("EXC")
    assert _counts(cover) == {None: 1_034_896_686}
    assert cover.figure == 1_034_896_686
    aside = {c.member: (c.value, why) for c, why in cover.set_aside}
    assert len(aside) == 6
    assert aside["exc:CommonwealthEdisonCoMember"][0] == 127_021_427
    assert all("LegalEntityAxis" in why for _, why in aside.values())


def test_zlab_adr_line_is_set_aside_not_summed():
    cover = _cover("ZLAB")
    assert _counts(cover) == {"us-gaap:CommonStockMember": 1_124_448_350}
    assert cover.figure == 1_124_448_350  # never 1,630,834,400 (the ADS-held subset added again)
    ((adr, why),) = cover.set_aside
    assert adr.member == "dei:AdrMember" and adr.value == 506_386_050 and "ADR" in why


def test_knsa_total_beside_its_parts_is_not_added_again():
    cover = _cover("KNSA")
    assert cover.total is not None and cover.total.member == "us-gaap:CommonStockMember"
    assert cover.figure == 78_571_705  # never 157,143,410
    assert _counts(cover) == {
        "us-gaap:CommonClassAMember": 48_836_965,
        "us-gaap:CommonClassBMember": 895_158,
        "knsa:CommonClassA1Member": 12_781_964,
        "knsa:CommonClassB1Member": 16_057_618,
    }
    assert ix.classes_outside_eps(cover, _eps("KNSA")) is None  # 78.57M against a base of 78.19M


def test_enha_and_glxy_leave_a_class_outside_the_eps_base():
    enha = ix.classes_outside_eps(_cover("ENHA"), _eps("ENHA"))
    assert enha is not None and enha.how == "nearest"
    assert [c.member for c in enha.inside] == ["us-gaap:CommonClassAMember"]
    assert enha.inside_sum == 13_807_527 and _eps("ENHA").total == 13_378_513
    glxy = ix.classes_outside_eps(_cover("GLXY"), _eps("GLXY"))
    assert glxy is not None and glxy.how == "nearest" and len(glxy.inside) == 1
    assert _eps("GLXY").total == 193_344_582 and _cover("GLXY").figure == 385_736_936


def _reading(*counts: tuple[str, float]) -> ix.CoverReading:
    cs = tuple(ix.CoverCount(m, v, f"{v:,.0f}", i) for i, (m, v) in enumerate(counts))
    return ix.CoverReading(date(2026, 10, 31), cs, sum(v for _, v in counts))


# The next three use the counts and EPS facts read from cached 2026-09-30 10-Qs (MSTR 0001050446-26-000060, FHI
# 0001056288-26-000030, CVNA 0001690820-26-000067), held here as the reader's output rather than as fixtures.


def test_per_class_eps_facts_decide_before_nearness():
    """MSTR's cover (Class A 402,714,357 + Class B 19,640,250) is 5.5% over its EPS base: its per-class EPS facts
    include Class B, so no class is outside (the gap is issuance)."""
    cover = _reading(
        ("us-gaap:CommonClassAMember", 402_714_357), ("us-gaap:CommonClassBMember", 19_640_250)
    )
    per_class = ix.EpsBase(
        date(2026, 7, 1),
        _Q3,
        400_148_000,
        (("us-gaap:CommonClassAMember", 380_508_000), ("us-gaap:CommonClassBMember", 19_640_000)),
    )
    assert ix.classes_outside_eps(cover, per_class) is None


def test_a_class_smaller_than_the_gap_explains_nothing():
    """FHI: 9,000 Class A shares beside 73.9M Class B, a base of 71.0M (4% under the sum): leaving Class A out is
    nearer, but 9,000 shares cannot explain a 2.9M gap."""
    cover = _reading(
        ("us-gaap:CommonClassAMember", 9_000), ("us-gaap:CommonClassBMember", 73_921_682)
    )
    eps = ix.EpsBase(date(2026, 7, 1), _Q3, 70_996_000)
    assert ix.classes_outside_eps(cover, eps) is None


def test_per_class_facts_naming_no_such_class():
    """CVNA: the EPS facts are Class A's alone (720,857,000) beside a Class B of 380,547,355 on the cover."""
    cover = _reading(
        ("us-gaap:CommonClassAMember", 722_713_745), ("us-gaap:CommonClassBMember", 380_547_355)
    )
    eps = ix.EpsBase(
        date(2026, 7, 1), _Q3, 720_857_000, (("us-gaap:CommonClassAMember", 720_857_000),)
    )
    out = ix.classes_outside_eps(cover, eps)
    assert out is not None and out.how == "per-class"
    assert [c.member for c in out.outside] == ["us-gaap:CommonClassBMember"]


_DOC = """<html><body><ix:header><ix:resources>
<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
</xbrli:entity><xbrli:period><xbrli:instant>2026-10-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="cA"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember
</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-10-31</xbrli:instant>
</xbrli:period></xbrli:context>
<xbrli:context id="cB"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassBMember
</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-10-31</xbrli:instant>
</xbrli:period></xbrli:context>
</ix:resources></ix:header>{body}</body></html>"""


def _fact(ctx: str, shown: str, **attrs: str) -> str:
    extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return (
        f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="{ctx}" '
        f'unitRef="shares"{extra}>{shown}</ix:nonFraction>'
    )


def test_printed_formats_scale_and_zero():
    doc = _DOC.format(
        body=_fact("cA", "1.234.567", format="ixt:num-comma-decimal")
        + _fact("cB", "-", format="ixt:fixed-zero")
    )
    cover = ix.read_cover(doc)
    assert cover is not None and _counts(cover) == {"us-gaap:CommonClassAMember": 1_234_567}
    assert not cover.multi_class  # a class with no shares outstanding is dropped, never summed
    scaled = ix.read_cover(_DOC.format(body=_fact("c1", "2.5", scale="6")))
    assert scaled is not None and scaled.figure == 2_500_000


def test_an_undimensioned_count_beside_classes_it_does_not_total_is_not_read():
    doc = _DOC.format(
        body=_fact("c1", "500") + _fact("cA", "100") + _fact("cB", "300")
    )  # 500 is not 100 + 300: which is the cover?
    assert ix.read_cover(doc) is None
    total = _DOC.format(body=_fact("c1", "400") + _fact("cA", "100") + _fact("cB", "300"))
    cover = ix.read_cover(total)
    assert cover is not None and cover.figure == 400 and cover.total is not None


def test_one_class_tagged_twice_with_two_counts_is_not_read():
    doc = _DOC.format(body=_fact("cA", "100") + _fact("cA", "101") + _fact("cB", "300"))
    assert ix.read_cover(doc) is None


def test_no_inline_xbrl_reads_nothing():
    text = "FORM 10-Q ... 12,345,678 shares of Class A common stock outstanding as of October 31, 2026."
    assert ix.read_cover(text) is None
    assert ix.eps_share_base(text, _Q3, 80, 371) is None

"""The fund launch radar's two pure reads, the stage derive's edge cases, and the structural guard that the
radar can never reach the call machinery. Cards here are made up ("Test Card A"); no DB."""

from __future__ import annotations

import subprocess
import sys
from datetime import date

from domain.enums import TermTier
from domain.thesis import TermSetEntry
from pipeline.fund_radar import build_report
from radar import fund_match
from radar.fund_match import CardInput, CompanyRef
from radar.fund_repo import SeriesView, SymbolRow
from radar.fund_stage import derive_launches

SIG = TermSetEntry(term="uranium", tier=TermTier.SIGNAL)
BRD = TermSetEntry(term="nuclear", tier=TermTier.BROAD)


def _view(
    name, *, sid="S1", kind="new", form="485APOS", day=date(2026, 11, 3), private=False, acc="A-1"
):
    return SeriesView(
        accession=acc,
        form=form,
        filer_name="Test Trust",
        filer_ciks=["0000000001"],
        public_date=day,
        private_to_public=private,
        effectiveness_date=None,
        series_key=sid,
        series_id=sid,
        series_name=name,
        kind=kind,
    )


# --- stages -------------------------------------------------------------------------------------------


def test_stage_derive_filed_effective_ticker_listed():
    views = [
        _view("Test Uranium ETF", acc="A-1", day=date(2026, 6, 1)),
        _view(
            "Test Uranium ETF", kind="existing", form="485BPOS", acc="B-1", day=date(2026, 8, 20)
        ),
        _view(
            "Test Uranium ETF", kind="existing", form="485BPOS", acc="B-2", day=date(2026, 9, 30)
        ),
    ]
    syms = [
        SymbolRow("S1", "C1", "TUR", "sec_ticker_file", date(2026, 8, 25)),
        SymbolRow("S1", "C1", "TUR", "nasdaq_directory", date(2026, 9, 2)),
    ]
    (lc,) = derive_launches(views, syms)
    assert (lc.stage.filed, lc.stage.effective) == (date(2026, 6, 1), date(2026, 8, 20))
    assert (lc.stage.ticker, lc.stage.listed) == (date(2026, 8, 25), date(2026, 9, 2))
    assert lc.stage.current == "listed"


def test_stage_only_filed_and_a_series_never_announced_new_is_no_launch():
    (lc,) = derive_launches([_view("Test Fund")], [])
    assert lc.stage.current == "filed" and lc.stage.effective is None
    assert derive_launches([_view("Old Fund", kind="existing", form="485BPOS")], []) == []


def test_a_485bpos_before_the_filed_date_is_not_its_effective_stage():
    views = [
        _view("Test Fund", acc="A-1", day=date(2026, 6, 1)),
        _view("Test Fund", kind="existing", form="485BPOS", acc="B-0", day=date(2026, 1, 5)),
    ]
    (lc,) = derive_launches(views, [])
    assert lc.stage.effective is None


# --- the theme read -----------------------------------------------------------------------------------


def test_broad_alone_matches_with_tier_broad():
    m = fund_match.match_theme("Test Nuclear Power ETF", [SIG, BRD])
    assert m is not None and m.signal == () and m.broad == ("nuclear",) and m.tier == "broad"


def test_signal_wins_the_tier_when_both_hit_and_both_lists_are_kept():
    m = fund_match.match_theme("Test Uranium and Nuclear ETF", [SIG, BRD])
    assert m is not None and m.signal == ("uranium",) and m.broad == ("nuclear",)
    assert m.tier == "signal"
    only = fund_match.match_theme("Test Uranium Miners ETF", [SIG, BRD])
    assert only is not None and only.tier == "signal" and only.broad == ()


def test_a_term_in_neither_tier_never_matches():
    assert fund_match.match_theme("Test Bond Income ETF", [SIG, BRD]) is None


def test_theme_uses_the_matchers_word_boundaries():
    ind = TermSetEntry(term="IND", tier=TermTier.SIGNAL)
    assert fund_match.match_theme("Test INDEX Fund", [ind]) is None


# --- the name read ------------------------------------------------------------------------------------

WRAP = fund_match.load_wrapper_words()


def _co(ticker, name=None, link="Core"):
    return CompanyRef(ticker, name, link)


def test_two_letter_ticker_needs_a_wrapper_word():
    ai, on = _co("AI"), _co("ON")
    # an ordinary "AI" / "ON" in a thematic name is not a wrapper on the company
    assert fund_match.match_names("Defiance Sovereign AI ETF", [ai], wrapper_words=WRAP) == []
    assert fund_match.match_names("Test Upside ON Growth ETF", [on], wrapper_words=WRAP) == []
    # next to a wrapper word it is
    (hit,) = fund_match.match_names("Test 2X Long AI Daily ETF", [ai], wrapper_words=WRAP)
    assert (hit.ticker, hit.token, hit.via, hit.link) == ("AI", "AI", "ticker", "Core")
    assert fund_match.match_names("Test PowerMaxx ON ETF", [on], wrapper_words=WRAP)


def test_three_letter_ticker_matches_alone_and_case_sensitively():
    abc = _co("ABC")
    assert fund_match.match_names("Test ABC Strategy ETF", [abc], wrapper_words=WRAP)
    assert fund_match.match_names("Test Abc Strategy ETF", [abc], wrapper_words=WRAP) == []
    assert fund_match.match_names("Test ABCD Strategy ETF", [abc], wrapper_words=WRAP) == []


def test_wrapper_words_load_from_the_editable_file():
    assert "PowerMaxx" in WRAP and "Covered Call" in WRAP and "2X" in WRAP


def test_company_name_length_rule_and_suffix_stripping():
    assert fund_match.clean_company_name("Example Holdings Inc") == "Example"
    assert fund_match.clean_company_name("Big Widget Co.") == "Big Widget"
    assert fund_match.clean_company_name("Acme Corp Class A") == "Acme"
    assert fund_match.name_is_distinctive("Big Widget")  # two words
    assert fund_match.name_is_distinctive("Example")  # one word, 7 letters
    assert not fund_match.name_is_distinctive("Acme")  # one short word
    short = _co("QQQQQ", "Acme Corp")
    long_ = _co("ZZZZZ", "Example Holdings Inc")
    two = _co("YYYYY", "Big Widget Co.")
    assert fund_match.match_names("Test Acme Income ETF", [short], wrapper_words=WRAP) == []
    (hit,) = fund_match.match_names("Test example Growth ETF", [long_], wrapper_words=WRAP)
    assert (hit.via, hit.token) == ("company_name", "example")
    assert fund_match.match_names("The Big Widget ETF", [two], wrapper_words=WRAP)


# --- matching launches --------------------------------------------------------------------------------


def test_private_to_public_launches_are_held_back_unless_asked():
    views = [
        _view("Test Uranium ETF", sid="S1", acc="A-1", private=True),
        _view("Test Uranium Miners ETF", sid="S2", acc="A-2"),
    ]
    launches = derive_launches(views, [])
    card = CardInput("t", "Test Card A", [SIG], [])
    assert [m.launch.key for m in fund_match.match_launches(launches, card)] == ["S2"]
    both = fund_match.match_launches(launches, card, include_private=True)
    assert {m.launch.key for m in both} == {"S1", "S2"}


def test_report_counts_by_filing_and_sponsor_and_holds_back_private():
    views = [
        _view("Test Uranium One ETF", sid="S1", acc="A-1"),
        _view("Test Uranium Two ETF", sid="S2", acc="A-1"),  # same filing: one batch
        _view("Test 2X Long AI Daily ETF", sid="S3", acc="A-2"),
        _view("Unrelated Bond ETF", sid="S4", acc="A-3"),
        _view("Test Uranium Old ETF", sid="S5", acc="A-4", private=True),
        _view("Test Nuclear Power ETF", sid="S6", acc="A-5"),  # BROAD only
    ]
    card = CardInput(
        "t", "Test Card A", [SIG, BRD], [CompanyRef("AI", "Test Intelligence Ltd", "Core")]
    )
    text = build_report(derive_launches(views, []), [card], today=date(2026, 11, 5))
    assert "== Test Card A" in text
    # two signal funds in ONE filing + one broad-only fund in another
    assert "theme: 2 filings from 1 sponsors (signal 1 · broad 1)" in text
    assert "THEME [signal]" in text and "SIGNAL: uranium" in text
    assert "THEME [broad]" in text and "BROAD: nuclear" in text
    assert "name hits: 1 filings from 1 sponsors" in text
    assert "new funds in the window: 6" in text
    assert "matched to at least one card: 4" in text
    assert "unmatched: 1" in text
    assert "private-to-public held back: 1 funds in 1 filings" in text


# --- the structural guard -----------------------------------------------------------------------------


def test_the_radar_never_imports_the_call_machinery():
    """A fund filing is a tape and a display, never a trigger: importing every radar module and the CLI must
    not pull `calls`, `signals` or the LLM seam in (not even transitively)."""
    code = (
        "import sys, radar.funds, radar.fund_match, radar.fund_stage, radar.fund_repo,"
        " radar.fund_header, pipeline.fund_radar\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('calls', 'signals', 'llm'))\n"
        "print(','.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, f"fund radar imported the call path: {proc.stdout.strip()}"


# --- company-name read: collision rules and the legal-form recall side (made-up companies throughout) -------

# an injected, made-up stoplist (the shipped file is checked separately below)
STOP = frozenset({"vexor", "zeta labs", "kyz"})


def test_a_cleaned_name_that_is_a_wrapper_word_never_matches():
    income = _co("QQQQQ", "Income Corp")  # cleaned name "Income" IS a wrapper word
    name = "Acme 2X Long Zorbex Daily Income ETF"
    assert fund_match.match_names(name, [income], wrapper_words=WRAP) == []


def test_a_single_word_on_the_stoplist_never_matches():
    vexor = _co("QQQQQ", "Vexor Inc")
    name = "Test Covered Call & Growth Vexor ETF"
    assert fund_match.match_names(name, [vexor], wrapper_words=WRAP, stopwords=STOP) == []
    # the same word is matchable when it is not on the list ...
    assert fund_match.match_names(name, [vexor], wrapper_words=WRAP, stopwords=frozenset())
    # ... and inside a longer distinctive name it still matches
    two = _co("YYYYY", "Vexor Capital Partners Inc")
    assert fund_match.match_names(
        "The Vexor Capital Partners ETF", [two], wrapper_words=WRAP, stopwords=STOP
    )


def test_trademark_marks_are_stripped_before_matching():
    tm = _co("TM")
    team = _co("TEAMX", "Springfield Foxes Holdings, Inc.")
    name = "Alpha Sports BaseballShares(TM) 2x Springfield Foxes ETF"
    hits = fund_match.match_names(name, [tm, team], wrapper_words=WRAP, stopwords=STOP)
    assert [(h.ticker, h.via) for h in hits] == [("TEAMX", "company_name")]
    assert fund_match.strip_marks("Foo™ Bar(R) Baz℠ Qux(sm)") == "Foo Bar Baz Qux"


def test_a_hyphen_is_part_of_the_word_for_the_name_read():
    q, zen = _co("Q"), _co("ZENX")
    hits = fund_match.match_names(
        "Q-ZEN 2X LONG ZENX DAILY ETF", [q, zen], wrapper_words=WRAP, stopwords=STOP
    )
    assert [h.ticker for h in hits] == ["ZENX"]  # the 1-letter ticker does not match inside "Q-ZEN"
    acme = _co("ACMQ", "Plexor Index Inc")
    assert (
        fund_match.match_names(
            "Test Plexor Index-500 Enhanced Yield ETF", [acme], wrapper_words=WRAP, stopwords=STOP
        )
        == []
    )
    # the same name beside a space matches
    assert fund_match.match_names(
        "Test Plexor Index 500 ETF", [acme], wrapper_words=WRAP, stopwords=STOP
    )


def test_the_stoplist_covers_every_matched_token_not_only_one_word_names():
    two_word = _co("ZLXX", "Zeta Labs Group Inc")  # cleaned: a two-word name on the list
    assert (
        fund_match.match_names("Zeta Labs Test ETF", [two_word], wrapper_words=WRAP, stopwords=STOP)
        == []
    )
    ticker = _co("KYZ")  # a ticker on the list
    assert (
        fund_match.match_names("Test Staked KYZ ETF", [ticker], wrapper_words=WRAP, stopwords=STOP)
        == []
    )
    assert fund_match.match_names(
        "Test Staked KYZ ETF", [ticker], wrapper_words=WRAP, stopwords=frozenset()
    )


def test_the_shipped_stoplist_loads_and_holds_sponsor_brands():
    shipped = fund_match.load_stopwords()
    assert {"ishares", "vanguard", "growth"} <= shipped


def test_legal_forms_are_stripped_before_the_length_rule():
    assert fund_match.clean_company_name("Zorbex Group N.V.") == "Zorbex"
    assert fund_match.clean_company_name("Example S.A.") == "Example"
    assert fund_match.clean_company_name("The Widget Factory & Co") == "Widget Factory"
    assert fund_match.clean_company_name("Acme SpA") == "Acme"
    zor = _co("ZRBX", "Zorbex Group N.V.")
    # matched by the cleaned company name (the ticker is absent from the fund name)
    (hit,) = fund_match.match_names(
        "Acme 2X Long Zorbex Daily ETF", [zor], wrapper_words=WRAP, stopwords=STOP
    )
    assert (hit.via, hit.token) == ("company_name", "Zorbex")


def test_s1_filer_filter_uses_word_boundaries():
    from ingest.edgar.dailyindex import IndexFiling
    from radar.funds import keep_filing

    def _s1(company: str):
        return IndexFiling(
            "1", company, "S-1", date(2026, 11, 3), "edgar/data/1/0000000001-26-000001.txt"
        )

    assert keep_filing(_s1("Canary Staked INJ ETF"))
    assert keep_filing(_s1("Example Digital Trust"))
    assert keep_filing(_s1("Test Commodity Fund, LP"))
    for bad in ("Refund Holdings Inc", "Fundamental Software Corp", "Trustmark Bancorp"):
        assert not keep_filing(_s1(bad))

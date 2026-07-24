"""Annual-cover shares (Retrieval Slice 1) — one test per hazard, each tied to a REAL name.

THIS MODULE IS THE ORACLE. Its expected values were hand-verified against the filings themselves (not
against companyfacts — checking a source against itself proves nothing) on 2026-07-23; the dated
measurement that produced them is PR #221 and the rules they encode are canon in
``docs/WORKBENCH_EXTRACTION.md`` ("The annual-cover path"). The fixtures under
``fixtures/sec_extractor/annual/`` are REAL filing text, trimmed with the trims verified to reproduce the
full-document result before commit. Both sides are pinned — frozen input, frozen expectation — so these
assertions stay true regardless of what the issuers file next. OFFLINE — no network, no DB.

Fixture ages are pinned to ``_TODAY`` (the verification date) so staleness is deterministic.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from domain.extraction import Tier
from ingest.edgar.annual_shares import annual_shares_for_security, extract_annual_shares
from ingest.edgar.converts import clean_filing_text

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "annual"
_TODAY = date(
    2026, 7, 23
)  # the answer key's measurement date — ages in the notes are deterministic

# The oracle rows exercised here (value = the FILING's cover, never companyfacts-checked-against-itself).
# report_date per name is the real PERIOD OF REPORT from submissions.
_KEY = {
    "ASML": (385_417_665, date(2025, 12, 31)),
    "CAMT": (45_828_133, date(2025, 12, 31)),
    "TSM": (25_932_524_521, date(2025, 12, 31)),
    "NVS": (1_908_151_679, date(2025, 12, 31)),
    "IMOS": (699_983_126, date(2025, 12, 31)),
}


def _text(fname: str) -> str:
    return (_FX / fname).read_text(encoding="utf-8")


def _cf(name: str) -> dict:
    return json.loads((_FX / f"cf-{name}.json").read_text(encoding="utf-8"))


def _subs(name: str) -> dict:
    return json.loads((_FX / f"subs-{name}.json").read_text(encoding="utf-8"))


def _one(
    fname: str,
    *,
    cf: dict | None = None,
    report_date: date = date(2025, 12, 31),
    form: str = "20-F",
    raw: bool = False,
    today: date = _TODAY,
    has_f6: bool = False,
):
    text = _text(fname)
    if raw:
        text = clean_filing_text(text)  # the production cleaning IS part of what these tests prove
    facts = extract_annual_shares(
        cf,
        text,
        annual_ref=f"https://sec.gov/{fname}",
        annual_form=form,
        report_date=report_date,
        today=today,
        has_f6_filing=has_f6,
    )
    assert len(facts) <= 1
    return facts[0] if facts else None


# ---------------------------------------------------------------------------------------------------------
# the answer key — filing-derived values, exact
# ---------------------------------------------------------------------------------------------------------


def test_annual_cover_matches_answer_key():
    """ASML / CAMT / TSM / NVS emit their hand-verified cover counts EXACTLY (IMOS rides the raw-fixture
    test below — same oracle). companyfacts agrees for all four, so no disagreement flag fires."""
    for name in ("ASML", "CAMT", "TSM", "NVS"):
        want, rd = _KEY[name]
        f = _one(f"{name}-20f.txt", cf=_cf(name), report_date=rd)
        assert f is not None, name
        assert f.value == want, name
        assert f.tier is Tier.FLAG and f.source == "annual-cover", name
        assert f.event_date == rd, name
        assert "source-disagreement" not in f.flags, name
        assert "companyfacts agrees" in f.note, name


def test_space_separated_thousands_parsed():
    """NVS renders `1 908 151 679` — SPACE-separated thousands (European convention, Finding C). A
    comma-only number regex silently returns no match — the classic invisible miss."""
    f = _one("NVS-20f.txt", cf=_cf("NVS"))
    assert f is not None and f.value == 1_908_151_679
    assert "1 908 151 679" in f.located_passages[0].excerpt


def test_tag_split_word_tolerated():
    """IMOS's cover renders `699,983,126 Comm on Shares` after tag-stripping — "Common" is split across
    an HTML boundary (``clean_filing_text`` replaces every tag with a SPACE; production behaviour, not a
    probe artifact). The instruction cue itself must survive the same treatment. RAW-HTML fixture: the
    cleaning step runs in-test, exactly as in production."""
    cleaned = clean_filing_text(_text("IMOS-20f-raw.htm"))
    assert "Comm on Shares" in cleaned  # the split shape is really there
    f = _one("IMOS-20f-raw.htm", cf=_cf("IMOS"), raw=True)
    assert f is not None and f.value == _KEY["IMOS"][0]


def test_rsquo_possessive_is_matched():
    """EHVVF renders ``issuer&rsquo;s`` — only ``clean_filing_text``'s html.unescape normalises the
    possessive, so the cue regex can match it. A hand-rolled tag-strip WITHOUT the unescape misses
    EHVVF (and CMND) invisibly — pinned here as the counterfactual."""
    raw = _text("EHVVF-20f-raw.htm")
    assert "&rsquo;" in raw  # the entity is really in the wire bytes
    f = _one("EHVVF-20f-raw.htm", raw=True)
    assert f is not None and f.value == 1_482_014_555
    # the counterfactual: tags stripped, whitespace collapsed, but NO entity unescape -> no match
    hand_rolled = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
    assert (
        extract_annual_shares(
            None,
            hand_rolled,
            annual_ref="x",
            annual_form="20-F",
            report_date=date(2025, 12, 31),
            today=_TODAY,
        )
        == []
    )


def test_40f_registrant_wording_is_matched():
    """The 40-F cover says *"of the Registrant's classes"* where the 20-F says *"of the issuer's"* —
    and OGI/DRUG omit the *"each of"*. A first draft matching only `issuer` silently dropped OGI,
    CRLBF and DRUG — three real names, invisibly (#9)."""
    for fname, want, rd in (
        ("OGI-40f.txt", 134_461_029, date(2025, 9, 30)),
        ("CRLBF-40f.txt", 158_940_757, date(2025, 12, 31)),
        ("DRUG-40f.txt", 7_635_789, date(2025, 9, 30)),
    ):
        f = _one(fname, form="40-F", report_date=rd)
        assert f is not None, fname
        assert f.value == want, fname


def test_cover_only_names_without_companyfacts():
    """GLAS, CRLBF, TRSG have NO companyfacts at all (data.sec.gov 404s) — the cover-only path must
    emit from the document alone, saying so, with no disagreement claim against a source that doesn't
    exist."""
    for fname, form, want in (
        ("GLAS-40f.txt", "40-F", 75_282_908),
        ("CRLBF-40f.txt", "40-F", 158_940_757),
        ("TRSG-20f.txt", "20-F", 11_793_485),
    ):
        f = _one(fname, cf=None, form=form)
        assert f is not None, fname
        assert f.value == want, fname
        assert "source-disagreement" not in f.flags, fname
        assert "No companyfacts to compare" in f.note, fname


# ---------------------------------------------------------------------------------------------------------
# neither source dominates — the later-as-of rule, from both directions
# ---------------------------------------------------------------------------------------------------------


def test_nvmi_prefers_fresher_cover_over_lagging_companyfacts():
    """Finding B: NVMI's 2025 20-F cover states 31,780,111 while companyfacts still serves 29,278,401
    as of 2024-12-31 (the prior year's filing — a 569-day-old count, understating the cap by 7.9%).
    The cover's later as-of wins; the disagreement is flagged with BOTH values in the note."""
    f = _one("NVMI-20f.txt", cf=_cf("NVMI"))
    assert f is not None
    assert f.value == 31_780_111  # never the lagging 29,278,401
    assert f.event_date == date(2025, 12, 31)
    assert "source-disagreement" in f.flags
    assert "29,278,401" in f.note and "2024-12-31" in f.note


def test_cmnd_prefers_fresher_companyfacts_over_cover():
    """THE test that proves the rule is *later-as-of-wins*, NOT *prefer-the-document*: CMND's cover
    (report 2025-10-31) reads 1,499,838, but companyfacts carries 158,076 as of 2026-01-19 — FRESHER
    than the filing. Without this case a lazy "cover always wins" shortcut passes everything else.
    The fact still carries the located COVER passage (the value's provenance trail stays visible).
    """
    f = _one("CMND-20f.txt", cf=_cf("CMND"), report_date=date(2025, 10, 31))
    assert f is not None
    assert f.value == 158_076  # companyfacts, the later as-of
    assert f.event_date == date(2026, 1, 19)
    assert "source-disagreement" in f.flags
    assert "1,499,838" in f.note and "158,076" in f.note  # both values stated where ratified
    assert f.located_passages and f.located_passages[0].kind == "cover"


# ---------------------------------------------------------------------------------------------------------
# never sum a cover — subsets and multi-class
# ---------------------------------------------------------------------------------------------------------


def test_cajpy_ads_subset_is_not_summed():
    """CAJPY's cover reads *"1,015,513,368 shares of common stock, **including** 17,371,450 ADSs"* —
    the second number is a SUBSET, not a second class; summing overstates by 1.7%. ADRs are common
    among foreign filers, so this recurs. Also the anti-position-bound guard: CAJPY's genuine cover
    sits ~52k chars deep (behind the inline-XBRL context block) — any offset threshold drops it."""
    f = _one("CAJPY-20f.txt", report_date=date(2022, 12, 31))
    assert f is not None
    assert f.value == 1_015_513_368
    assert f.value != 1_032_884_818  # the forbidden sum (first + the ADS subset)
    assert "multi-value-cover" in f.flags
    assert "including 17,371,450 ADSs" in f.located_passages[0].excerpt  # the subset is READABLE
    assert f.located_passages[0].offset is not None and f.located_passages[0].offset > 50_000


def test_crlbf_multiclass_cover_flags_not_sums():
    """CRLBF's cover carries FOUR counts (genuinely multi-class: Special Subordinate / Subordinate /
    Super / Multiple Voting). The FIRST is offered with `multi-value-cover`; composition is the
    operator's ratify against the passage — never an automatic sum."""
    f = _one("CRLBF-40f.txt", form="40-F")
    assert f is not None
    assert f.value == 158_940_757  # the first class, never any sum
    assert f.value != 158_940_757 + 343_232_815 + 81_492 + 500_000
    assert "multi-value-cover" in f.flags
    assert "343,232,815" in f.located_passages[0].excerpt  # the other classes are visible to ratify


# ---------------------------------------------------------------------------------------------------------
# fail closed — the direct-count secondary cover (PBM) and the synthetic unread cover
# ---------------------------------------------------------------------------------------------------------


def test_pbm_direct_count_cover_recovered_never_the_eps_note():
    """PBM uses the DIRECT-COUNT cover phrasing — *"The number of the issuer's outstanding common shares
    … was 2,293,277"* — matched by the SECONDARY instruction, while a cue-LOOKALIKE sits ~400k chars deep
    (an EPS note, *"number of outstanding shares - basic and diluted"*). The secondary must recover the
    real cover and NEVER the EPS note (it lacks *"the issuer's"*): the confidently-wrong number the probe's
    loose pattern returned there must stay impossible. The fixture RETAINS both hazard regions — asserted
    so a future trim can't hollow the test out (spec §8.4)."""
    text = _text("PBM-20f.txt")
    # fixture integrity: the trim trap — both regions the test exists to guard must still be present
    assert "number of outstanding shares - basic and diluted" in text.lower()
    assert "outstanding common shares as of March 31, 2026 was 2,293,277" in text
    f = _one("PBM-20f.txt", cf=None, report_date=date(2026, 3, 31), form="20-F")
    assert f is not None
    assert f.value == 2_293_277  # the real cover — NEVER an EPS figure (552,282 / 30,887 / 1,744)
    assert f.tier is Tier.FLAG and f.source == "annual-cover"
    assert "2,293,277" in f.located_passages[0].excerpt


def test_wrapper_stamps_cover_not_located_when_no_instruction_matches():
    """The wrapper distinguishes an UNREAD cover from a MISSING filing (SKHY): an annual filing EXISTS
    but NEITHER cover instruction matched -> `cover-not-located` (the name is UNREAD, not empty), and
    companyfacts is deliberately NOT served alone (no passage -> no fact — the Option-B contract). No
    real dark name currently trips this (PBM is recovered by the secondary pattern), so the empty state
    is proven with a synthetic cover that uses neither instruction — the case still guards future
    filings with unrecognised cover phrasing."""
    subs = {
        "filings": {
            "recent": {
                "form": ["20-F"],
                "accessionNumber": ["0009999999-26-000001"],
                "primaryDocument": ["synthetic-20f.htm"],
                "filingDate": ["2026-03-31"],
                "reportDate": ["2026-03-31"],
            }
        }
    }
    unreadable = (
        "Cover Page. This annual report states no share count in any recognised instruction."
    )
    client = _FakeClient(subs=subs, texts={"synthetic-20f.htm": unreadable})
    res = annual_shares_for_security(client, 1234, today=_TODAY)
    assert res.facts == [] and res.empty_reason == "cover-not-located"


# ---------------------------------------------------------------------------------------------------------
# the implausibility floor — flagged, never suppressed and never silently served
# ---------------------------------------------------------------------------------------------------------


def test_qntm_implausible_count_is_flagged_not_served_silently():
    """QNTM's companyfacts `dei` claims **12 shares** against a cover of 3,887,729. As measured, the
    cover's later as-of wins so 12 is never served. The counterfactual pins the backstop: were garbage
    the LATER value, it is emitted WITH `implausible-count` + both candidates in the note — flagged,
    never suppressed (#9) and never silent."""
    # (a) the real shape: cover wins; 12 never reaches the value
    f = _one("QNTM-20f.txt", cf=_cf("QNTM"))
    assert f is not None
    assert f.value == 3_887_729
    assert "implausible-count" not in f.flags  # the winning value is plausible
    assert "source-disagreement" in f.flags  # ...but the 12 is visibly named
    assert "12" in f.note
    # (b) the counterfactual: garbage with a LATER as-of wins -> flagged implausible, both values shown
    garbage_cf = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{"end": "2026-02-01", "val": 12, "filed": "2026-02-10"}]}
                }
            }
        }
    }
    g = _one("QNTM-20f.txt", cf=garbage_cf)
    assert g is not None
    assert g.value == 12  # emitted — a suppressed value is worse than a flagged one
    assert "implausible-count" in g.flags and "source-disagreement" in g.flags
    assert "3,887,729" in g.note  # the cover candidate stays visible where ratified


# ---------------------------------------------------------------------------------------------------------
# selection across forms — the CRDL short-circuit trap
# ---------------------------------------------------------------------------------------------------------


class _Cf404(Exception):
    """A duck-typed httpx.HTTPStatusError: `.response.status_code == 404` (companyfacts absent)."""

    def __init__(self):
        super().__init__("404 companyfacts")
        self.response = type("R", (), {"status_code": 404})()


class _FakeClient:
    """A cache-shaped fake: submissions + per-document texts keyed by primary-doc basename; the
    companyfacts fetch 404s unless a dict is given. Requesting an UNCONFIGURED document raises — so a
    wrong-form selection fails loudly instead of silently reading the wrong filing."""

    def __init__(self, *, subs: dict, texts: dict[str, str] | None = None, cf: dict | None = None):
        self._subs, self._texts, self._cf = subs, texts or {}, cf

    def get_json(self, url: str, cache_key: str) -> dict:
        if cache_key.startswith("submissions/"):
            return self._subs
        if cache_key.startswith("companyfacts/"):
            if self._cf is None:
                raise _Cf404()
            return self._cf
        raise AssertionError(f"unexpected get_json: {cache_key}")

    def get_text(self, url: str, cache_key: str) -> str:
        doc = cache_key.rsplit("/", 1)[-1]
        if doc not in self._texts:
            raise AssertionError(f"unexpected document fetch: {cache_key}")
        return self._texts[doc]


def test_crdl_picks_latest_across_20f_and_40f():
    """CRDL files BOTH forms: a 2023 20-F and a 2025 40-F. `filings_of('20-F') or filings_of('40-F')`
    short-circuits on the non-empty 20-F list and reads a two-year-old filing (the answer-key probe's
    own bug). Selection must compare report dates across BOTH forms. The fake serves ONLY the 40-F
    text, so a 20-F pick fails loudly."""
    client = _FakeClient(
        subs=_subs("CRDL"),
        texts={"crdl-20251231x40f.htm": _text("CRDL-40f.txt")},
    )
    res = annual_shares_for_security(client, 1234, today=_TODAY)
    assert res.empty_reason is None and len(res.facts) == 1
    f = res.facts[0]
    assert f.value == 100_257_009  # the 2025 40-F cover — companyfacts agreed on the real name
    assert f.event_date == date(2025, 12, 31)  # NOT 2023-12-31
    assert "40-F" in f.note
    assert "crdl-20251231x40f.htm" in f.source_ref


def test_no_annual_filing_returns_no_annual_filing():
    """SKHY (a brand-new F-1/DRS listing) and AGNPF have NO 20-F/40-F either — the honest-empty set,
    preserved: `no-annual-filing`, no fact, and no document/companyfacts fetch even attempted (the
    fake raises on any)."""
    for name in ("SKHY", "AGNPF"):
        client = _FakeClient(subs=_subs(name))
        res = annual_shares_for_security(client, 1234, today=_TODAY)
        assert res.facts == [] and res.empty_reason == "no-annual-filing", name


# ---------------------------------------------------------------------------------------------------------
# the structural bound + the passage contract + staleness
# ---------------------------------------------------------------------------------------------------------


def test_annual_path_never_returns_auto():
    """STRUCTURAL: the pre-fill tier is unreachable from this path — the token appears NOWHERE in the
    module source (the display-signals idiom: unable by construction, not by discipline), and every
    fact any fixture emits is FLAG."""
    import ingest.edgar.annual_shares as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "AUTO" not in src  # the token itself — not just `Tier.AUTO`
    for fname, rd, form in (
        ("ASML-20f.txt", date(2025, 12, 31), "20-F"),
        ("CMND-20f.txt", date(2025, 10, 31), "20-F"),
        ("QNTM-20f.txt", date(2025, 12, 31), "20-F"),
        ("OGI-40f.txt", date(2025, 9, 30), "40-F"),
        ("CAJPY-20f.txt", date(2022, 12, 31), "20-F"),
    ):
        f = _one(fname, report_date=rd, form=form)
        assert f is not None and f.tier is Tier.FLAG, fname


def test_every_flag_carries_a_located_passage():
    """The Option-B contract: no passage -> no fact. Every emitting fixture carries its located cover
    passage (kind `cover`, the instruction as anchor, the offset recorded for audit), and the base
    `annual-cover` flag leads."""
    for fname, rd, form in (
        ("ASML-20f.txt", date(2025, 12, 31), "20-F"),
        ("NVS-20f.txt", date(2025, 12, 31), "20-F"),
        ("NVMI-20f.txt", date(2025, 12, 31), "20-F"),
        ("GLAS-40f.txt", date(2025, 12, 31), "40-F"),
        ("TRSG-20f.txt", date(2025, 12, 31), "20-F"),
        ("DRUG-40f.txt", date(2025, 9, 30), "40-F"),
        ("CRLBF-40f.txt", date(2025, 12, 31), "40-F"),
        ("CAJPY-20f.txt", date(2022, 12, 31), "20-F"),
    ):
        f = _one(fname, report_date=rd, form=form)
        assert f is not None, fname
        assert f.located_passages, fname
        p = f.located_passages[0]
        assert p.kind == "cover" and p.offset is not None, fname
        assert "outstanding shares of" in p.anchor, fname
        assert f.flags and f.flags[0] == "annual-cover", fname


def test_stale_cover_ages_against_today_and_marks_the_exception():
    """`stale-cover` fires on AGE (chosen as-of vs `today`), mirroring the FE's >~6mo badge: ASML's
    2025-12-31 count is 204 days old at the answer key's measurement date -> flagged, with the age in
    the note; the SAME count read in January is current -> NOT flagged (honest loudness — the flag
    marks the exception, and time is a parameter, never an implicit now)."""
    stale = _one("ASML-20f.txt", cf=_cf("ASML"), today=date(2026, 7, 23))
    assert stale is not None and "stale-cover" in stale.flags
    assert "204 days old" in stale.note
    fresh = _one("ASML-20f.txt", cf=_cf("ASML"), today=date(2026, 1, 15))
    assert fresh is not None and "stale-cover" not in fresh.flags


# ---------------------------------------------------------------------------------------------------------
# the ADS ratio (spec §10) — apply where READ, SUPPRESS where not. The cover states ORDINARY shares;
# the price feed carries the ADS price; a defaulted 1:1 is a silent multiplicative error.
# ---------------------------------------------------------------------------------------------------------


def test_tsm_prose_ratio_is_read_and_f6_absence_never_implies_no_adr():
    """TSM states *"each ADS represents five (5) common shares"* in prose — word + parenthesized
    digits agreeing — and has NO F-6-family filing on record. The ratio must still be read: F-6 is a
    POSITIVE evidence signal only; inferring "no F-6 ⇒ not an ADR" would have priced TSM 5x high
    ($10.9T instead of ~$2.2T — the defect that motivated §10)."""
    f = _one("TSM-20f.txt", cf=_cf("TSM"), has_f6=False)
    assert f is not None
    assert f.ads_ratio == 5 and f.ads_ratio_status == "known"
    assert "ADS ratio 5:1" in f.note
    assert f.value == 25_932_524_521  # the fact stays the TRUE ordinary count — never pre-divided


def test_simo_securities_table_ratio_is_read():
    """SIMO's 4:1 sits in the cover's *securities-registered table* — *"American Depositary Shares,
    each representing four ordinary shares"* — not a prose sentence; the form the first (prose-only)
    parser missed. The division's own evidence rides as a second located passage (#6)."""
    f = _one("SIMO-20f.txt", has_f6=True)
    assert f is not None
    assert f.ads_ratio == 4 and f.ads_ratio_status == "known"
    assert any("each representing four" in p.excerpt for p in f.located_passages)


def test_adxn_nounless_table_row_and_change_history_read_120():
    """ADXN's registration row reads *"… The Nasdaq Stock Market LLC each representing 120 ordinary
    shares"* — the ADS noun sits in a DIFFERENT table cell, so a parser demanding an adjacent noun
    misses it (the region-scoped table arm exists for exactly this). Its prose also narrates a ratio
    CHANGE ("from one ADS representing six shares to a new ratio of … one hundred and twenty") — the
    historical from-arm must not read as a conflict. 120:1 is the current, correct ratio."""
    f = _one("ADXN-20f.txt", report_date=date(2025, 12, 31), has_f6=True)
    assert f is not None
    assert f.ads_ratio == 120 and f.ads_ratio_status == "known"


def test_bway_ratio_change_history_is_not_a_conflict():
    """BWAY's prose narrates *"changed from one ADS representing two ordinary shares to a new ratio of
    one ADS representing one ordinary share"* while its registration row says 1:1. The FROM-arm is
    history: reading it as a live value would manufacture a {1, 2} conflict and wrongly suppress a
    readable 1:1 name."""
    f = _one("BWAY-20f.txt", report_date=date(2025, 12, 31))
    assert f is not None
    assert f.ads_ratio == 1 and f.ads_ratio_status == "known"


def test_imos_twenty_to_one_is_read():
    """IMOS: 20 ordinary shares per ADS ("each ADS represents 20 ordinary shares") — the ratio whose
    omission displayed a $44.1B cap for a ~$2.2B company."""
    f = _one("IMOS-20f-raw.htm", cf=_cf("IMOS"), raw=True, has_f6=True)
    assert f is not None
    assert f.ads_ratio == 20 and f.ads_ratio_status == "known"


def test_one_to_one_ratio_names_read_one():
    """NVS / ARM / KYOCY are genuinely 1:1 — the ratio is READ (status known, divisor 1), never merely
    assumed: a 1:1 read and a no-evidence 1:1 assumption are different provenance."""
    for fname, kwargs in (
        ("NVS-20f.txt", dict(cf=_cf("NVS"), has_f6=True)),
        ("ARM-20f.txt", dict(has_f6=True)),
        ("KYOCY-20f.txt", dict(report_date=date(2018, 3, 31), has_f6=True)),
    ):
        f = _one(fname, **kwargs)
        assert f is not None, fname
        assert f.ads_ratio == 1 and f.ads_ratio_status == "known", fname


def test_adr_evidence_without_readable_ratio_is_unread_and_flagged():
    """The fail-closed floor: ADR evidence with NO defensible ratio -> `unread` + the
    `ads-ratio-unread` flag (the scorer withholds the cap; a guessed 1:1 would be silently wrong).
    SPRC: an F-6 on file but no ratio statement anywhere. EVO: a FRACTIONAL ratio ("each representing
    one-half of one ordinary share") — real, but never a divisor we apply."""
    sprc = _one("SPRC-20f.txt", has_f6=True)
    assert sprc is not None
    assert sprc.ads_ratio is None and sprc.ads_ratio_status == "unread"
    assert "ads-ratio-unread" in sprc.flags
    assert "WITHHELD" in sprc.note and "F-6" in sprc.note
    evo = _one("EVO-20f.txt", has_f6=True)
    assert evo is not None
    assert evo.ads_ratio is None and evo.ads_ratio_status == "unread"
    assert "ads-ratio-unread" in evo.flags


def test_xtlb_conflicting_in_document_ratios_are_unread():
    """XTLB's registration row says 100:1 while its 2026-financing prose says 400:1 ("Each ADS
    represents four hundred (400) ordinary shares" — a mid-cycle ratio change). DISTINCT values in one
    document = a conflict the parser must not adjudicate: unread, cap withheld, both values named.
    (Also pins compound word-numbers: a single-token map read "four hundred" as 4.)"""
    f = _one("XTLB-20f.txt", report_date=date(2025, 12, 31), has_f6=True)
    assert f is not None
    assert f.ads_ratio is None and f.ads_ratio_status == "unread"
    assert "CONFLICTING ratios stated (100, 400)" in f.note


def test_no_adr_evidence_reads_not_applicable():
    """No ADS/ADR evidence at all (ASML, CAMT, NVMI — ordinary shares listed directly): status None,
    the 1:1 assumption recorded in the note so it rides provenance into the scored read (#6)."""
    for fname in ("ASML-20f.txt", "CAMT-20f.txt", "NVMI-20f.txt"):
        f = _one(fname)
        assert f is not None, fname
        assert f.ads_ratio is None and f.ads_ratio_status is None, fname
        assert "No ADS/ADR evidence" in f.note, fname
        assert "ads-ratio-unread" not in f.flags, fname

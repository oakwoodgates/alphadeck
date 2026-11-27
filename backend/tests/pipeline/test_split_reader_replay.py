"""The split reader's old-vs-new replay (``scripts/split_reader_replay.py``, split-reader misreads PR-A): it reads
each filing exactly as the pipeline does (``pipeline.backfill_splits.screen_of`` / ``extract_for``), finds nothing
when both readers are one, and fails on a reading that became MORE confident or touches a guarded filing. Pure: real
fixture filings (``tests/fixtures/splits``), no database, no network."""

from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ingest.edgar import split_filing
from pipeline.backfill_splits import FilingRef, extract_for
from tests.split_fixtures import FX

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "split_reader_replay.py"
_READER = Path(split_filing.__file__)


@pytest.fixture(scope="module")
def replay():
    spec = importlib.util.spec_from_file_location("split_reader_replay", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["split_reader_replay"] = mod
    spec.loader.exec_module(mod)
    return mod


def _forms(tmp_path: Path, *accessions: str) -> Path:
    """The fixture submissions as the EDGAR cache keeps them: ``<acc>/<acc>.txt``."""
    root = tmp_path / "forms"
    for acc in accessions:
        dest = root / acc / f"{acc}.txt"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(gzip.decompress((FX / "sec" / f"{acc}.txt.gz").read_bytes()))
    return root


def test_every_fixture_filing_is_read_as_the_pipeline_reads_it(replay):
    items = replay.discover([FX / "sec"], {})
    read = 0
    for item in items:
        screen, why = replay.screen_for(item)
        if screen is None or item.filed is None:
            continue
        text = replay.read_text(item.path)
        docs = replay.documents(item, split_filing, text)
        ref = FilingRef(accession=item.key, form=item.form, filed=item.filed, items=item.items)
        assert replay.extract(split_filing, docs, item, screen, ()) == extract_for(
            docs, ref
        ), item.key
        read += 1
    assert read >= 70
    by_key = {i.key: i for i in items}
    cycu = by_key["0001628280-26-048496"]
    assert (cycu.form, cycu.filed, cycu.items, cycu.cik) == (
        "8-K",
        date(2026, 7, 16),
        ("3.01", "7.01", "9.01"),
        "1868419",
    )
    assert replay.screen_for(cycu) == ("catch_all", None)
    assert replay.screen_for(by_key["0001641172-25-007892"]) == ("restated", None)  # 5.03 / 9.01
    assert replay.screen_for(by_key["0001213900-26-098435"]) == ("restated", None)  # a 6-K


def test_the_same_reader_twice_changes_nothing(replay, tmp_path):
    forms = _forms(tmp_path, "0001640334-26-001421", "0001213900-26-053864", "0001628280-26-048496")
    out = tmp_path / "out"
    assert (
        replay.main(
            ["--old", str(_READER), "--new", str(_READER), "--forms", str(forms), "--out", str(out)]
        )
        == 0
    )
    assert (out / "changes.jsonl").read_text(encoding="utf-8") == ""
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "read: 3" in summary and "changed readings: 0" in summary and "PASS" in summary


_MORE_CONFIDENT = """
from decimal import Decimal

from ingest.edgar.split_filing import FilingDocument, KnownSplit, SplitExtraction, submission_documents


def extract_split(documents, *, filed=None, wide=False, known=()):
    return SplitExtraction(ratio_from=Decimal(7), ratio_to=Decimal(1))


extract_split_screened = extract_split
"""


def test_a_reader_that_reads_more_fails_the_replay(replay, tmp_path):
    """ARTL's 1-for-9 read as 7 -> 1 is a ratio changed (and a guard hit: the guard lists it); MI's 6-K with no
    ratio read as one is an abstain -> ratio. Either fails the replay."""
    new = tmp_path / "reads_seven.py"
    new.write_text(_MORE_CONFIDENT, encoding="utf-8")
    forms = _forms(tmp_path, "0001640334-26-001421", "0001213900-26-053864")
    guard = tmp_path / "guard.txt"
    guard.write_text("0001640334-26-001421\n", encoding="utf-8")
    out = tmp_path / "out"
    argv = ["--old", str(_READER), "--new", str(new), "--forms", str(forms), "--guard", str(guard)]
    assert replay.main([*argv, "--out", str(out)]) == 1
    changes = {
        c["key"]: c
        for c in map(json.loads, (out / "changes.jsonl").read_text(encoding="utf-8").splitlines())
    }
    assert changes["0001640334-26-001421"]["class"] == "ratio changed"
    assert changes["0001640334-26-001421"]["guard"] is True
    assert changes["0001213900-26-053864"]["class"] == "abstain->ratio"
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "HIT 0001640334-26-001421: ratio changed" in summary and "FAIL" in summary


def _reading(
    ratio=("9", "1"), when="2026-08-31", kind="trading", reason=None, note=None, passage="p"
):
    return {
        "ratio_from": ratio and ratio[0],
        "ratio_to": ratio and ratio[1],
        "effective_date": when,
        "date_kind": kind,
        "abstain_reason": reason,
        "date_note": note,
        "document": "8-K",
        "passage": passage,
        "mentions": [],
    }


@pytest.mark.parametrize(
    ("new", "cls"),
    [
        (_reading(), None),
        (
            _reading(ratio=None, when=None, kind=None, reason="no split ratio found"),
            "ratio->abstain",
        ),
        (_reading(ratio=("18", "2")), "ratio changed"),
        (
            _reading(when=None, kind=None, note="no first-session or effective date stated"),
            "date dropped",
        ),
        (_reading(when="2026-09-01"), "date changed"),
        (_reading(kind="legal"), "kind changed"),
        (_reading(note="a note"), "reason only"),
        (_reading(passage="q"), "text only"),
    ],
)
def test_each_change_is_classed_by_what_moved(replay, new, cls):
    assert replay.classify(_reading(), new)[0] == cls


def test_a_ratio_or_a_date_gained_is_classed_so(replay):
    abstain = _reading(ratio=None, when=None, kind=None, reason="no split ratio found")
    assert (
        replay.classify(abstain, _reading(ratio=None, reason="no split ratio found"))[0]
        == "date added"
    )
    assert replay.classify(abstain, _reading())[0] == "abstain->ratio"


# --- --accept-listed: an abstain -> ratio equal to a split prod lists (framework-completion F4) --------------------

_NFT = "0001213900-26-053864"  # a 6-K the real reader abstains on (NFT Ltd, CIK 1958713, filed 2026-05-08)
_ARTL = "0001640334-26-001421"  # Artelo's 8-K (CIK 1621221): the real reader reads its 1-for-9
_NFT_CIK = "1958713"
_FILED = date(2026, 5, 8)
_LISTED = {
    _NFT_CIK: [["40", "1", "2026-05-12"]]
}  # first session four days after the NFT 6-K was filed


def _reads(ratio=("40", "1"), when=None, kind="trading", tolerance=None):
    """A reader module that reads ``ratio`` (dated ``when``) out of any filing, as ``_MORE_CONFIDENT`` does."""
    dated = f", effective_date=date.fromisoformat({when!r}), date_kind={kind!r}" if when else ""
    const = f"KNOWN_DATE_TOLERANCE_DAYS = {tolerance}\n\n" if tolerance is not None else ""
    return f"""
from datetime import date
from decimal import Decimal

from ingest.edgar.split_filing import (
    FilingDocument,
    KnownSplit,
    SplitExtraction,
    submission_documents,
)

{const}
def extract_split(documents, *, filed=None, wide=False, known=()):
    return SplitExtraction(
        ratio_from=Decimal({ratio[0]!r}), ratio_to=Decimal({ratio[1]!r}){dated}
    )


extract_split_screened = extract_split
"""


def _gate(
    replay,
    tmp_path,
    new,
    *flags,
    known=None,
    old=None,
    accessions=(_NFT,),
    strip_cik=False,
    index=None,
    guard=(),
    where="run",
):
    """One replay of the fixture filings, OLD (the real reader, or the module text ``old``) against NEW (module
    text); returns ``(exit code, changes, summary)``. ``strip_cik`` takes the CIK out of the SGML headers.
    """
    root = tmp_path / where
    root.mkdir()
    forms = _forms(root, *accessions)
    if strip_cik:
        for path in forms.rglob("*.txt"):
            path.write_bytes(
                re.sub(rb"[ \t]*CENTRAL INDEX KEY:[^\r\n]*\r?\n", b"", path.read_bytes())
            )
    (root / "new.py").write_text(new, encoding="utf-8")
    reader = _READER
    if old is not None:
        reader = root / "old.py"
        reader.write_text(old, encoding="utf-8")
    out = root / "out"
    argv = ["--old", str(reader), "--new", str(root / "new.py"), "--forms", str(forms)]
    if known is not None:
        (root / "known.json").write_text(json.dumps(known), encoding="utf-8")
        argv += ["--known", str(root / "known.json")]
    if index is not None:
        (root / "index.csv").write_text(index, encoding="utf-8")
        argv += ["--index", str(root / "index.csv")]
    if guard:
        (root / "guard.txt").write_text("\n".join(guard) + "\n", encoding="utf-8")
        argv += ["--guard", str(root / "guard.txt")]
    code = replay.main([*argv, *flags, "--out", str(out)])
    lines = (out / "changes.jsonl").read_text(encoding="utf-8").splitlines()
    return code, [json.loads(c) for c in lines], (out / "summary.txt").read_text(encoding="utf-8")


def test_accept_listed_passes_an_abstain_to_a_listed_ratio(replay, tmp_path):
    """The NFT 6-K abstains on the old reader and reads 40 -> 1 on the new, undated; prod lists 40 -> 1 four days
    after the filing. Without the flag the same run fails, as it always has."""
    code, changes, summary = _gate(
        replay, tmp_path, _reads(), "--accept-listed", known=_LISTED, where="on"
    )
    assert code == 0
    (change,) = changes
    assert (change["class"], change["guard"]) == ("abstain->ratio", False)
    assert change["listed"] == {
        "from": "40",
        "to": "1",
        "first_session": "2026-05-12",
        "days": 4,
        "dated": False,
    }
    assert (
        "abstain->ratio: 1" in summary and "of which 1 equal a listed split (accepted)" in summary
    )
    assert "ratio-bearing date changes (each needs a verdict): 0" in summary
    assert summary.splitlines()[-1].startswith("PASS")

    code, changes, summary = _gate(replay, tmp_path, _reads(), known=_LISTED, where="off")
    assert code == 1
    assert "listed" not in changes[0]
    assert "listed split" not in summary and summary.splitlines()[-1].startswith("FAIL")


def test_a_reading_that_is_not_listed_fails_either_way(replay, tmp_path):
    """KWESST's shape: the new reader reads 21 -> 1 where the old abstained, and prod lists 9 -> 1: no human has
    said it is real, so the gate keeps failing it."""
    reads = _reads(("21", "1"), when="2026-05-10")
    listed = {_NFT_CIK: [["9", "1", "2026-05-10"]]}
    for flags, where in (((), "off"), (("--accept-listed",), "on")):
        code, changes, summary = _gate(replay, tmp_path, reads, *flags, known=listed, where=where)
        assert code == 1
        assert summary.splitlines()[-1].startswith("FAIL")
    assert changes[0]["listed"] is None
    assert "NOT LISTED" in summary and "21->1 is not a listed split" in summary
    assert "of which 0 equal a listed split (accepted)" in summary


@pytest.mark.parametrize(
    ("when", "days", "ok"),
    [
        ("2026-05-12", 0, True),
        ("2026-05-22", 10, True),
        ("2026-05-02", 10, True),
        ("2026-05-24", 12, False),
        ("2026-04-30", 12, False),
    ],
)
def test_a_dated_reading_must_be_within_ten_days_of_the_listed_first_session(
    replay, tmp_path, when, days, ok
):
    code, changes, summary = _gate(
        replay, tmp_path, _reads(when=when), "--accept-listed", known=_LISTED
    )
    assert code == (0 if ok else 1)
    if ok:
        assert changes[0]["listed"]["days"] == days and changes[0]["listed"]["dated"] is True
    else:
        assert changes[0]["listed"] is None
        assert f"{days} days from the listed 40->1 first session 2026-05-12" in summary


def test_the_date_window_is_a_setting_that_defaults_to_the_readers_tolerance(replay, tmp_path):
    wide = ("--accept-listed", "--listed-date-days", "12")
    assert (
        _gate(replay, tmp_path, _reads(when="2026-05-24"), *wide, known=_LISTED, where="a")[0] == 0
    )
    # four days off: the new reader's own KNOWN_DATE_TOLERANCE_DAYS (3 here) is the default window
    four_off = _reads(when="2026-05-16", tolerance=3)
    assert _gate(replay, tmp_path, four_off, "--accept-listed", known=_LISTED, where="b")[0] == 1
    flags = ("--accept-listed", "--listed-date-days", "4")
    assert _gate(replay, tmp_path, four_off, *flags, known=_LISTED, where="c")[0] == 0


@pytest.mark.parametrize(
    ("offset", "ok"),
    [(9, True), (120, True), (-120, True), (121, False), (130, False), (-130, False)],
)
def test_an_undated_reading_must_be_filed_within_120_days_of_the_listed_first_session(
    replay, tmp_path, offset, ok
):
    listed = {_NFT_CIK: [["40", "1", (_FILED + timedelta(days=offset)).isoformat()]]}
    code, changes, summary = _gate(replay, tmp_path, _reads(), "--accept-listed", known=listed)
    assert code == (0 if ok else 1)
    assert (changes[0]["listed"] is not None) is ok
    if not ok:
        assert f"undated, filed {abs(offset)} days from the listed 40->1 first session" in summary


def test_the_undated_window_is_a_setting(replay, tmp_path):
    listed = {_NFT_CIK: [["40", "1", (_FILED + timedelta(days=130)).isoformat()]]}
    flags = ("--accept-listed", "--listed-undated-days", "130")
    assert _gate(replay, tmp_path, _reads(), *flags, known=listed)[0] == 0


@pytest.mark.parametrize(
    ("ratio", "listed", "ok"),
    [
        (("40", "1"), ["40", "1"], True),
        (("80", "2"), ["40", "1"], True),  # the same fraction
        (("1.5", "1"), ["3", "2"], True),
        (("40", "1"), ["30", "1"], False),  # another ratio
        (("40", "1"), ["1", "40"], False),  # the other orientation
    ],
)
def test_the_ratio_must_equal_a_listed_one_as_an_exact_fraction(
    replay, tmp_path, ratio, listed, ok
):
    known = {_NFT_CIK: [[*listed, "2026-05-12"]]}
    code, changes, _ = _gate(replay, tmp_path, _reads(ratio), "--accept-listed", known=known)
    assert code == (0 if ok else 1)
    assert (changes[0]["listed"] is not None) is ok


def test_no_cik_means_not_listed_unless_the_index_has_one(replay, tmp_path):
    code, changes, summary = _gate(
        replay, tmp_path, _reads(), "--accept-listed", known=_LISTED, strip_cik=True, where="a"
    )
    assert code == 1 and changes[0]["listed"] is None
    assert "NOT LISTED" in summary and "no CIK" in summary
    # --index column 5 supplies it (the header says nothing)
    index = f"accession,form,filed,items,cik\n{_NFT},6-K,2026-05-08,,{_NFT_CIK}\n"
    code, changes, _ = _gate(
        replay,
        tmp_path,
        _reads(),
        "--accept-listed",
        known=_LISTED,
        strip_cik=True,
        index=index,
        where="b",
    )
    assert code == 0 and changes[0]["listed"]["from"] == "40"


def test_a_cik_prod_lists_no_split_for_is_not_listed(replay, tmp_path):
    code, changes, summary = _gate(
        replay, tmp_path, _reads(), "--accept-listed", known={"999": [["40", "1", "2026-05-12"]]}
    )
    assert code == 1 and changes[0]["listed"] is None
    assert f"no listed splits for CIK {_NFT_CIK}" in summary


def test_a_guard_hit_equal_to_a_listed_split_still_fails_and_is_labelled(replay, tmp_path):
    """Almonty: its filing is behind a confirmed split, so its changed reading stays a hit a human reads, even
    when the new reading equals what the list holds."""
    flags = ("--accept-listed",)
    code, changes, summary = _gate(replay, tmp_path, _reads(), *flags, known=_LISTED, guard=[_NFT])
    assert code == 1
    (change,) = changes
    assert change["guard"] is True and change["listed"]["first_session"] == "2026-05-12"
    assert (
        f"HIT {_NFT}: abstain->ratio (= the listed split 40->1, first session 2026-05-12)"
        in summary
    )
    assert "of which 0 equal a listed split (accepted)" in summary
    assert summary.splitlines()[-1].startswith("FAIL")


def test_only_an_abstain_to_a_ratio_is_ever_accepted(replay, tmp_path):
    """ARTL's 1-for-9 read as 7 -> 1 is a ratio changed, and stays a failure although prod lists a 7 -> 1 for the
    CIK; the NFT 6-K beside it is accepted."""
    listed = {_NFT_CIK: [["7", "1", "2026-05-12"]], "1621221": [["7", "1", "2026-08-31"]]}
    code, changes, summary = _gate(
        replay,
        tmp_path,
        _reads(("7", "1")),
        "--accept-listed",
        known=listed,
        accessions=(_ARTL, _NFT),
    )
    by_key = {c["key"]: c for c in changes}
    assert by_key[_ARTL]["class"] == "ratio changed" and by_key[_ARTL]["listed"] is None
    assert by_key[_NFT]["class"] == "abstain->ratio" and by_key[_NFT]["listed"] is not None
    assert code == 1
    assert "FAIL: abstain->ratio 0 (+1 listed, accepted), ratio changed 1" in summary


def test_a_ratio_bearing_date_change_is_counted_for_a_verdict_and_does_not_fail(replay, tmp_path):
    code, changes, summary = _gate(
        replay,
        tmp_path,
        _reads(when="2026-05-13"),
        "--accept-listed",
        known=_LISTED,
        old=_reads(when="2026-05-12"),
    )
    assert code == 0
    assert [c["class"] for c in changes] == ["date changed"] and changes[0]["listed"] is None
    assert "ratio-bearing date changes (each needs a verdict): 1" in summary


def test_accept_listed_needs_known(replay, tmp_path, capsys):
    argv = ["--old", str(_READER), "--new", str(_READER), "--forms", str(tmp_path)]
    assert replay.main([*argv, "--out", str(tmp_path / "out"), "--accept-listed"]) == 2
    assert "--accept-listed needs --known" in capsys.readouterr().err


def test_the_nearest_of_two_listed_splits_of_one_ratio_is_the_match(replay):
    """Two 1-for-20s (Beyond Air had two): the reading matches the one it sits next to."""
    known = {
        "7": [
            (Decimal(20), Decimal(1), date(2025, 7, 14), None),
            (Decimal(20), Decimal(1), date(2026, 3, 2), "0001-26-000001"),
        ]
    }
    dated = _reading(ratio=("20", "1"), when="2026-03-04")
    got, why = replay.match_listed("7", date(2026, 3, 9), dated, known)
    assert (got["first_session"], got["days"], got["dated"], why) == ("2026-03-02", 2, True, "")
    undated = _reading(ratio=("20", "1"), when=None, kind=None)
    got, _ = replay.match_listed("7", date(2025, 7, 1), undated, known)
    assert (got["first_session"], got["days"], got["dated"]) == ("2025-07-14", 13, False)
    assert replay.match_listed("7", None, undated, known)[0] is None  # no filing date, no window

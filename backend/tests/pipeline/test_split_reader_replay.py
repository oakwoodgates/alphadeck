"""The split reader's old-vs-new replay (``scripts/split_reader_replay.py``, split-reader misreads PR-A): it reads
each filing exactly as the pipeline does (``pipeline.backfill_splits.screen_of`` / ``extract_for``), finds nothing
when both readers are one, and fails on a reading that became MORE confident or touches a guarded filing. Pure: real
fixture filings (``tests/fixtures/splits``), no database, no network."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from datetime import date
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

"""Replay the SEC split reader, OLD against NEW, over cached filings: every changed reading, classed.

The proof a split-reader change owes (the split-reader misreads plan of 2026-11-26, "Proof: no real split
lost"): two versions of ``ingest/edgar/split_filing.py`` read the same filings the way the pipeline reads
them, and every reading that differs is written out with its class. Pure: files in, files out — no database, no
network, no clock, no settings.

    cd /app && PYTHONPATH=/app python scripts/split_reader_replay.py \\
        --old /tmp/old_split_filing.py --new /app/ingest/edgar/split_filing.py \\
        --forms /data/edgar_cache/forms [--forms DIR ...] [--index FILE.csv] [--known FILE.json] \\
        [--guard FILE.txt] [--old-calloff /tmp/old_split_calloff.py] --out /tmp/replay

**Inputs.** ``--old`` / ``--new``: the two reader modules, each loaded by ``importlib`` under its own name and
registered in ``sys.modules`` (its dataclasses need it). ``--forms DIR`` (repeatable): every ``*.txt``,
``*.htm``, ``*.html`` and ``*.bin`` file under ``DIR`` (gzipped too), e.g. ``DIR/<acc>/<acc>.txt``. A file with
``<DOCUMENT>`` blocks is a full submission; any other is ONE document. Form, filing date and 8-K items come from
the SGML header (CONFORMED SUBMISSION TYPE, FILED AS OF DATE, ITEM INFORMATION mapped to item codes) or from
``--index`` (CSV ``accession,form,filed,items[,cik]``, items as ``5.03;9.01``; the index wins), keyed by the
accession (the header's, else the parent folder's or the file's name when it is one) or else the file's name
without its extensions. ``--known`` (``{cik: [[from, to, first_session(, accession)], ...]}``): the confirmed splits
prod's list holds, passed to each filing of that CIK (the header's filer, or the index's) as
``pipeline.backfill_splits.known_for`` does (in effect by the filing date, never the split whose own evidence the
filing is). ``--guard`` (one accession per line): the filings behind confirmed splits. ``--old-calloff``: an old
``ingest/edgar/split_calloff.py``, loaded under its own name and set as the OLD reader's ``split_calloff`` (the
call-off reader it calls through that module attribute), so a wording-only change replays with one reader file on
both sides; a no-op, said so in the summary, when the old reader has no ``split_calloff``.

**Reading** (``pipeline.backfill_splits.screen_of`` / ``extract_for``): a catch-all 8-K (items 7.01 / 8.01, no
3.03 / 5.03) with its filing date; a 6-K, a charter 8-K or an 8-K with no items through the restatement screen; a
wide form through it with ``wide=True`` (a lone document typed as its form, as the targeted reader reads a
periodic report's primary document); a Reg A form bare. An 8-K whose resolved items hold no split item, and any
form the pipeline does not read, are skipped (counted). A file with no form or filing date is read bare and
counted as unindexed. Text is read as the EDGAR client reads its cache (UTF-8, universal newlines); a file that
is not UTF-8 is skipped (the client cannot read it either).

**Output** (``--out``): ``changes.jsonl``, one line per changed reading (the filing, its screen, both readings,
the fields that differ, ``class`` and ``guard``), and ``summary.txt`` (counts per class and per screen, skips,
errors). ``--all-readings`` also writes ``readings.jsonl`` (every filing, both readings). The class is the
reading's most material change: ``abstain->ratio``, ``ratio changed``, ``ratio->abstain``, ``date added``,
``date changed``, ``date dropped``, ``kind changed``, ``reason only`` (abstain reason or date note),
``call-off only`` (the reading's ``calloff``), ``text only`` (passage, document, mentions), ``error`` (one side
raised). A reading carries ``calloff`` (as JSON) only when its reader sets one, so a reader without call-offs writes
what it always did; the fields are compared over both sides' keys, and a summary line counts the readings whose
call-off moved, whatever their class. A GUARD HIT is a guard accession whose ratio, date or date kind changed (or
one side raised): ``call-off only`` is report only, never a failure or a hit.

**Listed readings** (``--accept-listed``; off by default = the gate above; needs ``--known``). An ``abstain->ratio``
then passes when the new ratio equals a split ``--known`` lists for the filing's CIK (the header's, else
``--index`` column 5): the same exact fraction, old -> new in the same orientation, and the reading near that
split's first session. A dated reading must sit within ``--listed-date-days`` of it (default: the new reader's
``KNOWN_DATE_TOLERANCE_DAYS``, else 10); an undated one must have been filed within ``--listed-undated-days``
(default 120) before or after it. Every listed row of the CIK counts, not only those in effect at the filing's
date. ``changes.jsonl`` then carries ``listed`` on every line (``{from, to, first_session, days, dated}``, or
``null``); the summary counts the accepted ones, names each abstain->ratio that is not listed and why ("no CIK",
...), and counts the ratio-bearing date changes (report only: each needs a human verdict). Nothing else is
accepted: a ``ratio changed``, an ``error`` and a guard hit still fail, and a guard hit that equals a listed split
is only labelled so, for a human to read.

**Exit**: 1 on any ``abstain->ratio`` (with ``--accept-listed``: one that is not a listed split, or sits on a
guard accession), ``ratio changed``, ``error`` (one side raised where the other did not) or guard hit; 2 on bad
arguments; else 0.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import gzip
import importlib.util
import json
import re
import sys
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any

from pipeline.backfill_splits import (
    SCREEN_CATCH_ALL,
    SCREEN_NONE,
    SCREEN_RESTATED,
    SCREEN_WIDE,
    SEC_FORMS,
    SPLIT_8K_ITEMS,
    WIDE_FORMS,
    FilingRef,
    screen_of,
)

_SUFFIXES = (".txt", ".htm", ".html", ".bin")
_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_8K_FORMS = frozenset({"8-K", "8-K/A"})

# the 8-K item names the SGML header's ITEM INFORMATION lines carry -> their codes (only 3.03 / 5.03 / 7.01 / 8.01
# decide how a filing is read; the rest say its items are resolved)
_ITEM_NAMES = (
    ("entry into a material definitive agreement", "1.01"),
    ("termination of a material definitive agreement", "1.02"),
    ("bankruptcy or receivership", "1.03"),
    ("mine safety", "1.04"),
    ("material cybersecurity incident", "1.05"),
    ("completion of acquisition or disposition", "2.01"),
    ("results of operations and financial condition", "2.02"),
    ("creation of a direct financial obligation", "2.03"),
    ("triggering events that accelerate", "2.04"),
    ("costs associated with exit or disposal", "2.05"),
    ("material impairments", "2.06"),
    ("notice of delisting", "3.01"),
    ("unregistered sales of equity securities", "3.02"),
    ("material modification", "3.03"),
    ("changes in registrant's certifying accountant", "4.01"),
    ("non-reliance on previously issued", "4.02"),
    ("changes in control of registrant", "5.01"),
    ("departure of directors", "5.02"),
    ("amendments to articles of incorporation", "5.03"),
    ("temporary suspension of trading", "5.04"),
    ("amendments to the registrant's code of ethics", "5.05"),
    ("change in shell company status", "5.06"),
    ("submission of matters to a vote", "5.07"),
    ("shareholder director nominations", "5.08"),
    ("regulation fd disclosure", "7.01"),
    ("other events", "8.01"),
    ("financial statements and exhibits", "9.01"),
)

FAIL_CLASSES = frozenset({"abstain->ratio", "ratio changed", "error"})
# the classes a guard accession may show without being a hit (no ratio, date or kind moved)
_TEXT_CLASSES = frozenset({"reason only", "call-off only", "text only"})
# the changes that keep a ratio and move its date: each needs a human verdict (``--accept-listed``'s report line)
_DATE_CLASSES = frozenset({"date added", "date changed", "date dropped", "kind changed"})
# ``--accept-listed``'s windows: days from a listed split's first session (the new reader's own tolerance, when it
# has one, is the dated default)
DATED_DAYS = 10
UNDATED_DAYS = 120
CLASS_ORDER = (
    "abstain->ratio",
    "ratio changed",
    "ratio->abstain",
    "date added",
    "date changed",
    "date dropped",
    "kind changed",
    "reason only",
    "call-off only",
    "text only",
    "error",
)


@dataclass
class Item:
    """One file to read: where it is, and the filing it is (as far as the header / index say)."""

    path: Path
    key: str
    accession: str | None
    form: str | None
    filed: date | None
    items: tuple[str, ...] | None
    cik: str | None
    submission: bool


# --- loading ----------------------------------------------------------------------------------------------------


def load_reader(path: Path, name: str) -> ModuleType:
    """Load one version of the reader from its file, registered under ``name`` before it runs."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def swap_calloff(reader: ModuleType, path: Path) -> str:
    """``--old-calloff``: set ``reader``'s ``split_calloff`` (the call-off reader it calls through that module
    attribute) to the module in ``path``; the other reader keeps the one it imported. A reader with no
    ``split_calloff`` is left as it is. Returns the summary's line."""
    if not hasattr(reader, "split_calloff"):
        return f"--old-calloff: the old reader has no split_calloff; nothing swapped ({path})"
    reader.split_calloff = load_reader(path, "split_calloff_old")
    return f"--old-calloff: the old reader reads call-offs through {path}"


def read_text(path: Path) -> str:
    """The file as the EDGAR client reads its cache: UTF-8 (a ``UnicodeDecodeError`` propagates), universal
    newlines; gunzipped first when it ends in ``.gz``."""
    data = path.read_bytes()
    if path.name.endswith(".gz"):
        data = gzip.decompress(data)
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _base_name(path: Path) -> str:
    name = path.name[: -len(".gz")] if path.name.endswith(".gz") else path.name
    return name.rsplit(".", 1)[0]


def item_codes(names: list[str]) -> tuple[str, ...] | None:
    """The 8-K item codes for the header's ITEM INFORMATION names (``None`` with none: not resolved)."""
    out = []
    for n in names:
        low = n.strip().lower()
        code = next((c for k, c in _ITEM_NAMES if low.startswith(k) or k in low), None)
        out.append(code or f"?{n.strip()}")
    return tuple(out) or None


def parse_header(text: str) -> dict[str, Any]:
    """Form, filing date, items, accession and filer CIK from a full submission's SGML header."""
    head = text[: text.find("<DOCUMENT>")] if "<DOCUMENT>" in text else text[:20000]
    out: dict[str, Any] = {}
    m = re.search(r"^ACCESSION NUMBER:\s*(\S+)", head, re.M)
    if m:
        out["accession"] = m.group(1)
    m = re.search(r"^CONFORMED SUBMISSION TYPE:\s*(.+?)\s*$", head, re.M) or re.search(
        r"^\s*FORM TYPE:\s*(.+?)\s*$", head, re.M
    )
    if m:
        out["form"] = m.group(1)
    m = re.search(r"^FILED AS OF DATE:\s*(\d{4})(\d\d)(\d\d)", head, re.M)
    if m:
        out["filed"] = date(int(m[1]), int(m[2]), int(m[3]))
    names = re.findall(r"^ITEM INFORMATION:\s*(.+?)\s*$", head, re.M)
    if names:
        out["items"] = item_codes(names)
    m = re.search(r"^\s*CENTRAL INDEX KEY:\s*(\d+)", head, re.M)
    if m:
        out["cik"] = str(int(m.group(1)))
    return out


def load_index(path: Path | None) -> dict[str, dict[str, Any]]:
    """``--index``: ``accession,form,filed,items[,cik]`` (a header row is skipped; blank fields are unknown;
    columns after the cik are ignored)."""
    if path is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if not row or row[0].strip().lower() in ("accession", ""):
                continue
            acc = row[0].strip()
            rec: dict[str, Any] = {}
            if len(row) > 1 and row[1].strip():
                rec["form"] = row[1].strip()
            if len(row) > 2 and row[2].strip():
                rec["filed"] = date.fromisoformat(row[2].strip())
            if len(row) > 3 and row[3].strip():
                rec["items"] = tuple(x for x in re.split(r"[;,\s]+", row[3].strip()) if x)
            if len(row) > 4 and row[4].strip().isdigit():
                rec["cik"] = str(int(row[4].strip()))
            out[acc] = rec
    return out


def load_known(path: Path | None) -> dict[str, list[tuple[Decimal, Decimal, date, str | None]]]:
    """``--known``: ``{cik: [[from, to, first_session(, accession)], ...]}`` (prod's confirmed splits)."""
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(int(cik)): [
            (
                Decimal(str(r[0])),
                Decimal(str(r[1])),
                date.fromisoformat(r[2]),
                r[3] if len(r) > 3 else None,
            )
            for r in rows
        ]
        for cik, rows in raw.items()
    }


def load_guard(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def discover(dirs: list[Path], index: dict[str, dict[str, Any]]) -> list[Item]:
    """Every readable file under the ``--forms`` folders, with what its header and the index say."""
    items: list[Item] = []
    for root in dirs:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            name = p.name[: -len(".gz")] if p.name.endswith(".gz") else p.name
            if not name.lower().endswith(_SUFFIXES):
                continue
            items.append(_item(p, index))
    return items


def _head(path: Path, size: int = 1 << 18) -> str:
    """The file's first ``size`` bytes, decoded leniently: enough for a submission's SGML header."""
    with gzip.open(path, "rb") if path.name.endswith(".gz") else path.open("rb") as fh:
        return fh.read(size).decode("utf-8", "ignore")


def _item(p: Path, index: dict[str, dict[str, Any]]) -> Item:
    head: dict[str, Any] = {}
    submission = False
    try:
        text = _head(p)
        submission = "<DOCUMENT>" in text
        if submission or "<SEC-HEADER>" in text[:2000]:
            head = parse_header(text)
    except (OSError, EOFError, gzip.BadGzipFile):
        pass  # reported when it is read
    base = _base_name(p)
    acc = head.get("accession")
    if acc is None:
        acc = next((c for c in (base, p.parent.name) if _ACCESSION.match(c)), None)
    # the index by accession, then by the file's own name (more specific: one document of a filing)
    rec = {**head, **index.get(acc or "", {}), **(index.get(base, {}) if base != acc else {})}
    if submission or base == acc:
        key = acc or base
    else:
        key = f"{acc}/{p.name}" if acc else base
    return Item(
        path=p,
        key=key,
        accession=acc,
        form=rec.get("form"),
        filed=rec.get("filed"),
        items=rec.get("items"),
        cik=rec.get("cik"),
        submission=submission,
    )


# --- reading ----------------------------------------------------------------------------------------------------


def screen_for(item: Item) -> tuple[str | None, str | None]:
    """``(screen, None)`` as ``screen_of`` reads the filing, or ``(None, why)`` when the pipeline never reads it.
    A file with no form or filing date is read bare (``SCREEN_NONE``)."""
    if item.form is None or item.filed is None:
        return SCREEN_NONE, None
    form = item.form.upper()
    if form not in SEC_FORMS and form not in WIDE_FORMS:
        return None, f"form {form} is not read"
    if form in _8K_FORMS and item.items is not None and not (SPLIT_8K_ITEMS & set(item.items)):
        return None, "8-K without a split item"
    ref = FilingRef(
        accession=item.accession or item.key, form=form, filed=item.filed, items=item.items
    )
    return screen_of(ref), None


def known_for(item: Item, mod: ModuleType, known: dict[str, list[tuple]]) -> tuple:
    """The confirmed splits a filing is read knowing (``pipeline.backfill_splits.known_for``): in effect by its
    filing date, never the split this filing is the evidence of; sorted."""
    if item.cik is None or item.filed is None:
        return ()
    out = {
        (a, b, d)
        for a, b, d, acc in known.get(item.cik, [])
        if d <= item.filed and (acc is None or acc != item.accession)
    }
    return tuple(
        mod.KnownSplit(a, b, d) for a, b, d in sorted(out, key=lambda k: (k[2], k[0], k[1]))
    )


def documents(item: Item, mod: ModuleType, text: str) -> list:
    if item.submission:
        return mod.submission_documents(text)
    kind = item.form.upper() if item.form and item.form.upper() in WIDE_FORMS else "HTML"
    return [mod.FilingDocument(type=kind, filename=item.path.name, raw=text)]


def extract(mod: ModuleType, docs: list, item: Item, screen: str, known: tuple) -> Any:
    """One reading, dispatched as ``pipeline.backfill_splits.extract_for``."""
    if screen == SCREEN_CATCH_ALL:
        return mod.extract_split(docs, filed=item.filed, known=known)
    if screen in (SCREEN_RESTATED, SCREEN_WIDE):
        return mod.extract_split_screened(
            docs, filed=item.filed, wide=screen == SCREEN_WIDE, known=known
        )
    return mod.extract_split(docs, known=known)


def as_dict(x: Any) -> dict[str, Any]:
    out = {
        "ratio_from": None if x.ratio_from is None else str(x.ratio_from),
        "ratio_to": None if x.ratio_to is None else str(x.ratio_to),
        "effective_date": x.effective_date.isoformat() if x.effective_date else None,
        "date_kind": x.date_kind,
        "abstain_reason": x.abstain_reason,
        "date_note": x.date_note,
        "document": x.document,
        "passage": x.passage,
        "mentions": list(x.mentions),
    }
    calloff = getattr(x, "calloff", None)  # only a reader that reads call-offs sets one
    if calloff is not None:
        out["calloff"] = _jsonable(calloff)
    return out


def _jsonable(v: Any) -> Any:
    """``v`` (a dataclass, mapping or sequence of them) as JSON values: dates in ISO form, decimals and any
    other scalar as their text."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _jsonable(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, int, float)):
        return v
    return v.isoformat() if isinstance(v, date) else str(v)


def _ratio(r: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    if r.get("ratio_from") is None or r.get("ratio_to") is None:
        return None
    return Decimal(r["ratio_from"]), Decimal(r["ratio_to"])


def _fraction(a: Decimal, b: Decimal) -> Fraction | None:
    """``a / b`` as an exact fraction (``None`` when either is not a positive number)."""
    if not (a.is_finite() and b.is_finite()) or a <= 0 or b <= 0:
        return None
    return Fraction(a) / Fraction(b)


def _plain(d: Decimal) -> str:
    return f"{d.normalize():f}"


def match_listed(
    cik: str | None,
    filed: date | None,
    reading: dict[str, Any],
    known: dict[str, list[tuple]],
    *,
    dated_days: int = DATED_DAYS,
    undated_days: int = UNDATED_DAYS,
) -> tuple[dict[str, Any] | None, str]:
    """``(listed, "")`` when ``reading``'s ratio equals a split ``known`` lists for ``cik`` (the same exact fraction,
    old -> new) and sits near that split's first session; else ``(None, why)``. A dated reading is judged by its date
    (within ``dated_days``); an undated one by when it was filed (within ``undated_days``). Every row of the CIK
    counts; of several rows with the ratio, the nearest one is the match. ``listed`` is ``{from, to, first_session,
    days, dated}`` (``days`` absolute)."""
    ratio = _ratio(reading)
    want = _fraction(*ratio) if ratio else None
    if ratio is None or want is None:
        return None, "no ratio"
    if cik is None:
        return None, "no CIK (no SGML header, no --index cik)"
    rows = known.get(cik) or []
    if not rows:
        return None, f"no listed splits for CIK {cik}"
    same = [r for r in rows if _fraction(r[0], r[1]) == want]
    if not same:
        listed = ", ".join(
            f"{_plain(r[0])}->{_plain(r[1])} {r[2]}" for r in sorted(rows, key=lambda r: r[2])
        )
        return (
            None,
            f"{_plain(ratio[0])}->{_plain(ratio[1])} is not a listed split of CIK {cik} (listed: {listed})",
        )
    when = reading.get("effective_date")
    anchor = date.fromisoformat(when) if when else filed
    if anchor is None:
        return None, "undated reading and no filing date to place it by"
    days, row = min(((abs((anchor - r[2]).days), r) for r in same), key=lambda t: (t[0], t[1][2]))
    limit = dated_days if when else undated_days
    if days > limit:
        how = "dated" if when else "undated, filed"
        return None, (
            f"{how} {days} days from the listed {_plain(row[0])}->{_plain(row[1])} first session "
            f"{row[2]} (limit {limit})"
        )
    return {
        "from": _plain(row[0]),
        "to": _plain(row[1]),
        "first_session": row[2].isoformat(),
        "days": days,
        "dated": bool(when),
    }, ""


def classify(old: dict[str, Any], new: dict[str, Any]) -> tuple[str | None, list[str]]:
    """``(class, differing fields)`` of one filing's two readings (``None`` when they are the same)."""
    if "error" in old or "error" in new:
        same = old.get("error") == new.get("error")
        return (None, []) if same else ("error", ["error"])
    fields = [k for k in {**old, **new} if old.get(k) != new.get(k)]
    if not fields:
        return None, []
    ro, rn = _ratio(old), _ratio(new)
    if ro is not None and rn is None:
        return "ratio->abstain", fields
    if ro is None and rn is not None:
        return "abstain->ratio", fields
    if ro is not None and rn is not None and ro != rn:
        return "ratio changed", fields
    do, dn = old["effective_date"], new["effective_date"]
    if do is None and dn is not None:
        return "date added", fields
    if do is not None and dn is None:
        return "date dropped", fields
    if do != dn:
        return "date changed", fields
    if old["date_kind"] != new["date_kind"]:
        return "kind changed", fields
    if {"abstain_reason", "date_note"} & set(fields):
        return "reason only", fields
    if "calloff" in fields:
        return "call-off only", fields
    return "text only", fields


def _read(mod: ModuleType, item: Item, text: str, screen: str, known: tuple) -> dict[str, Any]:
    try:
        return as_dict(extract(mod, documents(item, mod, text), item, screen, known))
    except Exception as e:  # one filing's failure is reported, never the run's
        return {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)}


def replay(
    old: ModuleType,
    new: ModuleType,
    items: list[Item],
    *,
    known: dict[str, list[tuple]],
    guard: set[str],
    out: Path,
    all_readings: bool = False,
    progress: Any = None,
    accept_listed: bool = False,
    dated_days: int = DATED_DAYS,
    undated_days: int = UNDATED_DAYS,
    old_calloff: str | None = None,
) -> dict[str, Any]:
    """Read every item with both readers; write ``changes.jsonl`` (and ``readings.jsonl``); return the counts.
    ``accept_listed``: an abstain->ratio equal to a listed split (``match_listed``) does not fail the run.
    ``old_calloff``: ``swap_calloff``'s line, for the summary.
    """
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, Counter] = {
        "class": Counter(),
        "class_screen": Counter(),
        "screen": Counter(),
        "skipped": Counter(),
    }
    errors: list[str] = []
    guard_hits: list[str] = []
    guard_text: list[str] = []
    accepted: list[str] = []
    unlisted: list[str] = []
    listed_guard = 0
    ratio_date_changes = 0
    calloff_moved = 0
    unindexed = 0
    read = 0
    changes_path = out / "changes.jsonl"
    readings_fh = (out / "readings.jsonl").open("w", encoding="utf-8") if all_readings else None
    with changes_path.open("w", encoding="utf-8") as fh:
        for n, item in enumerate(items, 1):
            if progress is not None and n % 25 == 0:
                print(f"  {n}/{len(items)}", file=progress, flush=True)
            screen, why = screen_for(item)
            if screen is None:
                counts["skipped"][why] += 1
                continue
            try:
                text = read_text(item.path)
            except (OSError, UnicodeDecodeError, EOFError, gzip.BadGzipFile) as e:
                counts["skipped"][f"unreadable ({type(e).__name__})"] += 1
                continue
            if text.lstrip()[:1] in ("{", "["):
                counts["skipped"]["not a document (JSON)"] += 1
                continue
            if item.form is None or item.filed is None:
                unindexed += 1
            read += 1
            counts["screen"][screen] += 1
            ko, kn = known_for(item, old, known), known_for(item, new, known)
            ro = _read(old, item, text, screen, ko)
            rn = _read(new, item, text, screen, kn)
            for side, r in (("old", ro), ("new", rn)):
                if "error" in r:
                    errors.append(f"{item.key} ({side}): {r['error']}")
            cls, fields = classify(ro, rn)
            head = {
                "key": item.key,
                "path": str(item.path),
                "accession": item.accession,
                "form": item.form,
                "filed": item.filed.isoformat() if item.filed else None,
                "items": list(item.items) if item.items else None,
                "screen": screen,
                "known": len(kn),
            }
            if readings_fh is not None:
                readings_fh.write(json.dumps({**head, "class": cls, "old": ro, "new": rn}) + "\n")
            if cls is None:
                continue
            is_guard = item.accession in guard
            hit = is_guard and cls not in _TEXT_CLASSES
            extra: dict[str, Any] = {}
            label = ""
            if accept_listed:
                listed = None
                if cls == "abstain->ratio":
                    listed, why = match_listed(
                        item.cik,
                        item.filed,
                        rn,
                        known,
                        dated_days=dated_days,
                        undated_days=undated_days,
                    )
                    if listed is None:
                        unlisted.append(f"{item.key}: {why}")
                    else:
                        label = f"{listed['from']}->{listed['to']}, first session {listed['first_session']}"
                        if hit:
                            listed_guard += 1
                        else:
                            how = "dated" if listed["dated"] else "undated"
                            accepted.append(
                                f"{item.key}: = the listed split {label} ({how}, {listed['days']} days)"
                            )
                elif cls in _DATE_CLASSES and _ratio(rn) is not None:
                    ratio_date_changes += 1
                extra = {"listed": listed}
            if hit:
                guard_hits.append(
                    f"{item.key}: {cls}" + (f" (= the listed split {label})" if label else "")
                )
            elif is_guard:
                guard_text.append(f"{item.key}: {cls}")
            counts["class"][cls] += 1
            counts["class_screen"][f"{cls} | {screen}"] += 1
            if "calloff" in fields:
                calloff_moved += 1
            fh.write(
                json.dumps(
                    {
                        **head,
                        "class": cls,
                        "fields": fields,
                        "guard": hit,
                        **extra,
                        "old": ro,
                        "new": rn,
                    }
                )
                + "\n"
            )
    if readings_fh is not None:
        readings_fh.close()
    failing = sum(counts["class"][c] for c in FAIL_CLASSES) - len(accepted) + len(guard_hits)
    result = {
        "found": len(items),
        "read": read,
        "unindexed": unindexed,
        "counts": counts,
        "errors": errors,
        "guard_hits": guard_hits,
        "guard_text": guard_text,
        "guard_size": len(guard),
        "failing": failing,
        "accept_listed": accept_listed,
        "windows": (dated_days, undated_days),
        "accepted": accepted,
        "unlisted": unlisted,
        "listed_guard": listed_guard,
        "ratio_date_changes": ratio_date_changes,
        "calloff_moved": calloff_moved,
        "old_calloff": old_calloff,
    }
    (out / "summary.txt").write_text(summary_text(result), encoding="utf-8")
    return result


def summary_text(r: dict[str, Any]) -> str:
    c = r["counts"]
    lines = [
        f"files found: {r['found']}; read: {r['read']} (unindexed, read bare: {r['unindexed']})",
        "read per screen: "
        + (", ".join(f"{k} {v}" for k, v in sorted(c["screen"].items())) or "none"),
        "skipped: " + (", ".join(f"{k} {v}" for k, v in sorted(c["skipped"].items())) or "none"),
    ]
    if r.get("old_calloff"):
        lines.append(r["old_calloff"])
    lines.append(f"changed readings: {sum(c['class'].values())}")
    for cls in CLASS_ORDER:
        if c["class"][cls]:
            per = ", ".join(
                f"{k.split(' | ')[1]} {v}"
                for k, v in sorted(c["class_screen"].items())
                if k.split(" | ")[0] == cls
            )
            more = ""
            if r["accept_listed"] and cls == "abstain->ratio":
                more = f", of which {len(r['accepted'])} equal a listed split (accepted)"
                if r["listed_guard"]:
                    more += (
                        f"; {r['listed_guard']} more equal one but are guard hits (not accepted)"
                    )
            lines.append(f"  {cls}: {c['class'][cls]} ({per}){more}")
    if r.get("calloff_moved"):
        lines.append(f"call-off moved (any class; report only): {r['calloff_moved']}")
    if r["accept_listed"]:
        dated_days, undated_days = r["windows"]
        lines.append(
            f"--accept-listed: an abstain->ratio equal to a listed split passes (dated: within {dated_days} days "
            f"of its first session; undated: filed within {undated_days} days of it)"
        )
        lines += [f"  accepted {a}" for a in r["accepted"]]
        lines += [f"  NOT LISTED {u}" for u in r["unlisted"]]
        lines.append(
            f"ratio-bearing date changes (each needs a verdict): {r['ratio_date_changes']}"
        )
    lines.append(f"guard accessions: {r['guard_size']}; guard hits: {len(r['guard_hits'])}")
    lines += [f"  HIT {h}" for h in r["guard_hits"]]
    lines += [f"  text only on a guard accession: {h}" for h in r["guard_text"]]
    lines.append(f"errors: {len(r['errors'])}")
    lines += [f"  {e}" for e in r["errors"][:50]]
    verdict = "FAIL" if r["failing"] else "PASS"
    abstain = f"{c['class']['abstain->ratio']}"
    if r["accept_listed"]:
        abstain = f"{c['class']['abstain->ratio'] - len(r['accepted'])} (+{len(r['accepted'])} listed, accepted)"
    lines.append(
        f"{verdict}: abstain->ratio {abstain}, ratio changed "
        f"{c['class']['ratio changed']}, error {c['class']['error']}, guard hits {len(r['guard_hits'])}"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--old", type=Path, required=True, help="the old split_filing.py")
    p.add_argument("--new", type=Path, required=True, help="the new split_filing.py")
    p.add_argument("--forms", type=Path, action="append", required=True, help="a folder of filings")
    p.add_argument("--index", type=Path, help="CSV accession,form,filed,items")
    p.add_argument("--known", type=Path, help="JSON {cik: [[from, to, first_session], ...]}")
    p.add_argument("--guard", type=Path, help="accessions behind confirmed splits, one per line")
    p.add_argument(
        "--old-calloff",
        type=Path,
        help="an old split_calloff.py for the OLD reader (a no-op when it has no split_calloff)",
    )
    p.add_argument("--out", type=Path, required=True, help="output folder")
    p.add_argument("--all-readings", action="store_true", help="also write readings.jsonl")
    p.add_argument(
        "--accept-listed",
        action="store_true",
        help="an abstain->ratio equal to a split --known lists does not fail the run (needs --known)",
    )
    p.add_argument(
        "--listed-date-days",
        type=int,
        help="a dated reading must be within N days of the listed first session "
        "(default: the new reader's KNOWN_DATE_TOLERANCE_DAYS, else 10)",
    )
    p.add_argument(
        "--listed-undated-days",
        type=int,
        default=UNDATED_DAYS,
        help="an undated reading must be filed within N days of the listed first session (default 120)",
    )
    a = p.parse_args(argv)
    if a.accept_listed and a.known is None:
        print("--accept-listed needs --known", file=sys.stderr)
        return 2
    for f in (a.old, a.new, *a.forms, *([a.old_calloff] if a.old_calloff else [])):
        if not f.exists():
            print(f"not found: {f}", file=sys.stderr)
            return 2
    old = load_reader(a.old, "split_reader_old")
    new = load_reader(a.new, "split_reader_new")
    swapped = None if a.old_calloff is None else swap_calloff(old, a.old_calloff)
    items = discover(a.forms, load_index(a.index))
    dated_days = a.listed_date_days
    if dated_days is None:
        dated_days = getattr(new, "KNOWN_DATE_TOLERANCE_DAYS", DATED_DAYS)
    result = replay(
        old,
        new,
        items,
        known=load_known(a.known),
        guard=load_guard(a.guard),
        out=a.out,
        all_readings=a.all_readings,
        progress=sys.stderr,
        accept_listed=a.accept_listed,
        dated_days=dated_days,
        undated_days=a.listed_undated_days,
        old_calloff=swapped,
    )
    print(summary_text(result), end="")
    return 1 if result["failing"] else 0


if __name__ == "__main__":
    sys.exit(main())

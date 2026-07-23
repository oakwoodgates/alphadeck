# Retrieval phase — the ANSWER KEY (established 2026-07-23)

> **What this is.** Hand-verified ground truth for the "dark names" — basket members with **no 10-K/10-Q**,
> for which `extract_for_security` returns `[]` today. Built **before** any retrieval code, so every
> coverage claim the phase makes is falsifiable against a fixed key rather than trusted because it sounds
> right. This is the invariant #9 discipline (`recall is sacred`) applied to the FACT layer.
>
> **Method discipline — the one that matters.** Ground truth is read from the **FILING**, never from
> `companyfacts`. Checking companyfacts against companyfacts proves nothing. Every value below carries its
> accession + document URL so any line can be spot-checked without re-deriving the whole key.
>
> **Status: MEASURED.** Everything in §1–§4 was executed against live EDGAR on 2026-07-23. §5 is PROPOSED.

---

## 0. Scale of the gap (measured, against the live demo DB)

| Metric | Value |
|---|---|
| Distinct resolved basket names | **250** |
| Covered (has a 10-K or 10-Q) | 202 |
| **Dark (extract returns `[]`)** | **48 — 19%** |

Per thesis: **AI Memory & Storage 20 / 90 (22%)**, **Rainbow Rush 31 / 159 (19%)**. The remaining three
system theses are unaffected (0 dark).

What the 48 actually have available — i.e. what we are currently discarding:

| Already retrievable | Count | Examples |
|---|---|---|
| IFRS companyfacts (`ifrs-full`) | 26 | TSM, NVS, RDY, IMOS, EVO |
| US-GAAP companyfacts (`us-gaap`) | 18 | ASML, CAMT, SIMO, NVMI, ATEYY |
| companyfacts EMPTY | 3 | OPTH, PBLS, SKHY |
| no companyfacts at all | 1 | AGNPF |

**44 of 48 (92%) have populated XBRL** at an endpoint the extractor **already calls and then discards**
(`extract.py:669` fetches `cf`; `extract.py:675` returns `[]` past it when neither periodic form exists).

Separately, on the insider side: **24 of the 48 have zero Form 4 filings, ever** — foreign private issuers
are exempt from Section 16, so there is nothing to retrieve. They can still warm via the built
`theme_conviction` Key-1 fallback (`CALL_LOGIC.md:151`) and all 48 have price bars (Key-2 unaffected); what
they cannot do is supply their **own** insider key, so they never reach `core` by that path. ASML is one.

---

## 1. The key — shares outstanding, filing vs companyfacts

`dei:EntityCommonStockSharesOutstanding` is present for **every** real name below, **US-GAAP and IFRS
alike** (`dei` is taxonomy-independent). Ground truth is the 20-F cover page.

| Name | Filing value | Report date | companyfacts value | cf as-of | Verdict |
|---|---:|---|---:|---|---|
| ASML | 385,417,665 | 2025-12-31 | 385,417,665 | 2025-12-31 | MATCH |
| CAMT | 45,828,133 | 2025-12-31 | 45,828,133 | 2025-12-31 | MATCH |
| **NVMI** | **31,780,111** | **2025-12-31** | **29,278,401** | **2024-12-31** | **MISMATCH −7.9%** |
| TSM | 25,932,524,521 | 2025-12-31 | 25,932,524,521 | 2025-12-31 | MATCH |
| NVS | 1 908 151 679 | 2025-12-31 | 1,908,151,679 | 2025-12-31 | MATCH |
| IMOS | 699,983,126 | 2025-12-31 | 699,983,126 | 2025-12-31 | MATCH |

**5 / 6 match. 1 / 6 is wrong by 7.9%.**

Sources (each is `https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<primary_doc>`):

| Name | Accession | Filed | Doc size |
|---|---|---|---|
| ASML | `0001628280-26-011378` | 2026-02-25 | 24.9 MB |
| CAMT | `0001178913-26-001561` | 2026-03-19 | — |
| NVMI | `0001178913-26-000504` | 2026-02-17 | 3.1 MB |
| TSM | `0001628280-26-025362` | 2026-04-16 | 10.4 MB |
| NVS | `0001114448-26-000004` | 2026-02-04 | 6.5 MB |
| IMOS | `0001193125-26-153743` | 2026-04-14 | 7.4 MB |

### Negative controls (they must return nothing — and they do)

| Name | What EDGAR has | Extract | Correct? |
|---|---|---|---|
| SKHY (SK hynix) | `F-1`, `F-1/A`, `DRS`, `6-K`×4, `424B4`, `F-6` — a **brand-new US listing**. companyfacts holds only `ffd` (fee data), no financials. 0 Form 4. | `[]` | **YES** — the UI message is TRUE here |
| AGNPF | no companyfacts at all | `[]` | **YES** |

> The operator's screenshot showing `— NO 10-K/10-Q` on SKHY is **one of the 4 names where the UI is
> telling the truth.** The same badge on ASML or CAMT is wrong (see §3).

---

## 2. Finding A — the AUTO gate's safety premise is structurally false for annual filers

`_shares` (`extract.py:180`) reaches `AUTO` when:

```python
latest_end is not None and len(vals) == 1 and date.fromisoformat(latest_end) >= period_end
```

The gate is justified in-code (`extract.py:175-179`) on this premise:

> *"a 10-Q cover is dated 'as of the latest practicable date' — AFTER the period end, BEFORE the filing
> date — so a current cover always passes this gate."*

That premise holds for 10-Q/10-K. **It is false for 20-F, by SEC form instruction.** The 20-F cover reads,
verbatim on every filing checked:

> "Indicate the number of outstanding shares … **as of the close of the period covered by the annual
> report.**"

So for an annual filer `latest_end` **equals** the fiscal year end, and `latest_end >= period_end` passes
**trivially, always** — while the value is as stale as the fiscal year is old. Measured staleness as of
2026-07-23: **204 days** for five of six names, **569 days** for NVMI.

**Consequence: AUTO would fire on a 204-day-old share count, pre-filled as confirm-and-go** — the lowest-
friction tier in the system, carrying the value with the least operator scrutiny. Shares → market cap →
a decision input. This is the hazard, stated concretely.

## 3. Finding B — companyfacts LAGS the filings (the NVMI case)

NVMI's latest 20-F is **report date 2025-12-31, filed 2026-02-17**, and its cover states
**31,780,111 ordinary shares**. `companyfacts` still serves **29,278,401 as of 2024-12-31** — the prior
year's 20-F.

The fresher number **exists and is readable**; companyfacts simply has not picked it up. So a
companyfacts-only implementation serves a 569-day-old count when a 204-day-old one is sitting in a
document we can already fetch, and understates market cap by **7.9%**.

**This is the finding that inverts the plan.** "Just stop discarding companyfacts" is not a safe cheap win.
Currency requires the document.

## 4. Finding C — two silent-miss landmines for document parsing (Slice 3)

Both were hit by the answer-key script itself while building this key. Both are the classic invariant #9
failure shape: a matcher that returns *nothing* rather than *wrong*, so the miss is invisible.

1. **Number formats vary.** NVS's cover renders `1 908 151 679` — **space-separated** thousands (European
   convention), not commas. A `\d{1,3}(,\d{3})+` regex silently returns no match.
2. **Tag-stripping splits words.** IMOS's cover renders `699,983,126 Comm on Shares` after tag removal —
   "Common" is broken across an HTML boundary. A keyword matcher for `"Common Shares"` silently misses.

Also relevant to Slice 3 cost/perf: these documents are **3–25 MB** (ASML 24.9 MB), materially larger than
the 10-Qs the located-passage machinery was built against.

---

## 5. What the key implies for the plan — PROPOSED, not decided

The measured findings above **invert the original slice order**. Recorded here so the reasoning is
auditable, not so it is binding.

- The originally-proposed *"Slice 1 — cheap win, emit AUTO from companyfacts"* is **withdrawn**. Findings A
  and B show it would ship a stale value at the lowest-friction tier, and an outright wrong one for NVMI.
- **Shares are reachable for all 44** (not just the 18 US-GAAP names) because `dei` is taxonomy-independent
  — a better result than first proposed. But reachable ≠ AUTO-able.
- The honest tier for a no-10-K/10-Q filer is **FLAG, carrying its explicit age**, with the value ratified
  by the operator. The `stale-cover` flag vocabulary and the `.wb-stale-shares` ">~6mo old" UI badge
  already exist (`index.css:514`) — the machinery is largely present, the gating is not.
- The IFRS map is confirmed a genuinely separate build: `_value_for_period` (`extract.py:76`), the OCF path
  (`:303`) and the one-time scan (`:331`) all **hardcode `"us-gaap"`**, so cash + revenue return nothing for
  the 26 IFRS names. Shares are unaffected (they come from `dei`).
- The FE's honest-empty surface is **already built** (`ScoredRow.tsx:136`, `FactsPanel.tsx:85`,
  `index.css:515`, from "the SIMO confusion") — but its copy, *"Nothing to extract or ratify here,"* is
  **false for 44 of 48 names**. A confidently-worded falsehood closes the question for the operator.

### Still UNKNOWN (must be measured before building, not assumed)

- Whether the **cash** and **revenue-mix** values are as reliably recoverable as shares. This key covers
  **shares only**. The same filing-vs-companyfacts check has **not** been run for cash/OCF/segment facts.
- Whether IFRS concept names map cleanly onto the existing detectors, or whether the mapping is per-filer.
- Whether the 20-F cover's share count is reliably locatable across all 44 given Finding C (the key proves
  it is locatable for 6 of 6 **only after** the format fixes).

---

## 6. The locatability probe — can the cover be read across ALL 48? (measured 2026-07-23)

§1 verified six names by hand. That proves the cover is *readable*, not that it is *reliably locatable*. So a
probe ran the same locate-and-compare over **all 48 dark names**, with the Finding-C fixes applied
(comma **and** space separators).

| Outcome | Count |
|---|---|
| Cue located, number extracted | **43** |
| No 20-F **or** 40-F on file (the honest-empty set) | 4 — OPTH, AGNPF, PBLS, SKHY |
| Cue found but no parseable number | 1 — SPRC |

Of the 43 located: **31 exactly match `companyfacts`**, 9 disagree, 3 have no companyfacts to compare.
A 9-digit exact agreement is not plausible by chance, so **the 31 agreements are strong evidence the
approach works.** Total corpus: **197 MB across 43 documents** (0.2 MB – 25.2 MB; ASML the largest).

### The 9 disagreements — and 3 of them were the PROBE'S OWN BUGS

Recorded honestly, because "my tool found 9 problems" was wrong; it found 6, and made 3.

| Name | Cause | Whose fault |
|---|---|---|
| NVMI, OGI, CURLD, HELP | **companyfacts lags the filing** — the cover is fresher | genuine (Finding B) |
| QNTM | **companyfacts carries garbage** — `dei` says **12 shares** (cover: 3,887,729) | genuine |
| CAJPY | **ADS subset mis-read as a second class** — cover says *"1,015,513,368 shares of common stock, **including 17,371,450 ADSs**"*. The second number is a SUBSET, not a class. Summing it would be badly wrong. | genuine |
| CRDL | **probe bug** — `filings_of('20-F') or filings_of('40-F')` short-circuits, so it read a **2023** 20-F while a **2025** 40-F existed. companyfacts was right. | **mine** |
| CMND, PBM | **probe bug — confidently WRONG values.** Neither filing contains the cover cue at all; the regex matched deep in the document (PBM at char **400,658**, in an EPS note: *"number of outstanding shares - basic and diluted"*) and returned a number that is not a share count. | **mine** |

### What the probe therefore imposes on the build

The CMND/PBM failures are the important ones: not "no match" but **confident wrong match**. That is the
`_locate` weakness in production form — `_locate` (`extract.py:93`) returns the first anchor found
*anywhere* in the document, and its anchor set (`"shares of Class"`, `"Class A"`, `"outstanding"`) is tuned
to a 10-Q cover. In a 25 MB 20-F it will match a footnote.

1. **Pick the latest filing across 20-F AND 40-F by report date** — never prefer one form (the CRDL bug).
2. **Bound the cover match by position** — verify the hit is the cover, not a match anywhere in 25 MB.
   A cue-miss must fail CLOSED (no value) rather than fall through to a wrong number.
3. **Parse both separators** — `1,234,567` and `1 234 567` (NVS).
4. **Tolerate tag-split words** — `clean_filing_text` (`converts.py:77`) replaces every tag with a SPACE,
   so `Comm<span>on</span>` → `Comm on`. **This is production behavior, not a probe artifact** — verified.
5. **"including N ADSs" is a subset, never a second class** (CAJPY). ADRs are common among foreign filers.
6. **Sanity-floor companyfacts** — QNTM's `dei` value of 12 must not be servable.
7. **Neither source dominates.** companyfacts sometimes lags the filing (NVMI) and is sometimes FRESHER
   than the latest annual filing (CRDL, via a 40-F newer than the 20-F). The rule must therefore be
   **"the later as-of date wins, and show both when they disagree"** — *not* "prefer the document."

## Reproducing this key

Every number above came from live EDGAR via the container. The pattern:

```
docker exec alphadeck-backend-1 python -c "<fetch submissions -> filings_of(subs,'20-F')[0] ->
  get_text(doc_url) -> locate the cover passage; compare against
  companyfacts dei:EntityCommonStockSharesOutstanding>"
```

Requires `ALPHADECK_USER_AGENT` set in the container (see `ADMIN.md`; an empty UA silently fails every
SEC pull — the 2026-07-22 incident, `FEED_LOOP.md` "Known gaps").

import { describe, expect, it } from "vitest";

import type {
  DisplaySignal,
  EpisodeOperatorOut,
  InsiderBuyOut,
  MemberDisplaySignalsOut,
  ScoredMemberOut,
  TriggerRefOut,
} from "../../api/hooks";
import { identityCells, ledgerRow, signalHeadlines } from "../ledger";
import type { OverlayEvent } from "../overlay";

// The ledger's pure formatters — the jsdom-testable strings behind the table + Cockpit strip. The numbering
// itself is overlay.test.ts's guarantee (the ledger inherits it by sharing the ONE `events` array); here we
// test the per-family row detail, the identity "—" discipline, and the present-only registry-ordered headlines.

function buy(over: Partial<InsiderBuyOut> = {}): InsiderBuyOut {
  return {
    d: "2026-01-15",
    insider_name: "Jane Doe",
    insider_role: "CEO",
    shares: 10000,
    usd: 2_100_000,
    aff_10b5_1: false,
    disclosed: "2026-07-01",
    ingested: "2026-07-01", // == disclosed -> single "disclosed" line (the two-clock default)
    character: "open_market",
    ...over,
  };
}

describe("ledgerRow — one recorded event → its table row (#N ↔ chip N, tinted by family)", () => {
  it("insider: who + fill + disclosure lag + market-price context; drops the redundant 'transacted' echo", () => {
    const e: OverlayEvent = {
      n: 2,
      family: "insider",
      date: "2026-01-15",
      closeThatDay: 217,
      pctVsNow: -0.12,
      buy: buy(),
    };
    const r = ledgerRow(e);
    expect(r.n).toBe(2);
    expect(r.cls).toBe("ov-insider");
    expect(r.type).toBe("insider buy");
    expect(r.date).toBe("2026-01-15");
    expect(r.detail).toContain("Jane Doe (CEO)");
    expect(r.detail).toContain("bought 10,000 sh @ $2.1M"); // the FILL — distinct from the market price
    expect(r.detail).toContain("disclosed 167d later (2026-07-01)"); // the honest lag (#6)
    expect(r.detail).toContain("stock $217 that day · −12% vs now");
    expect(r.detail).not.toContain("transacted"); // the date is its own column — the echo is dropped
    expect(r.detail).not.toContain("set aside"); // open_market — the norm — is unbadged (#7)
  });

  it("insider: a set-aside/self-filing character's label INHERITS into the detail cell (S2c — no ledger change)", () => {
    const row = (character: InsiderBuyOut["character"]) =>
      ledgerRow({
        n: 2,
        family: "insider",
        date: "2026-01-15",
        closeThatDay: null,
        pctVsNow: null,
        buy: buy({ character }),
      });
    // the detail derives from the tooltip, so the character lines ride in with zero ledger.ts logic
    expect(row("primary_market").detail).toContain("primary-market (offer-price, set aside)");
    expect(row("implausible").detail).toContain("implausible $ (bad source data, set aside)");
    expect(row("self_filing").detail).toContain("issuer self-filing (not a personal buy)");
    // the type cell stays "insider buy" — the grey + the detail label carry the exception
    expect(row("primary_market").type).toBe("insider buy");
  });

  const trigRow = (trigger: Partial<TriggerRefOut>, date = "2026-06-01", armDate = "2026-06-01") =>
    ledgerRow({
      n: 4,
      family: "trigger",
      date,
      armDate,
      closeThatDay: 100,
      trigger: { label: "3 insiders bought $2.1M", kind: "insider", ...trigger } as TriggerRefOut,
    } as OverlayEvent);

  it("trigger: the label leads, then kind · ticker", () => {
    const r = trigRow({ ticker: "IBM" });
    expect(r.cls).toBe("ov-trigger");
    expect(r.type).toBe("arm trigger");
    expect(r.detail).toBe("3 insiders bought $2.1M · insider · IBM");
    expect(r.links).toBeUndefined(); // no linkable source → no empty affordance on the row
  });

  it("trigger: grade, the arm linkage, and the provenance refs inherit into the detail cell (A1)", () => {
    const r = trigRow(
      {
        ticker: "IBM",
        grade: "core",
        sources: [{ source: "form4", ref: "0001-a", url: null, detail: {} }],
      },
      "2026-05-20", // fired before the arm → the linkage line is real information
      "2026-06-01",
    );
    expect(r.detail).toContain("grade core");
    expect(r.detail).toContain("→ fed the 2026-06-01 arm");
    expect(r.detail).toContain("form4: 0001-a"); // the ref rides as TEXT even without a link (#6)
  });

  it("trigger: an http(s)-resolvable source becomes a link — the ref STILL rides as text beside it", () => {
    const r = trigRow({
      sources: [
        { source: "form4", ref: "0001-a", url: "https://sec.gov/a-index.htm", detail: {} },
        { source: "fact_price_eod", ref: "close", url: null, detail: {} }, // not linkable
      ],
    });
    expect(r.links).toEqual([{ label: "form4", url: "https://sec.gov/a-index.htm" }]);
    expect(r.detail).toContain("form4: 0001-a");
    expect(r.detail).toContain("fact_price_eod: close"); // the link-less source is visible, not dropped
  });

  // A2: the operator decision row — the type cell says "operator", so the detail leads with the action
  const opRow = (over: Partial<EpisodeOperatorOut>) =>
    ledgerRow({
      n: 5,
      family: "operator",
      date: "2026-06-10",
      closeThatDay: 107,
      op: {
        action: "took",
        decision_id: "d1",
        decision_date: "2026-06-10",
        reason: null,
        thesis_level: false,
        entry_price: 12.34,
        entry_inferred: false,
        exit_price: null,
        exit_inferred: false,
        exit_date: null,
        running: false,
        operator_return: null,
        ...over,
      },
    });

  it("operator: took @ the fill, labeled logged vs inferred, with the running return (A2)", () => {
    const r = opRow({ operator_return: 0.08, running: true });
    expect(r.cls).toBe("ov-operator");
    expect(r.type).toBe("operator");
    expect(r.detail).toBe("took @ $12.34 (logged fill) · running +8.0%");
    expect(opRow({ entry_inferred: true }).detail).toBe("took @ $12.34 (close, inferred)");
  });

  it("operator: passed carries its reason verbatim when present (A2)", () => {
    expect(opRow({ action: "passed" }).detail).toBe("passed");
    expect(opRow({ action: "passed", reason: "too extended" }).detail).toBe("passed · too extended");
  });

  // A3: the tape row — the ONE family that isn't a recorded row, so its detail carries the epistemics
  const tapeRow = (signalKind: string, label: string) =>
    ledgerRow({
      n: 6,
      family: "signal",
      date: "2026-06-10",
      closeThatDay: 107,
      signalKind,
      asof: "2026-07-15",
      event: { key: "k", label, date: "2026-06-10", direction: "up" },
    });

  it("signal: the label leads, then the as-of the read was derived under + the flip caveat (A3)", () => {
    const r = tapeRow("sma_position", "golden cross: 50d crossed above 200d");
    expect(r.n).toBe(6);
    expect(r.cls).toBe("ov-signal");
    expect(r.type).toBe("tape signal");
    expect(r.date).toBe("2026-06-10");
    expect(r.detail).toBe(
      "golden cross: 50d crossed above 200d · display-only tape read · derived as-of 2026-07-15 · " +
        "most recent flip only — earlier crosses not shown",
    );
    expect(r.links).toBeUndefined(); // a computation has no filing to jump to (never an empty affordance)
  });

  it("signal: a relative-strength row carries the as-of but NOT the flip caveat (it isn't a flip)", () => {
    const r = tapeRow("relative_strength", "RS vs SPY at a 52-week high");
    expect(r.detail).toBe(
      "RS vs SPY at a 52-week high · display-only tape read · derived as-of 2026-07-15",
    );
    expect(r.detail).not.toContain("most recent flip");
  });

  it("lifecycle: the type names the specific kind; de-armed carries its reason, exit-by its gloss", () => {
    const lc = (kind: "warmed" | "armed" | "dearmed" | "exit_by", closeReason?: string): OverlayEvent => ({
      n: 1,
      family: "lifecycle",
      date: "2026-06-01",
      closeThatDay: 100,
      kind,
      closeReason,
    });
    expect(ledgerRow(lc("warmed"))).toMatchObject({ type: "warmed", cls: "ov-lifecycle", detail: "—" });
    expect(ledgerRow(lc("armed"))).toMatchObject({ type: "armed", detail: "—" });
    // A1: the de-arm reason reads as ENGLISH; an unknown token rides raw rather than vanishing
    expect(ledgerRow(lc("dearmed", "conviction_aged_out"))).toMatchObject({
      type: "de-armed",
      detail: "conviction aged out (past exit-by)",
    });
    expect(ledgerRow(lc("dearmed", "aged out"))).toMatchObject({ type: "de-armed", detail: "aged out" });
    expect(ledgerRow(lc("dearmed"))).toMatchObject({ type: "de-armed", detail: "—" }); // no reason → "—"
    expect(ledgerRow(lc("exit_by"))).toMatchObject({ type: "exit-by", detail: "signal-validity horizon" });
  });

  // Slice C: the composed dearm_detail answers the "(see de-arm day)" deferral in the row itself.
  it("de-armed: a composed dearm_detail replaces the deferral with the actual answer (Slice C)", () => {
    const r = ledgerRow({
      n: 7,
      family: "lifecycle",
      date: "2026-06-05",
      closeThatDay: 100,
      kind: "dearmed",
      closeReason: "dearmed_other",
      dearmDetail: "Structural break: closed below the 200-day base (a de-arm, not a sell)",
    });
    expect(r.detail).toBe(
      "de-armed — Structural break: closed below the 200-day base (a de-arm, not a sell)",
    );
  });

  // Slice C: the risk row — the call's own risk tape, distinct from the sell/filing FACT rows.
  it("risk: label + kind · ticker + provenance in the detail, the filing as the row's jump (#6)", () => {
    const trigger = {
      label: "2 insiders incl. senior officer sold $1,850,000 open-market (code S) across 3 txns",
      kind: "insider_sell",
      grade: null,
      event_date: "2026-06-01",
      ticker: "DEVCO",
      sources: [
        {
          source: "form4",
          ref: "0001234567-26-000321",
          url: "https://www.sec.gov/Archives/edgar/data/123/000123456726000321-index.htm",
          detail: {},
        },
      ],
    } as TriggerRefOut;
    const r = ledgerRow({ n: 4, family: "risk", date: "2026-06-01", closeThatDay: null, trigger });
    expect(r.type).toBe("risk signal");
    expect(r.cls).toBe("ov-risk");
    expect(r.detail).toContain("sold $1,850,000 open-market");
    expect(r.detail).toContain("insider_sell · DEVCO");
    expect(r.detail).toContain("form4: 0001234567-26-000321"); // the ref rides as TEXT too
    expect(r.links).toEqual([
      {
        label: "form4",
        url: "https://www.sec.gov/Archives/edgar/data/123/000123456726000321-index.htm",
      },
    ]);
  });
});

describe("identityCells — the Cockpit identity line, honest '—' for a missing field (#6)", () => {
  const scored = {
    security_id: "s1",
    business_type: "software_it",
    royalty: false,
    instrument_kind: "equity",
    sector: "Technology",
    exchange: "NYSE",
    origin: "Shanghai", // the derived where-from string, free on the scored wire (identity lifecycle)
    market_cap: { value: 8e9, provenance: [] },
  } as unknown as ScoredMemberOut;

  it("labels the business-type leaf, sector, exchange, origin, and formats the market cap", () => {
    expect(identityCells(scored)).toEqual([
      { label: "type", value: "software/IT" },
      { label: "sector", value: "Technology" },
      { label: "exchange", value: "NYSE" },
      { label: "origin", value: "Shanghai" },
      { label: "market cap", value: "$8.0B" },
    ]);
  });

  it("a null scored member → every field is '—', never a guess", () => {
    expect(identityCells(null).map((c) => c.value)).toEqual(["—", "—", "—", "—", "—"]);
  });

  it("a missing individual field reads '—' (type unclassified, origin unknown, market cap without a value)", () => {
    const partial = {
      security_id: "s1",
      business_type: null,
      sector: "Energy",
      exchange: null,
      market_cap: { value: null, provenance: [] },
    } as unknown as ScoredMemberOut;
    // origin absent → "—": the ladder's abstain surfaces as the honest unknown, never a guessed place
    expect(identityCells(partial).map((c) => c.value)).toEqual(["—", "Energy", "—", "—", "—"]);
  });

  it("appends 'priced under' ONLY when a resolved price symbol is set (honest loudness, no '—' row)", () => {
    // the healthy common case: no exception cell at all (never a "priced under —" that carries no signal)
    expect(identityCells(scored).some((c) => c.label === "priced under")).toBe(false);
    // the exception: an OTC name priced under a different vendor symbol surfaces the cell, loudly
    const otc = { ...scored, price_symbol: "FDCTD" } as unknown as ScoredMemberOut;
    expect(identityCells(otc).at(-1)).toEqual({ label: "priced under", value: "FDCTD" });
  });
});

describe("signalHeadlines — present-only, in the display registry order (#7)", () => {
  function sig(kind: string, hasHeadline: boolean): DisplaySignal {
    return {
      kind,
      label: kind,
      headline: hasHeadline
        ? { key: `${kind}.key`, label: `${kind} headline`, glyph: "up", detail: null }
        : null,
      metrics: [],
      events: [],
      basis: { source: "test", params: {} },
    } as unknown as DisplaySignal;
  }

  it("keeps only signals that carry a headline, sorted into registry order (regardless of wire order)", () => {
    const member = {
      security_id: "s1",
      ticker: "X",
      signals: [
        sig("volume_regime", true),
        sig("sma_position", true),
        sig("insider_flow_90d", false), // no headline → silence, not a blank row
        sig("range_52w", true),
      ],
    } as unknown as MemberDisplaySignalsOut;
    expect(signalHeadlines(member).map((s) => s.kind)).toEqual([
      "sma_position",
      "range_52w",
      "volume_regime",
    ]);
  });

  // A1 chore: the order mirrors backend/signals/display/__init__.py's import (= registration) order.
  it("orders the FULL registry — the four newer members sort into place, not to the tail", () => {
    const member = {
      signals: [
        "vcp",
        "etf_flow",
        "insider_flow_90d",
        "relative_strength",
        "rvol",
        "volume_regime",
        "range_52w",
        "trailing_returns",
        "sma_position",
      ].map((k) => sig(k, true)), // deliberately reversed on the wire
    } as unknown as MemberDisplaySignalsOut;
    expect(signalHeadlines(member).map((s) => s.kind)).toEqual([
      "sma_position",
      "trailing_returns",
      "range_52w",
      "volume_regime",
      "rvol",
      "relative_strength",
      "insider_flow_90d",
      "etf_flow",
      "vcp",
    ]);
  });

  it("an unregistered new kind still renders — sorted last (zero-FE-change framework)", () => {
    const member = {
      signals: [sig("brand_new", true), sig("sma_position", true)],
    } as unknown as MemberDisplaySignalsOut;
    expect(signalHeadlines(member).map((s) => s.kind)).toEqual(["sma_position", "brand_new"]);
  });

  it("a null member → no headlines", () => {
    expect(signalHeadlines(null)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------------------------------
// Slice B: the three widened families' rows — sell / 8-K filing / 13D-G stake.

describe("ledgerRow — the Slice B families", () => {
  it("sell: the buy row mirrored — who + fill + character line; the date echo drops", () => {
    const r = ledgerRow({
      n: 3,
      family: "sell",
      date: "2026-06-02",
      closeThatDay: 102,
      pctVsNow: 0.08,
      sell: {
        d: "2026-06-02",
        insider_name: "A Seller",
        insider_role: "CFO",
        shares: 2000,
        usd: 90000,
        aff_10b5_1: true,
        disclosed: "2026-06-04",
        ingested: "2026-06-04",
        character: "planned",
      },
    });
    expect(r.cls).toBe("ov-sell");
    expect(r.type).toBe("insider sell");
    expect(r.detail).toContain("A Seller (CFO)");
    expect(r.detail).toContain("sold 2,000 sh @ $90K");
    expect(r.detail).toContain("disclosed 2d later (2026-06-04)");
    expect(r.detail).toContain("10b5-1 planned sale (near-noise, screened)"); // why it didn't count (#6)
    expect(r.detail).toContain("stock $102 that day · +8% vs now");
    expect(r.detail).not.toContain("transacted"); // the date is its own column
    expect(r.links).toBeUndefined(); // no per-row filing URL on the insider wire — no empty affordance
  });

  it("filing: type = the verbatim form; detail = items + ingest lag (no 'filed' echo); links the EDGAR index (#6)", () => {
    const r = ledgerRow({
      n: 4,
      family: "filing",
      date: "2026-06-03",
      closeThatDay: 104,
      event: {
        d: "2026-06-03",
        form: "8-K",
        items: ["2.02", "9.01"],
        url: "https://www.sec.gov/Archives/edgar/data/1/acc-index.htm",
        ingested: "2026-06-08",
      },
    });
    expect(r.cls).toBe("ov-filing");
    expect(r.type).toBe("8-K");
    expect(r.detail).toContain("items 2.02, 9.01");
    expect(r.detail).toContain("ingested 5d later (2026-06-08)");
    expect(r.detail).not.toContain("filed "); // the date is its own column
    expect(r.links).toEqual([
      { label: "filing index", url: "https://www.sec.gov/Archives/edgar/data/1/acc-index.htm" },
    ]);
  });

  it("filing: null items reads 'items unresolved' — honest, never dropped (#9)", () => {
    const r = ledgerRow({
      n: 4,
      family: "filing",
      date: "2026-06-03",
      closeThatDay: null,
      event: { d: "2026-06-03", form: "8-K/A", items: null, url: "https://x.test/i.htm", ingested: "2026-06-03" },
    });
    expect(r.type).toBe("8-K/A");
    expect(r.detail).toContain("items unresolved");
  });

  it("stake: type = the verbatim form; detail = filer + pct; links the index; unresolved reads so", () => {
    const base = {
      n: 5,
      family: "activist" as const,
      date: "2026-06-04",
      closeThatDay: 106,
    };
    const resolved = ledgerRow({
      ...base,
      stake: {
        d: "2026-06-04",
        form: "SCHEDULE 13D",
        filer_name: "Engaged Capital",
        filer_cik: "0009876543",
        pct_owned: 6.2,
        url: "https://www.sec.gov/Archives/edgar/data/1/acc13d-index.htm",
        ingested: "2026-06-04",
      },
    });
    expect(resolved.cls).toBe("ov-activist");
    expect(resolved.type).toBe("SCHEDULE 13D");
    expect(resolved.detail).toContain("Engaged Capital");
    expect(resolved.detail).toContain("6.2% of class");
    expect(resolved.links).toEqual([
      { label: "filing index", url: "https://www.sec.gov/Archives/edgar/data/1/acc13d-index.htm" },
    ]);
    const unresolved = ledgerRow({
      ...base,
      stake: {
        d: "2026-06-04",
        form: "SC 13G",
        filer_name: null,
        filer_cik: null,
        pct_owned: null,
        url: "https://x.test/g.htm",
        ingested: "2026-06-04",
      },
    });
    expect(unresolved.type).toBe("SC 13G");
    expect(unresolved.detail).toContain("filer unresolved"); // #9 — said, never guessed or dropped
    expect(unresolved.detail).not.toContain("% of class");
  });
});

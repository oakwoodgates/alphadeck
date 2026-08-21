import { describe, expect, it } from "vitest";

import type {
  DisplaySignal,
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

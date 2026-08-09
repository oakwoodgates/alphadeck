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
  });

  it("trigger: the label leads, then kind · ticker", () => {
    const e: OverlayEvent = {
      n: 4,
      family: "trigger",
      date: "2026-06-01",
      closeThatDay: 100,
      trigger: { label: "3 insiders bought $2.1M", kind: "insider", ticker: "IBM" } as TriggerRefOut,
    };
    const r = ledgerRow(e);
    expect(r.cls).toBe("ov-trigger");
    expect(r.type).toBe("arm trigger");
    expect(r.detail).toBe("3 insiders bought $2.1M · insider · IBM");
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

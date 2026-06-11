import { describe, expect, it } from "vitest";

import { archLabel, formatMarketCap, meterValueLabel, provChip, provNotes } from "../format";

describe("formatMarketCap", () => {
  it("scales to B/M and renders '—' for missing (no fake $0)", () => {
    expect(formatMarketCap(8_000_000_000)).toBe("$8.0B");
    expect(formatMarketCap(600_000_000)).toBe("$600M");
    expect(formatMarketCap(null)).toBe("—");
    expect(formatMarketCap(undefined)).toBe("—");
  });
});

describe("meterValueLabel", () => {
  it("renders each meter and the meter-specific no-data cases", () => {
    expect(meterValueLabel("purity", { pips: 4, value: 77, provenance: [] })).toBe("77%");
    expect(meterValueLabel("runway", { pips: 2, value: 18, provenance: [] })).toBe("18 mo");
    // runway null = cash-generative (top pip, no months); dilution null = no convert data
    expect(meterValueLabel("runway", { pips: 4, value: null, provenance: [] })).toBe(
      "cash-generative",
    );
    expect(meterValueLabel("dilution", { pips: null, value: null, provenance: [] })).toBe(
      "no convert data",
    );
    expect(meterValueLabel("catalysts", { pips: 0, value: 0, provenance: [] })).toBe("0 live");
    expect(
      meterValueLabel("market cap", { pips: null, value: 2_500_000_000, provenance: [] }),
    ).toBe("$2.5B");
  });
});

describe("archLabel", () => {
  it("maps the enum to friendly labels", () => {
    expect(archLabel("high_beta")).toBe("high-beta");
    expect(archLabel("fund")).toBe("ETF sleeve");
    expect(archLabel("leader")).toBe("leader");
  });
});

describe("provChip", () => {
  it("links a full-URL ref and a price ref, plain otherwise", () => {
    const filing = provChip({ source: "10-q", ref: "https://www.sec.gov/x.htm", detail: {} });
    expect(filing.url).toBe("https://www.sec.gov/x.htm");
    const price = provChip({ source: "price", ref: "price:2026-06-05", detail: {} });
    expect(price.text).toBe("price · 2026-06-05");
    expect(price.url).toBeNull();
    const plain = provChip({ source: "10-q", ref: "10-Q", detail: {} });
    expect(plain.url).toBeNull(); // a non-URL, non-price ref isn't clickable
  });
});

describe("provNotes", () => {
  it("collects distinct detail.note strings (the why-lines)", () => {
    expect(
      provNotes([
        { source: "10-q", ref: "x", detail: { note: "ENTRA1 burn composition" } },
        { source: "price", ref: "price:2026-06-05", detail: {} },
      ]),
    ).toEqual(["ENTRA1 burn composition"]);
  });
});

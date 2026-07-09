import { describe, expect, it } from "vitest";

import {
  archLabel,
  formatMarketCap,
  isAcronymTerm,
  memberHasFundamentals,
  meterValueLabel,
  provChip,
  provNotes,
} from "../format";

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

describe("meterValueLabel — market cap with one input on file (the gate-3 'no save?' fix)", () => {
  const prov = (source: string) => ({ source, ref: "r", url: null, detail: {} });
  it("names the missing half instead of a bare '—'", () => {
    // a ratified shares fact, no price bars yet (the awaiting-note rides as source "computed")
    expect(
      meterValueLabel("market cap", {
        pips: null,
        value: null,
        provenance: [prov("10-q-cover"), prov("computed")],
      }),
    ).toBe("shares on file · needs price");
    // price bars, no ratified shares yet
    expect(
      meterValueLabel("market cap", { pips: null, value: null, provenance: [prov("price")] }),
    ).toBe("price on file · needs shares");
  });
  it("stays '—' with nothing on file, and formats normally with a value", () => {
    expect(meterValueLabel("market cap", { pips: null, value: null, provenance: [] })).toBe("—");
    expect(
      meterValueLabel("market cap", { pips: null, value: 2_500_000_000, provenance: [] }),
    ).toBe("$2.5B");
  });
});

describe("memberHasFundamentals — a ratified shares fact counts before a price exists", () => {
  const fig = (value: number | null = null, provenance: { source: string }[] = []) => ({
    pips: null,
    value,
    provenance,
  });
  it("counts the shares-on-file member (the funnel must move on a confirm)", () => {
    const m = {
      purity: fig(),
      runway: fig(),
      market_cap: fig(null, [{ source: "10-q-cover" }, { source: "computed" }]),
    };
    expect(memberHasFundamentals(m)).toBe(true);
  });
  it("price bars alone are NOT operator-confirmed data", () => {
    const m = { purity: fig(), runway: fig(), market_cap: fig(null, [{ source: "price" }]) };
    expect(memberHasFundamentals(m)).toBe(false);
  });
});

describe("archLabel", () => {
  it("maps the enum to friendly labels", () => {
    expect(archLabel("high_beta")).toBe("high-beta");
    expect(archLabel("fund")).toBe("ETF sleeve");
    expect(archLabel("leader")).toBe("leader");
  });
});

describe("isAcronymTerm (G — the collision-lens predicate)", () => {
  it("marks single all-caps tokens as collision-prone", () => {
    expect(isAcronymTerm("HBM")).toBe(true);
    expect(isAcronymTerm("DRAM")).toBe(true);
    expect(isAcronymTerm("H100")).toBe(true); // digits allowed after a leading letter
    expect(isAcronymTerm(" HBM ")).toBe(true); // authored whitespace tolerated
    // NAND is a real word AND all-caps — it clusters BY DESIGN (the v1 rule is deliberately simple;
    // judged on live data, tweaked after).
    expect(isAcronymTerm("NAND")).toBe(true);
  });
  it("never marks phrases, mixed case, or single letters", () => {
    expect(isAcronymTerm("high-bandwidth memory")).toBe(false); // spelled-out phrase
    expect(isAcronymTerm("NAND flash")).toBe(false); // two words
    expect(isAcronymTerm("Hbm")).toBe(false); // mixed case
    expect(isAcronymTerm("psilocybin")).toBe(false); // ordinary word
    expect(isAcronymTerm("A")).toBe(false); // one letter is not an acronym
    expect(isAcronymTerm("3PAR")).toBe(false); // must start with a letter (v1)
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

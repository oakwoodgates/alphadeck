import { describe, expect, it } from "vitest";

import {
  archLabel,
  formatMarketCap,
  isAcronymTerm,
  memberHasFundamentals,
  meterValueLabel,
  onFileValues,
  provChip,
  provNotes,
  sharesAsof,
  staleSharesMonths,
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

describe("onFileValues", () => {
  it("recovers the ratified values per fact type from the meters' provenance detail", () => {
    const map = onFileValues({
      purity: {
        pips: 3,
        value: 77,
        provenance: [
          { source: "10-k-segment", ref: "K", detail: { mix_pct: 77, segment_label: "reactors", note: "basis" } },
        ],
      },
      runway: {
        pips: 4,
        value: 60,
        provenance: [{ source: "10-q", ref: "Q", detail: { cash_usd: 1e9, quarterly_burn_usd: 5e7 } }],
      },
      market_cap: {
        pips: null,
        value: 2.5e9,
        provenance: [
          { source: "10-q-cover", ref: "SH", detail: { shares: 1e8 } },
          { source: "price", ref: "price:2026-06-01", detail: { close: 25 } },
        ],
      },
    });
    expect(map.revenue_mix).toEqual({ mix_pct: 77, segment_label: "reactors", note: "basis" });
    expect(map.shares_outstanding).toEqual({ shares: 1e8, note: undefined });
    expect(map.cash_burn).toEqual({ cash_usd: 1e9, quarterly_burn_usd: 5e7, note: undefined });
  });

  it("absent facts stay absent; price/computed provenance never counts as on file", () => {
    const map = onFileValues({
      purity: { pips: null, value: null, provenance: [] },
      runway: { pips: null, value: null, provenance: [] },
      market_cap: {
        pips: null,
        value: null,
        provenance: [{ source: "price", ref: "price:2026-06-01", detail: { close: 25 } }],
      },
    });
    expect(map.revenue_mix).toBeUndefined();
    expect(map.shares_outstanding).toBeUndefined(); // the price leg alone is NOT an operator fact
    expect(map.cash_burn).toBeUndefined();
  });

  it("a pre-threading fact (no figures in detail) still reads as on file, values undefined", () => {
    const map = onFileValues({
      purity: { pips: 3, value: 77, provenance: [{ source: "10-k-segment", ref: "K", detail: {} }] },
      runway: { pips: 4, value: 60, provenance: [{ source: "10-q", ref: "Q", detail: {} }] },
      market_cap: { pips: null, value: null, provenance: [] },
    });
    expect(map.revenue_mix?.mix_pct).toBe(77); // the figure's own value backstops purity
    expect(map.cash_burn).toEqual({ cash_usd: undefined, quarterly_burn_usd: undefined, note: undefined });
  });
});

describe("stale-shares age (the ENDV finding — display-only)", () => {
  const capWith = (shares_asof?: string) => ({
    market_cap: {
      pips: null,
      value: 1e6,
      provenance: [
        { source: "price", ref: "price:2026-07-01", detail: { close: 10 } },
        { source: "10-q-cover", ref: "Q", detail: shares_asof ? { shares: 1e6, shares_asof } : { shares: 1e6 } },
      ],
    } as never,
  });

  it("sharesAsof reads the cover date off the shares provenance (not the price leg)", () => {
    expect(sharesAsof(capWith("2023-12-28"))).toBe("2023-12-28");
    expect(sharesAsof(capWith(undefined))).toBeUndefined(); // pre-threading fact: no date
  });

  it("flags a >6-month-old count with its rounded age, stays silent for a current one", () => {
    // ENDV: a 2023 cover against a 2026 as-of → loudly stale
    expect(staleSharesMonths("2023-12-28", "2026-07-17")).toBe(31);
    // a current Q1 cover (~2.6 months) → null (honest loudness: the common case shows nothing)
    expect(staleSharesMonths("2026-04-29", "2026-07-17")).toBeNull();
    // ~5 months old is still under the ~6-month threshold → quiet
    expect(staleSharesMonths("2026-02-20", "2026-07-17")).toBeNull();
    // ~6.5 months old is past it → lights (a company on schedule files a 10-Q every quarter)
    expect(staleSharesMonths("2026-01-01", "2026-07-17")).toBe(6);
  });

  it("is null (never throws) on a missing or unparseable date", () => {
    expect(staleSharesMonths(undefined, "2026-07-17")).toBeNull();
    expect(staleSharesMonths("not-a-date", "2026-07-17")).toBeNull();
  });
});

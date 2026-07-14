import { describe, expect, it } from "vitest";

import {
  matchesAnyJunkTell,
  nameTokensCooccur,
  signalAcronymTermsFrom,
  soleAcronymSignalMatch,
  tokenize,
  type JunkTellContext,
} from "../junkTells";

const ctx = (over: Partial<JunkTellContext> = {}): JunkTellContext => ({
  matchedTerms: [],
  companyName: "",
  signalAcronymTerms: new Set(["hbm"]),
  ...over,
});

describe("tokenize", () => {
  it("splits on non-alphanumeric and lowercases", () => {
    expect(tokenize("BlackRock Multi-Asset Income Trust")).toEqual(
      new Set(["blackrock", "multi", "asset", "income", "trust"]),
    );
  });
});

describe("signalAcronymTermsFrom", () => {
  it("collects SIGNAL-tier acronym terms only", () => {
    const terms = signalAcronymTermsFrom([
      { term: "HBM", tier: "signal" },
      { term: "memory", tier: "broad" },
      { term: "high-bandwidth memory", tier: "signal" },
    ]);
    expect(terms).toEqual(new Set(["hbm"]));
  });
});

describe("soleAcronymSignalMatch", () => {
  it("fires on a sole SIGNAL acronym match", () => {
    expect(soleAcronymSignalMatch(ctx({ matchedTerms: ["HBM"] }))).toBe(true);
  });
  it("does not fire with a corroborating second term", () => {
    expect(soleAcronymSignalMatch(ctx({ matchedTerms: ["HBM", "memory"] }))).toBe(false);
  });
  it("does not fire when the sole match is not in the acronym term set", () => {
    expect(soleAcronymSignalMatch(ctx({ matchedTerms: ["memory"] }))).toBe(false);
  });
});

describe("nameTokensCooccur", () => {
  const blackRockTrust = nameTokensCooccur(["blackrock", "trust"]);

  it("fires when both tokens appear in the company name", () => {
    expect(
      blackRockTrust(ctx({ companyName: "BlackRock Multi-Asset Income Trust" })),
    ).toBe(true);
  });
  it("does not fire on bare BlackRock", () => {
    expect(blackRockTrust(ctx({ companyName: "BlackRock Inc" }))).toBe(false);
  });
  it("does not fire when company name is missing", () => {
    expect(blackRockTrust(ctx({ companyName: "" }))).toBe(false);
  });
  it("fires for Royce + Trust", () => {
    const royceTrust = nameTokensCooccur(["royce", "trust"]);
    expect(royceTrust(ctx({ companyName: "Royce Value Trust, Inc." }))).toBe(true);
  });
});

describe("matchesAnyJunkTell", () => {
  it("ORs across the registry", () => {
    expect(matchesAnyJunkTell(ctx({ matchedTerms: ["HBM"] }))).toBe(true);
    expect(
      matchesAnyJunkTell(ctx({ companyName: "BlackRock Global Equity Trust" })),
    ).toBe(true);
    expect(matchesAnyJunkTell(ctx())).toBe(false);
  });
});
